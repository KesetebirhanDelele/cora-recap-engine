"""
SMS eligibility - pure logic, no I/O (spec/32).

Decides whether a lead may be sent an automated SMS, from data the caller has
already loaded. Three outcomes:

  block - never text this lead (DND / STOP / do-not-contact). The incident is
          closed with the reason; nothing is retried.
  hold  - don't text right now, but the lead is still eligible later
          (outside the sending window, or the lead replied and a human is handling it).
          The incident stays open.
  send  - no signal against it.

Sources, strongest first:
  1. GHL contact: `dnd` (all channels), `dndSettings.SMS.status` active/permanent
     (GHL sets this itself when a lead replies STOP), opt-out tags.
  2. Cora lead_state: do_not_call, invalid number, closed status,
     not_interested / wrong_number sales outcome.
  3. An inbound message that is just STOP/UNSUBSCRIBE/etc.
  4. Cora lead_state.last_replied_at -> HOLD (Cora's own rule: a reply suppresses
     automated messaging; a person should answer).
Window logic (campaign hours + TCPA floor) is applied by the caller via
`in_tcpa_hours`, because it needs the DB-backed campaign settings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SEND = "send"
BLOCK = "block"
HOLD = "hold"

# Federal TCPA floor: no automated texts before 8:00 or after 21:00 local time,
# regardless of how permissive the campaign window is configured.
TCPA_EARLIEST_HOUR = 8
TCPA_LATEST_HOUR = 21

_DND_ACTIVE = {"active", "permanent"}
_OPT_OUT_TAGS = {
    "dnd", "do not disturb", "do not contact", "do not text", "do_not_contact",
    "unsubscribed", "opted out", "opt out", "opt-out", "sms opt out", "sms opt-out", "stop",
}
STOP_REPLY = re.compile(
    r"^\s*(stop|stopall|stop all|unsubscribe|cancel|end|quit|opt[\s-]?out)\W*\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class Verdict:
    action: str          # SEND | BLOCK | HOLD
    reason: str = ""
    kind: str = ""       # for HOLD: "window" | "replied"
    next_open: str | None = None


def ghl_dnd_reason(contact: dict[str, Any] | None, channel: str = "sms") -> str | None:
    """Why a GHL contact must not be contacted on `channel` ("sms" | "email"), or None.
    Another channel's DND (e.g. email-only DND for a text) does not block."""
    if not contact:
        return None
    if contact.get("dnd") is True:
        return "GHL DND is on (all channels)"
    settings = contact.get("dndSettings") or {}
    key = "Email" if channel == "email" else "SMS"
    ch = settings.get(key) or settings.get(key.lower()) or {}
    if isinstance(ch, dict) and str(ch.get("status", "")).lower() in _DND_ACTIVE:
        return f"GHL DND is on for {key} (lead opted out)"
    for tag in contact.get("tags") or []:
        if str(tag).strip().lower() in _OPT_OUT_TAGS:
            return f"contact tagged '{tag}'"
    return None


def contact_block_reason(contact: dict[str, Any] | None, channel: str = "sms") -> str | None:
    """DND / opt-out tag, or nothing to send to (no email address / no phone)."""
    reason = ghl_dnd_reason(contact, channel)
    if reason:
        return reason
    if channel == "email" and not (contact or {}).get("email"):
        return "no email address on the contact"
    if channel == "sms" and not (contact or {}).get("phone"):
        return "no phone number on the contact"
    return None


def is_stop_reply(body: str | None) -> bool:
    return bool(body and STOP_REPLY.match(body))


def cora_state_verdict(lead: dict[str, Any] | None, has_stop_reply: bool) -> Verdict | None:
    """Verdict from Cora's own records, or None when nothing is against sending."""
    if has_stop_reply:
        return Verdict(BLOCK, "lead replied STOP / UNSUBSCRIBE")
    if not lead:
        return None
    if lead.get("do_not_call"):
        return Verdict(BLOCK, "marked do-not-contact in Cora")
    if lead.get("invalid"):
        return Verdict(BLOCK, "invalid / wrong number")
    if (lead.get("status") or "").lower() == "closed":
        return Verdict(BLOCK, "lead is closed in Cora")
    if (lead.get("sales_outcome") or "").lower() in {"not_interested", "wrong_number"}:
        return Verdict(BLOCK, f"sales outcome: {lead['sales_outcome']}")
    if lead.get("last_replied_at"):
        return Verdict(HOLD, "lead replied - needs a human follow-up, not an automated text", kind="replied")
    return None


def in_tcpa_hours(local_hour: int) -> bool:
    return TCPA_EARLIEST_HOUR <= local_hour < TCPA_LATEST_HOUR
