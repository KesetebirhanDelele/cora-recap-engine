"""Weekly read of the outbound attempt cap (spec/54): capped group vs the 10% holdout, plus the "switch it off" check.

READ-ONLY. Prints counts only (no phone numbers, names or transcript text). Safe to rerun.

  docker compose exec -T worker-quality python -m execution.attempt_cap_weekly_report
  docker compose exec -T worker-quality python -m execution.attempt_cap_weekly_report --cap-live-at 2026-11-22T17:10:00+00:00

Who is compared: the people who were AT the cap on go-live (lifetime AI attempts >= cap AND >= guarantee attempts since the
AI class started). The cap blocks them unless they are in the uncapped holdout, so the two groups differ only in that.
Because the groups differ in size, results are shown per 1,000 people.

Definitions (from call_events, outbound ColdLead/NewLead, report_excluded false):
  call      = one row.  reached = status 'completed' (a person picked up; voicemail, no-answer, busy and failed are not).
  booking   = a call with detected_intent 'booked' (bookings are only recorded from April 2026).
Callbacks the lead asked for are exempt from the cap, so a few calls in the capped group are expected and are not a leak.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.core.attempt_cap import AI_AGENTS, decide, in_holdout

DEFAULT_CAP_LIVE_AT = "2026-11-22T17:10:00+00:00"   # worker container rebuilt 17:06 UTC; 87bfbe3 deployed
# Proposed threshold, to be confirmed by Kes: flag when bookings fall this many points MORE than calls do.
REVIEW_POINTS = 30.0
MIN_POST_WEEKS = 2
BASELINE_WEEKS = 4


@dataclass(frozen=True)
class Call:
    phone: str          # last 10 digits
    at: datetime
    reached: bool
    booked: bool
    minutes: float


def to_phone10(contact_id: str | None) -> str | None:
    digits = re.sub(r"\D", "", contact_id or "")
    return digits[-10:] if len(digits) >= 10 else None


def classify_cohort(calls: Iterable[Call], *, cutoff: datetime, era: datetime, cap: int, guarantee: int,
                    holdout_percent: int) -> dict[str, str]:
    """phone -> 'holdout' | 'capped' for everyone who was at the cap at `cutoff`."""
    lifetime: dict[str, int] = {}
    since_era: dict[str, int] = {}
    for c in calls:
        if c.at < cutoff:
            lifetime[c.phone] = lifetime.get(c.phone, 0) + 1
            if c.at >= era:
                since_era[c.phone] = since_era.get(c.phone, 0) + 1
    out: dict[str, str] = {}
    for phone, n in lifetime.items():
        if decide(n, since_era.get(phone, 0), cap=cap, guarantee=guarantee, holdout=False).blocked:
            out[phone] = "holdout" if in_holdout(phone, holdout_percent) else "capped"
    return out


def _zero() -> dict[str, float]:
    return {"calls": 0, "reached": 0, "booked": 0, "minutes": 0.0}


def _add(bucket: dict[str, float], c: Call) -> None:
    bucket["calls"] += 1
    bucket["reached"] += 1 if c.reached else 0
    bucket["booked"] += 1 if c.booked else 0
    bucket["minutes"] += c.minutes


def build(calls: list[Call], *, cutoff: datetime, now: datetime, era: datetime, cap: int, guarantee: int,
          holdout_percent: int) -> dict:
    cohort = classify_cohort(calls, cutoff=cutoff, era=era, cap=cap, guarantee=guarantee, holdout_percent=holdout_percent)
    people = {"capped": sum(1 for g in cohort.values() if g == "capped"),
              "holdout": sum(1 for g in cohort.values() if g == "holdout")}
    n_weeks = max(0, (now - cutoff).days // 7 + (1 if (now - cutoff) % timedelta(days=7) else 0))
    weeks = []
    for k in range(n_weeks):
        start, end = cutoff + timedelta(days=7 * k), cutoff + timedelta(days=7 * (k + 1))
        weeks.append({"start": start, "end": end, "complete": end <= now,
                      "capped": _zero(), "holdout": _zero(), "all": _zero()})
    base_start = cutoff - timedelta(days=7 * BASELINE_WEEKS)
    baseline = _zero()
    for c in calls:
        if base_start <= c.at < cutoff:
            _add(baseline, c)
        elif c.at >= cutoff:
            k = (c.at - cutoff).days // 7
            if k < len(weeks):
                _add(weeks[k]["all"], c)
                group = cohort.get(c.phone)
                if group:
                    _add(weeks[k][group], c)
    done = [w for w in weeks if w["complete"]]
    return {"cohort_people": people, "weeks": weeks, "baseline_weekly": {k: v / BASELINE_WEEKS for k, v in baseline.items()},
            "fallback": fallback_check(baseline, done)}


def fallback_check(baseline: dict[str, float], complete_weeks: list[dict]) -> dict:
    """The commitment to Ali: if total bookings fall clearly MORE than total calls do, switch the cap off."""
    if len(complete_weeks) < MIN_POST_WEEKS:
        return {"status": "TOO EARLY", "reason": f"needs {MIN_POST_WEEKS} complete weeks after go-live, has {len(complete_weeks)}"}
    n = len(complete_weeks)
    post = {k: sum(w["all"][k] for w in complete_weeks) / n for k in ("calls", "booked", "reached")}
    base = {k: baseline[k] / BASELINE_WEEKS for k in ("calls", "booked", "reached")}
    if base["calls"] <= 0 or base["booked"] <= 0:
        return {"status": "NO BASELINE", "reason": "no calls or no bookings in the 4 weeks before go-live"}
    calls_change = (post["calls"] / base["calls"] - 1) * 100
    booked_change = (post["booked"] / base["booked"] - 1) * 100
    reached_change = (post["reached"] / base["reached"] - 1) * 100 if base["reached"] > 0 else float("nan")
    flag = round(booked_change - calls_change, 6) < -REVIEW_POINTS
    return {"status": "REVIEW: bookings fell clearly more than calls" if flag else "OK",
            "calls_change_pct": calls_change, "booked_change_pct": booked_change, "reached_change_pct": reached_change,
            "post_weekly": post, "baseline_weekly": base, "threshold_points": REVIEW_POINTS}


def per_1000(bucket: dict[str, float], people: int) -> dict[str, float]:
    return {k: (v / people * 1000 if people else 0.0) for k, v in bucket.items()}


def render(rep: dict, *, cutoff: datetime, now: datetime) -> str:
    p = rep["cohort_people"]
    out = [f"Attempt cap weekly read | go-live {cutoff:%Y-%m-%d %H:%M} UTC | as of {now:%Y-%m-%d %H:%M} UTC",
           f"People at the cap on go-live: capped {p['capped']:,}, holdout {p['holdout']:,}", ""]
    if not rep["weeks"]:
        out.append("No weeks yet.")
    for i, w in enumerate(rep["weeks"], 1):
        tag = "" if w["complete"] else "  (PARTIAL WEEK, do not read)"
        out.append(f"Week {i}: {w['start']:%b %d} to {w['end']:%b %d}{tag}")
        out.append(f"  {'group':<9}{'people':>8}{'calls':>8}{'reached':>9}{'booked':>8}{'calls/1k':>10}{'reached/1k':>12}{'booked/1k':>11}")
        for g in ("capped", "holdout"):
            b, n = w[g], p[g]
            r = per_1000(b, n)
            out.append(f"  {g:<9}{n:>8,}{int(b['calls']):>8,}{int(b['reached']):>9,}{int(b['booked']):>8,}"
                       f"{r['calls']:>10.1f}{r['reached']:>12.1f}{r['booked']:>11.2f}")
        a = w["all"]
        out.append(f"  all calls this week: {int(a['calls']):,} calls, {int(a['reached']):,} reached, "
                   f"{int(a['booked']):,} booked, {a['minutes']:,.0f} minutes")
        out.append("")
    b = rep["baseline_weekly"]
    out.append(f"Baseline (4 weeks before go-live, weekly average): {b['calls']:,.0f} calls, {b['reached']:,.0f} reached, "
               f"{b['booked']:,.1f} booked, {b['minutes']:,.0f} minutes")
    fb = rep["fallback"]
    out.append("")
    out.append(f"Switch-off check: {fb['status']}")
    if "calls_change_pct" in fb:
        out.append(f"  calls {fb['calls_change_pct']:+.0f}%, reached {fb['reached_change_pct']:+.0f}%, bookings {fb['booked_change_pct']:+.0f}% "
                   f"vs the 4-week baseline (flag if bookings are {fb['threshold_points']:.0f}+ points below calls)")
        out.append(f"  weekly averages now: {fb['post_weekly']['calls']:,.0f} calls, {fb['post_weekly']['booked']:,.1f} booked "
                   "(bookings are few per week, so one week is noisy)")
    elif "reason" in fb:
        out.append(f"  {fb['reason']}")
    out += ["", "Notes: capped-group calls should be near zero (callbacks the lead asked for are exempt). The holdout is the "
            "long-term comparison: read bookings at 6 and 12 months. Until then steer on reached calls per 1,000 people."]
    return "\n".join(out)


_SQL = """
SELECT contact_id, COALESCE(call_started_at, created_at) AS at, status, detected_intent, duration_seconds
FROM call_events
WHERE LOWER(direction) = 'outbound' AND voice_agent IN :agents AND report_excluded = false
  AND contact_id IS NOT NULL AND COALESCE(call_started_at, created_at) >= :since
