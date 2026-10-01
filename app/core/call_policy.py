"""
Outbound call policy - pure helpers (no I/O) (spec/33).

Hard cap: a lead is dialed at most `max_calls_per_lead_per_day` times (default 2) in one
calendar day of the LEAD's own timezone, unless the lead asked for a call back.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_MAX_CALLS_PER_DAY = 2

# `intent_reason` values written by app/core/intent_actions.py when the LEAD asked to be
# called (exact-time callback, "call me later", or asked for a human/transfer). These
# calls are exempt from the daily cap and are never moved by the slot rebalancer.
CALLBACK_INTENT_REASONS = frozenset(
    {"callback_request", "callback_with_time", "call_later_no_time", "transfer_requested"}
)


def is_lead_requested_callback(payload: dict | None) -> bool:
    return ((payload or {}).get("intent_reason") or "") in CALLBACK_INTENT_REASONS


def local_day_start_utc(now: datetime, tz_name: str) -> datetime:
    """00:00 of `now`'s calendar day in tz_name, as an aware UTC datetime."""
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    return datetime(local.year, local.month, local.day, tzinfo=tz).astimezone(timezone.utc)


def next_local_day_start_utc(now: datetime, tz_name: str) -> datetime:
    """00:00 of the NEXT calendar day in tz_name, as an aware UTC datetime."""
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    midnight = datetime(local.year, local.month, local.day, tzinfo=tz) + timedelta(days=1)
    return midnight.astimezone(timezone.utc)
