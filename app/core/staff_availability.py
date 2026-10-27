"""
Who can take a live transfer right now - pure, no I/O (spec/39).

Why: the voice agents were attempting a live transfer when nobody was on shift. The prompt states office hours but gives the
agent no clock, so it tried, the Synthflow transfer action answered "transfer_outside_availability" (47 times in production),
and the caller got a failure line instead of a booked callback. Cora builds the outbound prompt per call, so it can say exactly
who is available at the moment the call starts and tell the agent to book a callback instead of transferring when nobody is.

Admissions and Payments/IPBC are Mon-Fri 9AM-5PM Central (what the prompts say). Support follows the dashboard's
`support_staff_roster` (name, shift days, shift start / end, overnight shifts supported). "CST" in the prompts means Central time.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.staff_roster import MINUTES_PER_WEEK, _expand_intervals, _week_minute

TIMEZONE = "America/Chicago"
OFFICE_DAYS = [0, 1, 2, 3, 4]                       # Monday .. Friday
OFFICE_START, OFFICE_END = "09:00", "17:00"


def _entry(name: str, days: list[int] | None = None, start: str = OFFICE_START, end: str = OFFICE_END) -> dict[str, Any]:
    return {"name": name, "ghl_id": name, "days": OFFICE_DAYS if days is None else days, "shift_start": start, "shift_end": end}


def parse_people(raw: str | None, default_name: str) -> list[dict[str, Any]]:
    """The primary person of an `admissions_assistants` / `ipbc_assistants` setting (JSON list of {name, ghl_id}), office hours.
    A missing or malformed setting falls back to `default_name` so the line is always built."""
    name = default_name
    try:
        first = json.loads(raw or "[]")[0]
        name = str(first.get("name") or "").strip().split(" ")[0] or default_name
    except (ValueError, TypeError, IndexError, AttributeError, KeyError):
        pass
    return [_entry(name)]


def parse_roster(raw: str | None) -> list[dict[str, Any]]:
    """`support_staff_roster` JSON -> entries with a usable name and schedule; anything malformed is dropped."""
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for e in data if isinstance(data, list) else []:
        try:
            _expand_intervals(e)
            if str(e.get("name") or "").strip():
                out.append(e)
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return out


def status(entries: list[dict[str, Any]], now: datetime, tz: str = TIMEZONE) -> tuple[list[str], tuple[str, datetime] | None]:
    """(first names on shift right now, (first name, local datetime) of the next shift start or None)."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    here = _week_minute(local)
    active: list[str] = []
    upcoming: list[tuple[int, str]] = []
    for e in entries:
        first = str(e.get("name") or "").strip().split(" ")[0]
        for start, end in _expand_intervals(e):
            for off in (-MINUTES_PER_WEEK, 0, MINUTES_PER_WEEK):
                s, f = start + off, end + off
                if s <= here < f and first not in active:
                    active.append(first)
                elif s > here:
                    upcoming.append((s - here, first))
    nxt = None
    if upcoming:
        delta, who = min(upcoming)
        nxt = (who, (local + timedelta(minutes=delta)).replace(second=0, microsecond=0))
    return active, nxt


def _when(local: datetime, now_local: datetime) -> str:
    day = "today" if local.date() == now_local.date() else local.strftime("%A")
    return f"{day} {local.strftime('%I:%M %p').lstrip('0')} Central"


def _team_line(label: str, entries: list[dict[str, Any]], now: datetime, tz: str) -> str:
    if not entries:
        return f"- {label}: schedule unknown - try the transfer once; if it fails, follow the failed-transfer rule above."
    active, nxt = status(entries, now, tz)
    if active:
        return f"- {label}: AVAILABLE NOW ({', '.join(active)})."
    if nxt:
        local_now = now.astimezone(ZoneInfo(tz))
        return f"- {label}: NOT available now. Next available: {_when(nxt[1], local_now)}."
    return f"- {label}: NOT available now."


DEFAULT_CALLBACK_ONLY = "admissions,support"       # Synthflow cannot transfer a live call to numbers outside the US (Kes 2026-10-26)


def parse_callback_only(value: str | None) -> frozenset[str]:
    """Teams that must never get a live transfer (callback only): 'admissions', 'ipbc', 'support'. None -> the default; '' -> none."""
    raw = DEFAULT_CALLBACK_ONLY if value is None else value
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


DEFAULT_LIVE_PEOPLE = "balamurali"                  # a person on a callback-only team whose number works for live transfer (Kes 2026-10-26)


def parse_live_people(value: str | None) -> frozenset[str]:
    """First names (lower case) who can take a live transfer even though their team is callback-only. None -> the default; '' -> nobody."""
    raw = DEFAULT_LIVE_PEOPLE if value is None else value
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


def _callback_line(label: str, entries: list[dict[str, Any]], now: datetime, tz: str, live_people: frozenset[str] = frozenset()) -> str:
    """A team that is callback-only: say when it is staffed, but never offer a live transfer, except to a named person whose number works and who is on shift."""
    active, nxt = status(entries, now, tz) if entries else ([], None)
    live_now = [n for n in active if n.lower() in live_people]
    if live_now:
        return (f"- {label}: live transfer ONLY to {', '.join(live_now)} (on shift now, AVAILABLE NOW); anyone else on this team is callback only: "
                f"book the callback.")
    when = "now" if active else (f"from {_when(nxt[1], now.astimezone(ZoneInfo(tz)))}" if nxt else "during office hours")
    return f"- {label}: NO live transfer (callback only). The team can call back {when}: book the callback."


def availability_block(now: datetime, admissions_raw: str | None, ipbc_raw: str | None, roster_raw: str | None,
                       tz: str = TIMEZONE, callback_only: str | None = None, live_people: str | None = None) -> str:
    """The text put at the very top of an outbound call prompt. Contains no secrets and no URLs."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    clock = f"{local.strftime('%A')} {local.strftime('%I:%M %p').lstrip('0')} Central"
    cb = parse_callback_only(callback_only)
    live = parse_live_people(live_people)
    teams = (("admissions", "Admissions", parse_people(admissions_raw, "Roselen")),
             ("ipbc", "Payments / IPBC", parse_people(ipbc_raw, "Taiwo")),
             ("support", "Support", parse_roster(roster_raw)))
    lines = [_callback_line(label, entries, now, tz, live) if key in cb else _team_line(label, entries, now, tz) for key, label, entries in teams]
    return "\n".join([
        "## LIVE STATUS - computed when this call started; trust it over any other assumption about the time",
        f"Current time: {clock}.",
        *lines,
        "",
        "Before ANY transfer, find the team the caller needs above. A team marked \"NO live transfer\" must NEVER be transferred to: say "
        "\"Let me set up a time for our team to call you back - what day and time works best for you?\" and book it with the booking tool. "
        "For a team marked NOT available now, do the same. Only try a transfer (once) to a team marked AVAILABLE NOW. The failed-transfer "
        "rule below still applies. Never ask the caller whether they would rather keep talking to you.",
        "",
        "---",
        "",
    ])
