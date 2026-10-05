"""
Delivery-health rules - pure, no I/O (spec/35).

Why this exists: the SMS workflow silently matched nothing for a month while Cora's "attempted" count
looked healthy and email kept flowing. So health is judged PER CHANNEL, on DELIVERED (not attempted),
by a scheduled check for silence - a dead channel throws no error.

Outcomes: delivered | failed | pending. A hand-off still pending after `confirm_minutes` is "unconfirmed".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

CHANNELS = ("email", "sms", "call")
LABEL = {"email": "Email", "sms": "SMS", "call": "Calls"}

DELIVERED, FAILED, PENDING = "delivered", "failed", "pending"
# The lead has no email address in GHL: nothing could be delivered, and that is a data gap, not a delivery failure.
# Such hand-offs are shown separately and are NOT counted as sent / unconfirmed / failed.
NO_ADDRESS = "no_address"
GREEN, AMBER, RED, GREY = "green", "amber", "red", "grey"

# GHL message statuses (observed live 2026-10-01: SMS delivered/undelivered/sent; email delivered/opened).
_SMS_OK = {"delivered", "read"}
_SMS_BAD = {"undelivered", "failed", "error", "blocked"}
_EMAIL_OK = {"delivered", "opened", "clicked", "open", "click", "complained", "unsubscribed"}
_EMAIL_BAD = {"bounced", "bounce", "failed", "rejected", "dropped", "blocked", "error", "invalid"}
_ERR = re.compile(r"(?:Error\s+)?(\d{5})\s*[-:]?\s*([^\"\\}\n]{0,90})", re.I)


def sms_outcome(status: str | None) -> str:
    s = (status or "").strip().lower()
    return DELIVERED if s in _SMS_OK else FAILED if s in _SMS_BAD else PENDING


def email_outcome(status: str | None) -> str:
    s = (status or "").strip().lower()
    return DELIVERED if s in _EMAIL_OK else FAILED if s in _EMAIL_BAD else PENDING


def message_outcome(channel: str, status: str | None) -> str:
    return email_outcome(status) if channel == "email" else sms_outcome(status)


def extract_error(message: dict) -> str | None:
    """Twilio-style 'Error 30003 - Number unreachable' text from a GHL message, if any."""
    blob = str(message.get("error") or "") + " " + str(message.get("meta") or "")
    m = _ERR.search(blob)
    return f"{m.group(1)} {m.group(2).strip()}".strip()[:200] if m else None


def call_outcome(end_call_reason: str | None, duration_seconds: float | None) -> str:
    """A call is DELIVERED when it connected - a person or a voicemail box. Synthflow 'undefined'
    with a few seconds on the line counts as connected; zero-length / failed-to-start does not."""
    r = (end_call_reason or "").strip().lower()
    d = duration_seconds or 0
    if r in ("voicemail", "human_pick_up_cut_off", "human_goodbye", "agent_goodbye"):
        return DELIVERED
    if r in ("undefined", "") and d >= 5:
        return DELIVERED
    return FAILED


@dataclass(frozen=True)
class Thresholds:
    silence_hours: float = 60.0            # Ali: alert after 2.5 days without a delivery
    amber_silence_hours: float = 36.0
    confirm_minutes: int = 15              # email / SMS hand-off not confirmed after this = unconfirmed
    call_confirm_minutes: int = 25         # call logs reach Cora 10-15 min after the call
    min_volume: int = 20                   # collapse rules need at least this many sent in 24h
    red_rate: float = 0.5
    amber_rate: float = 0.9
    amber_unconfirmed: float = 0.10

    @staticmethod
    def hours(value: float) -> timedelta:
        return timedelta(hours=value)


@dataclass
class ChannelStats:
    channel: str
    sent: int = 0
    delivered: int = 0
    failed: int = 0
    unconfirmed: int = 0
    no_address: int = 0
    last_delivered_at: datetime | None = None
    last_handoff_at: datetime | None = None
    ghl_delivered_24h: int | None = None
    muted_reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    level: str = GREEN

    @property
    def rate(self) -> float | None:
        resolved = self.delivered + self.failed
        return (self.delivered / resolved) if resolved else None


def active_day_hours(since: datetime, now: datetime, days: set[int], tz_name: str) -> float:
    """Hours between `since` and `now` that fall on a campaign-active weekday (0=Mon .. 6=Sun, in tz_name).
    Calls only run on active days (cold leads Mon-Fri), so a weekend of silence is expected, not an outage."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    cur, end, total = since.astimezone(tz), now.astimezone(tz), 0.0
    while cur < end:
        nxt = min(end, (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        if cur.weekday() in days:
            total += (nxt - cur).total_seconds() / 3600
        cur = nxt
    return total


def evaluate(stats: ChannelStats, now: datetime, th: Thresholds, tracker_age_hours: float,
             silent_hours: float | None = None) -> ChannelStats:
    """Fill stats.level / stats.reasons. Rules (same for the tile colour and the alert):
      RED   no delivery for >= silence_hours, or >= min_volume sent in 24h and < red_rate delivered
      AMBER silent >= amber_silence_hours, or delivered rate < amber_rate, or > amber_unconfirmed unconfirmed
      GREY  muted on purpose (paused / operator mute) - shown, never alerts
    `tracker_age_hours`: how long delivery data has existed; silence is not judged before the
    tracker has watched a full window (an empty new table must not look like a dead channel)."""
    if stats.muted_reason:
        stats.level, stats.reasons = GREY, [stats.muted_reason]
        return stats
    level = GREEN
    reasons: list[str] = []

    rank = {GREEN: 0, AMBER: 1, RED: 2}

    def bump(to: str, why: str) -> None:
        nonlocal level
        reasons.append(why)
        if rank[to] > rank[level]:
            level = to

    silent_h = None
    if stats.last_delivered_at is not None:
        silent_h = silent_hours if silent_hours is not None else (now - stats.last_delivered_at).total_seconds() / 3600
    judged = silent_h is not None or tracker_age_hours >= th.silence_hours
    if judged:
        if silent_h is None or silent_h >= th.silence_hours:
            bump(RED, "no delivery in %s" % (f"{silent_h / 24:.1f} days" if silent_h is not None
                                              else f"the last {tracker_age_hours / 24:.1f} days"))
        elif silent_h >= th.amber_silence_hours:
            bump(AMBER, f"quiet for {silent_h:.0f} hours")
    if stats.sent >= th.min_volume and stats.rate is not None:
        if stats.rate < th.red_rate:
            bump(RED, f"only {stats.rate:.0%} of {stats.delivered + stats.failed} resolved were delivered")
        elif stats.rate < th.amber_rate:
            bump(AMBER, f"delivery rate {stats.rate:.0%}")
    if stats.sent >= th.min_volume and stats.sent and stats.unconfirmed / stats.sent > th.amber_unconfirmed:
        why = f"{stats.unconfirmed} of {stats.sent} not confirmed"
        # everything unconfirmed is the signature of a dead workflow
        bump(RED if stats.delivered == 0 and stats.failed == 0 else AMBER, why)
    stats.level, stats.reasons = level, reasons
    return stats