"""


def load_calls(session, since: datetime) -> list[Call]:
    from sqlalchemy import bindparam, text
    stmt = text(_SQL).bindparams(bindparam("agents", expanding=True))
    calls: list[Call] = []
    for cid, at, status, intent, dur in session.execute(stmt, {"agents": list(AI_AGENTS), "since": since}):
        phone = to_phone10(cid)
        if not phone:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        calls.append(Call(phone, at, (status or "").lower() == "completed", (intent or "") == "booked",
                          (dur or 0) / 60.0))
    return calls


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-live-at", default=DEFAULT_CAP_LIVE_AT, help="ISO time the cap went live (UTC)")
    a = ap.parse_args(argv)
    from app.config import get_settings
    from app.db import get_sync_session
    s = get_settings()
    cutoff = datetime.fromisoformat(a.cap_live_at)
    era = datetime.fromisoformat(s.outbound_attempt_cap_era_start).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    with get_sync_session() as session:
        calls = load_calls(session, datetime(2025, 1, 1, tzinfo=timezone.utc))   # lifetime count needs all history
    rep = build(calls, cutoff=cutoff, now=now, era=era, cap=s.outbound_attempt_cap,
                guarantee=s.outbound_attempt_cap_era_guarantee, holdout_percent=s.outbound_attempt_cap_holdout_percent)
    print(render(rep, cutoff=cutoff, now=now))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
