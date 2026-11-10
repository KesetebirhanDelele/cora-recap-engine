"""
Callback follow-up decisions (spec/50) - pure logic, no I/O, no clock.

After a live transfer failed (or a lead's email sat unanswered) Cora must make sure a person follows up:
  * the caller / writer named a CLEAR time and that slot is free on the staff member's calendar  -> book a call-back reminder
  * no time, an unclear time, a taken slot, or a failed booking                                     -> one email to the staff member
Either way the staff member gets the lead's details and a short summary. Nothing here sends or writes anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Chicago"
MIN_LEAD_TIME = timedelta(minutes=10)      # a slot closer than this cannot be a real call-back plan
MAX_AHEAD = timedelta(days=45)
SLOT_MINUTES = 30

BOOK, EMAIL_STAFF = "book", "email_staff"

# Staff directory found in GHL on 2026-11-10 (calendar ids from /calendars/, emails from the staff's own GHL contacts).
# Override with the dashboard setting `staff_directory` (JSON list of the same shape). No secrets here.
DEFAULT_STAFF: list[dict[str, Any]] = [
    {"key": "roselen", "name": "Roselen Flores", "kind": "admissions", "ghl_id": "0swBv9tBNeXeYXPYFBSx",
     "email": "roselen@colaberry.com", "calendar_id": "Uuw0yQ8FsSBzQMtlD9aj", "aliases": ["roselen", "rose"]},
    {"key": "taiwo", "name": "Taiwo Oludimimu", "kind": "ipbc", "ghl_id": "93bhNRgb5pzSoHmaSimH",
     "email": "taiwo@colaberry.com", "calendar_id": "BhGW60bWDWPxawcaLoCh", "aliases": ["taiwo"]},
    {"key": "balakrishna", "name": "Balakrishna K", "kind": "support", "ghl_id": "yIhCTptvoNLixaWkLcRd",
     "email": "balakrishna.k@colaberry.com", "calendar_id": "CM6G3RwgWLrn4ULvXZLr", "aliases": ["bala", "balakrishna"]},
    {"key": "farhat", "name": "Farhat Beig", "kind": "support", "ghl_id": "RBa4nlSSiWeu4CoGFVyl",
     "email": "farhat@colaberry.com", "calendar_id": "LdZxL3IggIMHAoCUYIvL", "aliases": ["farhat"]},
    {"key": "balamurali", "name": "Balamurali Kasula", "kind": "support", "ghl_id": "vn8w0WF0pZcoszVn4Ll1",
     "email": "balamurali@colaberry.com", "calendar_id": "dR5QsN6ecX8UwAx2kD3h", "aliases": ["balamurali"]},
]


def parse_directory(raw: str | None) -> list[dict[str, Any]]:
    """The `staff_directory` setting, or the default when it is missing / malformed. A bad setting never leaves Cora with no staff."""
    if not raw or not str(raw).strip():
        return list(DEFAULT_STAFF)
    try:
        data = json.loads(raw)
        ok = [d for d in data if isinstance(d, dict) and d.get("key") and d.get("email") and d.get("kind")]
        return ok or list(DEFAULT_STAFF)
    except (ValueError, TypeError):
        return list(DEFAULT_STAFF)


def support_on_duty(directory: list[dict[str, Any]], roster: list[dict[str, Any]] | None, now: datetime) -> dict[str, Any] | None:
    """The support person whose shift covers `now` (dashboard `support_staff_roster`); when nobody is on shift, the next person
    whose shift starts. None when there is no usable roster (the caller then falls back to all support staff)."""
    if not roster:
        return None
    from app.core import staff_availability as sa

    active, nxt = sa.status(roster, now)
    wanted = list(active) + ([nxt[0]] if nxt else [])
    support = [p for p in directory if p.get("kind") == "support"]
    for first in wanted:
        for p in support:
            if p["name"].split(" ")[0].lower() == first.lower():
                return p
    return None


def pick_staff(kind: str, target_name: str | None, directory: list[dict[str, Any]], roster: list[dict[str, Any]] | None = None,
               now: datetime | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """(person to book, people to email). The transfer action name (e.g. 'customer_support_bala') names the person the caller was
    sent to. Admissions / payments have one named person. A support request that names nobody we know goes to the support person
    on shift at that time, or, when nobody is on shift, to the next person whose shift starts (Kes 2026-11-10). Only when there is
    no usable roster is it emailed to ALL support staff and never booked (we would be guessing whose calendar)."""
    tokens = {t for t in (target_name or "").lower().replace("-", "_").split("_") if t}
    for p in directory:
        if tokens & {a.lower() for a in p.get("aliases", [])}:
            return p, [p]
    same_kind = [p for p in directory if p.get("kind") == kind]
    if kind in ("admissions", "ipbc") and same_kind:
        return same_kind[0], [same_kind[0]]
    if kind == "support" and same_kind:
        duty = support_on_duty(directory, roster, now or datetime.now(ZoneInfo(DEFAULT_TZ)))
        if duty is not None:
            return duty, [duty]
        return None, same_kind
    adm = [p for p in directory if p.get("kind") == "admissions"]
    return (adm[0], [adm[0]]) if adm else (None, [])


def parse_requested_time(value: str | None, now: datetime, tz_name: str = DEFAULT_TZ) -> datetime | None:
    """Local 'YYYY-MM-DDTHH:MM' (the analysis model never gives an offset) -> aware datetime, or None when it is empty, malformed,
    in the past, too soon, or more than 45 days away. Anything doubtful is 'no time'."""
    if not value or not str(value).strip():
        return None
    zone = ZoneInfo(tz_name)
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    dt = dt.replace(tzinfo=zone) if dt.tzinfo is None else dt.astimezone(zone)
    ref = now.astimezone(zone)
    if dt < ref + MIN_LEAD_TIME or dt > ref + MAX_AHEAD:
        return None
    return dt


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def resolve_day_clock(day: str | None, clock: str | None, reference: datetime, tz_name: str = DEFAULT_TZ) -> str | None:
    """What the caller SAID (a day word and a clock time) -> local 'YYYY-MM-DDTHH:MM', worked out here and not by the model.
    `reference` is when the call / email happened. Needs both parts; anything unrecognised is None (= no time). A weekday means
    its next occurrence; a bare MM-DD means its next occurrence (rolling into next year when it already passed)."""
    day = (day or "").strip().lower()
    clock = (clock or "").strip()
    if not day or not clock:
        return None
    try:
        hh, mm = (int(x) for x in clock.split(":")[:2])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
    except ValueError:
        return None
    ref = reference.astimezone(ZoneInfo(tz_name))
    today = ref.date()
    target = None
    if day == "today":
        target = today
    elif day == "tomorrow":
        target = today + timedelta(days=1)
    elif day in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(day) - today.weekday()) % 7
        target = today + timedelta(days=ahead)
        if ahead == 0 and (hh, mm) <= (ref.hour, ref.minute):
            target = today + timedelta(days=7)
    else:
        try:
            if len(day) == 10:
                target = datetime.strptime(day, "%Y-%m-%d").date()
            else:
                m, d = (int(x) for x in day.split("-"))
                target = today.replace(month=m, day=d)
                if target < today:
                    target = target.replace(year=today.year + 1)
        except ValueError:
            return None
    return f"{target.isoformat()}T{hh:02d}:{mm:02d}"


def slot_is_free(start: datetime, free_slots: list[str]) -> bool:
    for s in free_slots or []:
        try:
            if datetime.fromisoformat(s) == start:
                return True
        except ValueError:
            continue
    return False


@dataclass(frozen=True)
class Plan:
    action: str                       # BOOK | EMAIL_STAFF
    reason: str                       # why this action (also stored in the ledger)
    start: datetime | None = None
    end: datetime | None = None


def decide(*, callback_wanted: bool, time_is_clear: bool, requested_local: str | None, staff: dict[str, Any] | None,
           free_slots: list[str] | None, now: datetime, tz_name: str = DEFAULT_TZ) -> Plan:
    """Book only when everything is certain: a clear explicit time, a known staff calendar, and that exact slot free.
    Every other case is one email to staff (the caller is never left without a follow-up, nothing is guessed)."""
    if staff is None or not staff.get("calendar_id"):
        return Plan(EMAIL_STAFF, "no_calendar")
    if not requested_local or not time_is_clear:
        return Plan(EMAIL_STAFF, "no_time" if not requested_local else "time_unclear")
    start = parse_requested_time(requested_local, now, tz_name)
    if start is None:
        return Plan(EMAIL_STAFF, "time_not_usable")
    if not slot_is_free(start, free_slots or []):
        return Plan(EMAIL_STAFF, "slot_taken", start=start)
    return Plan(BOOK, "clear_time_free_slot", start=start, end=start + timedelta(minutes=SLOT_MINUTES))


_REASON_TEXT = {
    "no_time": "No call-back time was given.",
    "time_unclear": "A time was mentioned but it was not clear.",
    "time_not_usable": "The time given was in the past, too soon, or too far ahead.",
    "slot_taken": "The time asked for is not free on your calendar.",
    "no_calendar": "No calendar is set up for this person, so nothing was booked.",
    "booking_failed": "Cora tried to book it and GHL refused, so nothing is on your calendar.",
    "clear_time_free_slot": "A call-back reminder was booked on your calendar.",
}


@dataclass
class Lead:
    name: str = ""
    phone: str = ""
    email: str = ""
    campaign: str = ""
    contact_id: str = ""


def build_staff_email(*, plan: Plan, lead: Lead, staff_names: list[str], source: str, source_id: str, summary: str,
                      asked_for: str = "", requested_local: str = "", tz_name: str = DEFAULT_TZ) -> tuple[str, str]:
    """(subject, plain-text body) for the staff member. No secrets, no transcript: a short summary plus the lead's own contact details."""
    who = lead.name.strip() or "A lead"
    when = ""
    if plan.start is not None:
        when = plan.start.astimezone(ZoneInfo(tz_name)).strftime("%A %b %d at %I:%M %p %Z").replace(" 0", " ")
    if plan.action == BOOK:
        subject = f"Call-back booked: {who} - {when}"
        head = f"Cora booked a call-back reminder on your calendar for {when}. Please call {who} then."
    else:
        subject = f"Please call back: {who}"
        head = f"{who} asked for a call back and needs a person to follow up. Please call them as soon as you can."
    lines = [
        f"Hi {' / '.join(n.split(' ')[0] for n in staff_names) or 'team'},", "", head, "",
        f"Name: {who}", f"Phone: {lead.phone or 'not on file'}", f"Email: {lead.email or 'not on file'}",
    ]
    if lead.campaign:
        lines.append(f"Campaign: {lead.campaign}")
    if asked_for:
        lines.append(f"Asked for: {asked_for}")
    if requested_local and plan.action != BOOK:
        lines.append(f"Time mentioned: {requested_local} (Central) - {_REASON_TEXT.get(plan.reason, '')}")
    elif plan.action != BOOK:
        lines.append(f"Time: {_REASON_TEXT.get(plan.reason, '')}")
    lines += ["", "Why they called / wrote:", (summary or "No summary available.").strip(), "",
              f"Source: {source} {source_id}", "This is an automated message from Cora."]
    return subject, "\n".join(lines)


