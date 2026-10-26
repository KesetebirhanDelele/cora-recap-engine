"""
Sign-up intake trend (spec/40): how many people sign up through the Refactored platform, and how many of them have an email and a phone.

The sign-ups are GHL contacts written by the Refactored platform's sync (source "api v1"). They are not Cora leads, so Cora's own tables
cannot answer "how many today"; GHL can, but paging it on every dashboard load is slow, so an hourly worker job summarises GHL into two
small tables (migration 0030) and the page reads those.

  segment "explorer"   : tag src-free_signup, or source "api v1" without an Open House tag (covers the early contacts created before
                         tagging went live)
  segment "open_house" : tag src-open_house / brand-open_house (Open House RSVPs; the only ones with phone numbers so far, and the ones
                         Cora's New Lead flow calls)

Days are America/Chicago calendar days. Every hour the last REFRESH_DAYS days are rebuilt from GHL (the first run fills the history since
HISTORY_START), then a cumulative snapshot row per segment is stored. Read-only on GHL; the only writes are the two summary tables.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/Chicago")
HISTORY_START = datetime(2026, 10, 21, 0, 0, tzinfo=TZ)          # first sign-up reached GHL on 2026-10-23
REFRESH_DAYS = 7
PAGE = 100
MAX_PAGES = 300                                                    # 30,000 contacts in one window; beyond that fail loudly, never undercount
SEGMENTS = ("explorer", "open_house")
SIGNUP_SOURCE = "api v1"
_COUNTS = ("signups", "with_email", "with_phone", "with_both", "with_campaign", "utm_fb", "utm_ig")


# ── pure ──────────────────────────────────────────────────────────────────────────────────────────

def classify(contact: dict[str, Any]) -> str | None:
    """'explorer', 'open_house', or None when the contact is not a Refactored sign-up."""
    tags = {str(t).lower() for t in (contact.get("tags") or [])}
    source = str(contact.get("source") or "").strip().lower()
    if "src-open_house" in tags or "brand-open_house" in tags:
        return "open_house"
    if "src-free_signup" in tags or source == SIGNUP_SOURCE:
        return "explorer"
    return None


def local_day(contact: dict[str, Any]) -> date | None:
    raw = contact.get("dateAdded")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(TZ).date()
    except ValueError:
        return None


def summarize(contacts: list[dict[str, Any]]) -> dict[tuple[date, str], dict[str, int]]:
    """(Central day, segment) -> counts. Contacts that are not sign-ups, or have no usable created date, are skipped."""
    out: dict[tuple[date, str], dict[str, int]] = {}
    for c in contacts:
        seg, day = classify(c), local_day(c)
        if seg is None or day is None:
            continue
        row = out.setdefault((day, seg), dict.fromkeys(_COUNTS, 0))
        email, phone = bool((c.get("email") or "").strip()), bool((c.get("phone") or "").strip())
        tags = {str(t).lower() for t in (c.get("tags") or [])}
        row["signups"] += 1
        row["with_email"] += email
        row["with_phone"] += phone
        row["with_both"] += email and phone
        row["with_campaign"] += any(t.startswith("camp-") for t in tags)
        row["utm_fb"] += "utm-fb" in tags
        row["utm_ig"] += "utm-ig" in tags
    return out


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


# ── GHL → tables ───────────────────────────────────────────────────────────────────────────────────

def fetch_contacts(ghl: Any, since: datetime, until: datetime) -> list[dict[str, Any]]:
    """Every contact GHL created in [since, until], paged. Raises if the window is too large to page completely."""
    def ms(d: datetime) -> int:
        return int(d.timestamp() * 1000)

    out: list[dict[str, Any]] = []
    after = None
    for _ in range(MAX_PAGES):
        r = ghl.search_contacts([{"field": "dateAdded", "operator": "range", "value": {"gte": ms(since), "lte": ms(until)}}],
                                page_limit=PAGE, search_after=after)
        page = r.get("contacts", [])
        out.extend(page)
        if len(page) < PAGE:
            return out
        after = page[-1].get("searchAfter")
    raise RuntimeError(f"signup_intake: more than {MAX_PAGES * PAGE} contacts in one window - refusing to write a partial count")


def run_sync(session: Session, settings: Any = None, now: datetime | None = None, ghl: Any = None) -> dict[str, Any]:
    """Rebuild the recent days from GHL, then store a cumulative snapshot. The GHL read happens BEFORE any write, so a failed or
    partial read leaves the tables exactly as they were. The caller commits."""
    now = now or datetime.now(tz=timezone.utc)
    if ghl is None:
        from app.adapters.ghl import GHLClient
        ghl = GHLClient(settings=settings)

    empty = (session.execute(text("SELECT COUNT(*) FROM signup_daily")).scalar() or 0) == 0
    if empty:
        since = HISTORY_START
    else:
        since = datetime.combine(now.astimezone(TZ).date() - timedelta(days=REFRESH_DAYS - 1), time.min, TZ)
    contacts = fetch_contacts(ghl, since, now)
    rows = summarize(contacts)

    session.execute(text("DELETE FROM signup_daily WHERE day >= :d"), {"d": since.date().isoformat()})
    for (day, seg), v in sorted(rows.items()):
        session.execute(text(
            "INSERT INTO signup_daily (day, segment, signups, with_email, with_phone, with_both, with_campaign, utm_fb, utm_ig, updated_at) "
            "VALUES (:day, :seg, :signups, :with_email, :with_phone, :with_both, :with_campaign, :utm_fb, :utm_ig, :now)"),
            {"day": day.isoformat(), "seg": seg, "now": now, **v})

    taken = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    totals = {r[0]: r for r in session.execute(text(
        "SELECT segment, SUM(signups), SUM(with_phone), SUM(with_email) FROM signup_daily GROUP BY segment")).fetchall()}
    for seg in SEGMENTS:
        t = totals.get(seg)
        session.execute(text("DELETE FROM signup_snapshot WHERE taken_at = :t AND segment = :s"), {"t": taken, "s": seg})
        session.execute(text("INSERT INTO signup_snapshot (taken_at, segment, total, with_email, with_phone) VALUES (:t, :s, :n, :e, :p)"),
                        {"t": taken, "s": seg, "n": int(t[1] or 0) if t else 0, "e": int(t[3] or 0) if t else 0, "p": int(t[2] or 0) if t else 0})
    signups = sum(v["signups"] for v in rows.values())
    logger.info("signup_intake: synced | since=%s contacts_read=%d signups=%d first_run=%s", since.date(), len(contacts), signups, empty)
    return {"since": since.date().isoformat(), "contacts_read": len(contacts), "signups": signups, "first_run": empty}


# ── tables → dashboard ─────────────────────────────────────────────────────────────────────────────

def _day_str(v: Any) -> str:
    return v.isoformat() if isinstance(v, date) else str(v)[:10]


def _utc(v: Any) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def get_trend(session: Session, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Everything the Sign-up Intake page shows. Days with no sign-ups are zero-filled; the window starts no earlier than the first
    tracked day."""
    now = now or datetime.now(tz=timezone.utc)
    today = now.astimezone(TZ).date()
    start = today - timedelta(days=max(1, days) - 1)

    daily = {(_day_str(r[0]), r[1]): dict(zip(_COUNTS, (int(x or 0) for x in r[2:9])))
             for r in session.execute(text(
                 "SELECT day, segment, signups, with_email, with_phone, with_both, with_campaign, utm_fb, utm_ig "
                 "FROM signup_daily WHERE day >= :d"), {"d": start.isoformat()}).fetchall()}
    first = session.execute(text("SELECT MIN(day) FROM signup_daily")).scalar()
    first_day = _day_str(first) if first else None
    if first_day and first_day > start.isoformat():
        start = date.fromisoformat(first_day)

    zero = dict.fromkeys(_COUNTS, 0)
    series = []
    d = start
    while d <= today:
        key = d.isoformat()
        series.append({"day": key, **{seg: dict(daily.get((key, seg), zero)) for seg in SEGMENTS}})
        d += timedelta(days=1)

    alltime = {}
    for seg in SEGMENTS:
        t = dict.fromkeys(_COUNTS, 0)
        for r in session.execute(text(
                "SELECT SUM(signups), SUM(with_email), SUM(with_phone), SUM(with_both), SUM(with_campaign), SUM(utm_fb), SUM(utm_ig) "
                "FROM signup_daily WHERE segment = :s"), {"s": seg}).fetchall():
            t = dict(zip(_COUNTS, (int(x or 0) for x in r)))
        t["email_pct"], t["phone_pct"], t["campaign_pct"] = pct(t["with_email"], t["signups"]), pct(t["with_phone"], t["signups"]), pct(t["with_campaign"], t["signups"])
        alltime[seg] = t

    cumulative: dict[str, dict[str, Any]] = {}
    for r in session.execute(text(
            "SELECT taken_at, segment, total, with_email, with_phone FROM signup_snapshot WHERE taken_at >= :s ORDER BY taken_at"),
            {"s": datetime.combine(start, time.min, TZ).astimezone(timezone.utc)}).fetchall():
        day = _utc(r[0]).astimezone(TZ).date().isoformat()                   # the last snapshot of each Central day wins
        cumulative.setdefault(day, {"day": day})[r[1]] = {"total": int(r[2]), "with_email": int(r[3]), "with_phone": int(r[4])}

    refreshed = session.execute(text("SELECT MAX(updated_at) FROM signup_daily")).scalar()
    return {"days": series, "alltime": alltime, "cumulative": [cumulative[k] for k in sorted(cumulative)],
            "first_tracked_day": first_day, "last_refreshed": _utc(refreshed).isoformat() if refreshed else None,
            "timezone": "America/Chicago"}
