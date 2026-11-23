"""Weekly read of the outbound attempt cap (spec/54): capped group vs the 10% holdout, plus the "switch it off" check.

READ-ONLY. Prints counts only (no phone numbers, names or transcript text). Safe to rerun.

  docker compose exec -T worker-quality python -m execution.attempt_cap_weekly_report
  docker compose exec -T worker-quality python -m execution.attempt_cap_weekly_report --cap-live-at 2026-11-22T17:10:00+00:00

Who is compared: the people who were AT the cap on go-live (lifetime AI attempts >= cap AND >= guarantee attempts since the
AI class started). The cap blocks them unless they are in the uncapped holdout, so the two groups differ only in that.
Because the groups differ in size, results are shown per 1,000 people.

Definitions (from call_events, outbound ColdLead/NewLead, report_excluded false):
  call      = one row.  reached = status 'completed' (a person picked up; voicemail, no-answer, busy and failed are not).
  connected = the call got past ringing: 'completed' or 'hangup_on_voicemail' (failed, no-answer and busy are not).
Week 1 is the first week (counted from go-live) in which any call connected. Until one does, the report says so and
compares nothing (Ali, 2026-11-23: do not compare two zeros).
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
CONNECTED_STATUSES = ("completed", "hangup_on_voicemail")   # got past ringing; failed, no-answer, busy did not
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
    connected: bool = False


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
    return {"calls": 0, "connected": 0, "reached": 0, "booked": 0, "minutes": 0.0}


def _add(bucket: dict[str, float], c: Call) -> None:
    bucket["calls"] += 1
    bucket["connected"] += 1 if c.connected else 0
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
    attempted = sum(w["all"]["calls"] for w in weeks)
    first = next((i for i, w in enumerate(weeks) if w["all"]["connected"] > 0), None)
    weeks = weeks[first:] if first is not None else []
    done = [w for w in weeks if w["complete"]]
    return {"cohort_people": people, "weeks": weeks, "attempted_since_go_live": int(attempted),
            "first_connected_week": first, "baseline_weekly": {k: v / BASELINE_WEEKS for k, v in baseline.items()},
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
            "post_weekly": post, "baseline_weekly": base, "threshold_points": REVIEW_POINTS,
            "week_booked": [int(w["all"]["booked"]) for w in complete_weeks]}


def per_1000(bucket: dict[str, float], people: int) -> dict[str, float]:
    return {k: (v / people * 1000 if people else 0.0) for k, v in bucket.items()}


def render(rep: dict, *, cutoff: datetime, now: datetime) -> str:
    p = rep["cohort_people"]
    out = [f"Attempt cap weekly read | go-live {cutoff:%Y-%m-%d %H:%M} UTC | as of {now:%Y-%m-%d %H:%M} UTC",
           f"People at the cap on go-live: capped {p['capped']:,}, holdout {p['holdout']:,}", ""]
    if not rep["weeks"]:
        out.append(f"NO CONNECTED CALLS since go-live ({rep['attempted_since_go_live']:,} attempted, none got past ringing). "
                   "Nothing to compare yet; week 1 starts the first week a call connects.")
        out.append("")
        out.append("Switch-off check: WAITING FOR CONNECTED CALLS (clock has not started)")
        return "\n".join(out)
    for i, w in enumerate(rep["weeks"], 1):
        tag = "" if w["complete"] else "  (PARTIAL WEEK, do not read)"
        out.append(f"Week {i}: {w['start']:%b %d} to {w['end']:%b %d}{tag}")
        out.append(f"  {'group':<9}{'people':>8}{'calls':>8}{'connected':>11}{'reached':>9}{'booked':>8}{'calls/1k':>10}{'reached/1k':>12}{'booked/1k':>11}")
        for g in ("capped", "holdout"):
            b, n = w[g], p[g]
            r = per_1000(b, n)
            out.append(f"  {g:<9}{n:>8,}{int(b['calls']):>8,}{int(b['connected']):>11,}{int(b['reached']):>9,}{int(b['booked']):>8,}"
                       f"{r['calls']:>10.1f}{r['reached']:>12.1f}{r['booked']:>11.2f}")
        a = w["all"]
        out.append(f"  all calls this week: {int(a['calls']):,} calls, {int(a['connected']):,} connected, {int(a['reached']):,} reached, "
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
        out.append(f"  bookings behind it: {fb['baseline_weekly']['booked']:.1f} a week before go-live; "
                   f"since go-live, by week: {', '.join(str(n) for n in fb['week_booked'])}")
    elif "reason" in fb:
        out.append(f"  {fb['reason']}")
    out += ["", "Notes: capped-group calls should be near zero (callbacks the lead asked for are exempt). The holdout is the "
            "long-term comparison: read bookings at 6 and 12 months. Until then steer on reached calls per 1,000 people."]
    return "\n".join(out)


# -- The Edu: Cory - Outbound Synthflow agent (Ali, 2026-11-23): its own line, NOT part of the cap comparison ----------
EDU_CORY_MODEL_ID = "f013fd7e-94de-446b-a755-192fdb3ccc5e"
EDU_CORY_LABEL = "Edu: Cory - Outbound (Synthflow agent, number ending 2568)"
SYNTHFLOW_CALLS_URL = "https://api.synthflow.ai/v2/calls"
CONNECTED_STATUSES_EXT = ("completed", "hangup_on_voicemail")


@dataclass(frozen=True)
class ExtCall:
    at: datetime
    status: str


def summarize_agent(calls: Iterable[ExtCall], *, cutoff: datetime, now: datetime) -> dict:
    """Weekly attempts/connected/answered for one outside agent from go-live, plus its last connected call overall.
    Weeks are the same calendar weeks as the cap report, so the lines sit side by side."""
    calls = list(calls)
    n_weeks = max(0, (now - cutoff).days // 7 + (1 if (now - cutoff) % timedelta(days=7) else 0))
    weeks = [{"start": cutoff + timedelta(days=7 * k), "end": cutoff + timedelta(days=7 * (k + 1)),
              "attempts": 0, "connected": 0, "answered": 0, "failed": 0} for k in range(n_weeks)]
    for c in calls:
        if c.at < cutoff:
            continue
        k = (c.at - cutoff).days // 7
        if k >= len(weeks):
            continue
        st = c.status.lower()
        weeks[k]["attempts"] += 1
        weeks[k]["connected"] += 1 if st in CONNECTED_STATUSES_EXT else 0
        weeks[k]["answered"] += 1 if st == "completed" else 0
        weeks[k]["failed"] += 1 if st == "failed" else 0
    connected_at = sorted(c.at for c in calls if c.status.lower() in CONNECTED_STATUSES_EXT)
    since = [t for t in connected_at if t >= cutoff]
    return {"weeks": weeks, "last_connected_ever": connected_at[-1] if connected_at else None,
            "first_connected_since_go_live": since[0] if since else None,
            "attempts_since_go_live": sum(w["attempts"] for w in weeks)}


def render_agent(summary: dict | None, *, error: str | None = None) -> str:
    out = ["", f"Separate line, not in the cap comparison: {EDU_CORY_LABEL}"]
    if error or summary is None:
        return chr(10).join(out + [f"  could not read Synthflow ({error or 'no data'}); rerun later"])
    for i, w in enumerate(summary["weeks"], 1):
        out.append(f"  Week {i} ({w['start']:%b %d} to {w['end']:%b %d}): {w['attempts']} attempts, "
                   f"{w['connected']} connected, {w['answered']} answered by a person, {w['failed']} failed")
    if not summary["weeks"]:
        out.append("  no weeks yet")
    first, last = summary["first_connected_since_go_live"], summary["last_connected_ever"]
    if first:
        out.append(f"  FIRST CONNECTED CALL since go-live: {first:%Y-%m-%d %H:%M} UTC (its fix has landed)")
    else:
        lc = f"{last:%Y-%m-%d}" if last else "never"
        out.append(f"  NO CONNECTED CALLS since go-live ({summary['attempts_since_go_live']} attempted). "
                   f"Last connected call ever: {lc}. The day this stops being zero, its fix has landed.")
    return chr(10).join(out)


def load_agent_calls(model_id: str, api_key: str, *, http=None, max_pages: int = 30) -> list[ExtCall]:
    """READ-ONLY: GET /v2/calls pages for one agent. Raises on HTTP errors (the caller reports it, the report goes on)."""
    import httpx
    client = http or httpx.Client(timeout=30)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    out: list[ExtCall] = []
    for page in range(max_pages):
        resp = client.get(SYNTHFLOW_CALLS_URL, headers=headers,
                          params={"model_id": model_id, "limit": 100, "offset": page * 100})
        resp.raise_for_status()
        rows = (resp.json().get("response") or {}).get("calls") or []
        if not rows:
            break
        for r in rows:
            t = r.get("start_time")
            if t is None:
                continue
            try:
                at = datetime.fromtimestamp(float(t) / 1000, timezone.utc)
            except (TypeError, ValueError):
                at = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            out.append(ExtCall(at, str(r.get("call_status") or r.get("status") or "")))
    return out


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
        st = (status or "").lower()
        calls.append(Call(phone, at, st == "completed", (intent or "") == "booked", (dur or 0) / 60.0,
                          connected=st in CONNECTED_STATUSES))
    return calls


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap-live-at", default=DEFAULT_CAP_LIVE_AT, help="ISO time the cap went live (UTC)")
    ap.add_argument("--edu-cory-model-id", default=EDU_CORY_MODEL_ID, help="Synthflow agent id for the separate Edu: Cory line")
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
    text = render(rep, cutoff=cutoff, now=now)
    try:
        key = s.synthflow_api_key
        key = key.get_secret_value() if hasattr(key, "get_secret_value") else key
        text += chr(10) + render_agent(summarize_agent(load_agent_calls(a.edu_cory_model_id, key), cutoff=cutoff, now=now))
    except Exception as exc:   # the report must never fail because the outside agent could not be read
        text += chr(10) + render_agent(None, error=type(exc).__name__)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