def business_due(received_at: datetime, now: datetime, wait: timedelta = timedelta(minutes=30), tz_name: str = DEFAULT_TZ) -> bool:
    """True when `wait` of BUSINESS time (Mon-Fri 09:00-17:00 Central) has passed since an email arrived. Outside hours the clock
    starts at the next opening."""
    zone = ZoneInfo(tz_name)
    t = received_at.astimezone(zone)

    def opening(d: datetime) -> datetime:
        return d.replace(hour=9, minute=0, second=0, microsecond=0)

    def closing(d: datetime) -> datetime:
        return d.replace(hour=17, minute=0, second=0, microsecond=0)

    if t.weekday() >= 5 or t >= closing(t):
        t = opening(t + timedelta(days=1))
        while t.weekday() >= 5:
            t += timedelta(days=1)
    elif t < opening(t):
        t = opening(t)
    remaining, cur, end_now = wait, t, now.astimezone(zone)
    while remaining > timedelta(0):
        day_close = closing(cur)
        if cur + remaining <= day_close:
            cur = cur + remaining
            remaining = timedelta(0)
        else:
            remaining -= day_close - cur
            cur = opening(day_close + timedelta(days=1))
            while cur.weekday() >= 5:
                cur += timedelta(days=1)
    return end_now >= cur
