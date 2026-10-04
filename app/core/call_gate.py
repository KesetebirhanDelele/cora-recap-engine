"""
Opt-out call gate - pure logic, no I/O (spec/39).

Decides from a GHL contact record whether Cora may place an outbound call. The flag is set in GHL (do-not-disturb, or a staff-applied
tag) and the thing that dials must read it: three separate incidents came from a flag set in one place that the acting code never read.

Blocks when the contact has ANY of:
  - GHL do-not-disturb on for all channels (`dnd` true)
  - GHL Call do-not-disturb active or permanent (`dndSettings.Call`)
  - a tag in the block list (app_config `call_block_tags`, comma separated; default below)
"""
from __future__ import annotations

from typing import Any

DEFAULT_BLOCK_TAGS = "do not contact,do not call again,do not call,not interested,dnd,unsubscribed"
# A person asked us to stop (as opposed to "not interested", which only ends the campaign): also marks do_not_call in Cora.
STRONG_TAGS = frozenset({"do not contact", "do not call again", "do not call", "dnd", "unsubscribed"})
_DND_ACTIVE = {"active", "permanent"}


def parse_tags(value: str | None) -> frozenset[str]:
    raw = DEFAULT_BLOCK_TAGS if value is None else value
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


def blocking_reason(record: dict[str, Any] | None, block_tags: frozenset[str]) -> tuple[str, bool] | None:
    """(reason, strong) when the call must not be placed, else None. `strong` = the person asked us to stop."""
    c = (record or {}).get("contact", record or {}) or {}
    if c.get("dnd") is True:
        return "GHL do-not-disturb is on for all channels", True
    call = (c.get("dndSettings") or {}).get("Call") or {}
    if isinstance(call, dict) and str(call.get("status", "")).lower() in _DND_ACTIVE:
        return "GHL Call do-not-disturb is active", True
    tags = {str(t).strip().lower() for t in (c.get("tags") or [])}
    hit = sorted(tags & block_tags)
    if hit:
        return f"GHL tag '{hit[0]}'", bool(set(hit) & STRONG_TAGS)
    return None
