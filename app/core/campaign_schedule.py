"""
Campaign active-window enforcement.

Determines whether a campaign is currently within its configured calling
hours and days, and computes the next window-open moment when it is not.

The active window is evaluated in the **caller's local timezone**, read from
raw_payload_json['timezone'] on the most recent call_event for that contact.
Falls back to settings.default_timezone when no timezone is recorded.

Days use ISO weekday numbering: 0=Monday, 6=Sunday.

Window values are read from the app_config table first (runtime-editable via
the dashboard Settings page), falling back to Settings fields (.env), then
to the hard-coded defaults below.

Configured via dashboard Settings page or .env:
  NEW_LEAD_ACTIVE_DAYS        — comma-separated weekday numbers (default: all days)
  NEW_LEAD_ACTIVE_START_HOUR  — 24-hour start of window (default: 8)
  NEW_LEAD_ACTIVE_END_HOUR    — 24-hour end of window, exclusive (default: 22)
  COLD_LEAD_ACTIVE_DAYS       — comma-separated weekday numbers (default: Mon–Fri)
  COLD_LEAD_ACTIVE_START_HOUR — 24-hour start of window (default: 8)
  COLD_LEAD_ACTIVE_END_HOUR   — 24-hour end of window, exclusive (default: 22)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings

logger = logging.getLogger(__name__)

# Normalise all known campaign name variants to a canonical key
_CAMPAIGN_KEY: dict[str, str] = {
    "new lead":  "new_lead",
    "new_lead":  "new_lead",
    "cold lead": "cold_lead",
    "cold_lead": "cold_lead",
}


def get_contact_timezone(session, contact_id: str, settings: Settings) -> str:
    """
    Look up the caller's IANA timezone from the most recent call_event for this contact.

    Reads raw_payload_json['timezone'] from the most recent call_event row.
    Validates the value is a real IANA timezone string using ZoneInfo.
    Falls back to settings.default_timezone if:
      - no call_event exists for the contact
      - raw_payload_json is missing or has no 'timezone' key
      - the value is not a valid IANA timezone identifier

    Never raises — always returns a usable timezone string.
    """
    from sqlalchemy import desc, select

    from app.models.call_event import CallEvent

    try:
        row = session.scalars(
            select(CallEvent)
            .where(CallEvent.contact_id == contact_id)
            .order_by(desc(CallEvent.created_at))
            .limit(1)
        ).first()

        if row and row.raw_payload_json:
            tz_str = row.raw_payload_json.get("timezone")
            if tz_str and isinstance(tz_str, str):
                ZoneInfo(tz_str)  # raises ZoneInfoNotFoundError if not a valid IANA name
                logger.debug(
                    "get_contact_timezone | contact_id=%s tz=%s", contact_id, tz_str
                )
                return tz_str
    except ZoneInfoNotFoundError:
        logger.warning(
            "get_contact_timezone: unrecognised timezone %r for contact_id=%s — "
            "falling back to %s",
            (row.raw_payload_json or {}).get("timezone") if row else None,
            contact_id,
            settings.default_timezone,
        )
    except Exception:
        logger.warning(
            "get_contact_timezone: unexpected error for contact_id=%s — "
            "falling back to %s",
            contact_id,
            settings.default_timezone,
            exc_info=True,
        )

    return settings.default_timezone


def _get_window(
    campaign_name: str,
    settings: Settings,
    session=None,
) -> tuple[set[int], int, int]:
    """
    Return (active_days_set, start_hour, end_hour) for the campaign.

    Reads from app_config DB table when session is provided (DB-first).
    Falls back to settings fields (.env) when DB row is absent.
    """
    from app.core.app_config import get_int, get_str

    key = _CAMPAIGN_KEY.get((campaign_name or "").strip().lower(), "new_lead")

    if session is not None:
        if key == "cold_lead":
            days_str = get_str("cold_lead_active_days", session, settings, "0,1,2,3,4")
            start = get_int("cold_lead_active_start_hour", session, settings, 8)
            end = get_int("cold_lead_active_end_hour", session, settings, 22)
        else:
            days_str = get_str("new_lead_active_days", session, settings, "0,1,2,3,4,5,6")
            start = get_int("new_lead_active_start_hour", session, settings, 8)
            end = get_int("new_lead_active_end_hour", session, settings, 22)
        return _parse_days(days_str), start, end

    # No session — fall back directly to settings fields
    if key == "cold_lead":
        days = _parse_days(settings.cold_lead_active_days)
        return days, settings.cold_lead_active_start_hour, settings.cold_lead_active_end_hour
    else:
        days = _parse_days(settings.new_lead_active_days)
        return days, settings.new_lead_active_start_hour, settings.new_lead_active_end_hour


def _parse_days(days_str: str) -> set[int]:
    """Parse a comma-separated weekday string into a set of integers."""
    result = set()
    for part in days_str.split(","):
        part = part.strip()
        if part:
            result.add(int(part))
    return result


def is_campaign_active(
    campaign_name: str,
    now: datetime,
    settings: Settings,
    contact_tz: str | None = None,
    session=None,
) -> bool:
    """
    Return True if the campaign is within its active window at `now`.

    `now` may be naive or timezone-aware; it is converted to the caller's
    local timezone (contact_tz) before comparing against configured hours.
    Falls back to settings.default_timezone when contact_tz is None.

    When session is provided, window settings are read from the app_config DB
    table (runtime-editable) before falling back to Settings/.env values.

    Active window is [start_hour, end_hour) — start inclusive, end exclusive.
    For example, start=8 end=22 means calls are allowed from 08:00 to 21:59.
    """
    tz = ZoneInfo(contact_tz or settings.default_timezone)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    active_days, start_hour, end_hour = _get_window(campaign_name, settings, session)
    weekday = local_now.weekday()  # 0=Monday, 6=Sunday
    return weekday in active_days and start_hour <= local_now.hour < end_hour


def next_active_window_start(
    campaign_name: str,
    now: datetime,
    settings: Settings,
    contact_tz: str | None = None,
    session=None,
) -> datetime:
    """
    Return the earliest datetime >= now when the campaign window opens.

    If `now` is already within the active window, returns `now` unchanged.
    Otherwise returns the start of the next open window (start_hour on the
    nearest active day) in the caller's local timezone (contact_tz).
    Falls back to settings.default_timezone when contact_tz is None.

    When session is provided, window settings are read from the app_config DB
    table (runtime-editable) before falling back to Settings/.env values.

    The returned datetime is timezone-aware.
    """
    tz = ZoneInfo(contact_tz or settings.default_timezone)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    active_days, start_hour, end_hour = _get_window(campaign_name, settings, session)

    # Already active — return unchanged
    if local_now.weekday() in active_days and start_hour <= local_now.hour < end_hour:
        return local_now

    # Same day, before the window opens
    if local_now.weekday() in active_days and local_now.hour < start_hour:
        return local_now.replace(hour=start_hour, minute=0, second=0, microsecond=0)

    # Past end of window (or inactive day) — advance to next active day at start_hour
    candidate = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(8):
        candidate = candidate + timedelta(days=1)
        if candidate.weekday() in active_days:
            return candidate.replace(hour=start_hour, minute=0, second=0, microsecond=0)

    # Fallback: should not reach here when active_days is non-empty
    logger.error(
        "next_active_window_start: no active day found in next 8 days | campaign=%s",
        campaign_name,
    )
    return local_now + timedelta(days=1)
