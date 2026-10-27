"""
Live GHL custom-field writes by label (spec/45).

Several writers called `GHLClient.update_contact_fields` without the dashboard's live-mode flags, so the client fell back to the env setting
(`ghl_writes_enabled`, False in production) and every write ran in shadow mode: nothing reached GHL (end-of-campaign finalization, enrolled leads,
the dashboard "force finalize", student summaries). Some also sent a field LABEL where GHL needs the field id, or a phone-number contact id GHL cannot
update. This helper does all three things right and returns True only when GHL really accepted the write, so callers can no longer report a shadow
write as done.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def write_label_fields(ghl: Any, contact_id: str, label_updates: dict[str, str], settings: Any, flags: Any, reason: str) -> bool:
    """Write {field label: value} to one contact. True only for a real write: the contact is matched in GHL (a phone-number id is verified against the
    contact's phone), every label resolves to a field id, and the dashboard's live flags allow writes. Never raises for an unmatched contact or a
    missing field (logs a WARNING and returns False); GHL / network errors propagate to the caller."""
    from app.core.intent_actions import resolve_ghl_contact_id
    from app.worker.jobs.crm_jobs import _resolve_to_field_ids

    ghl_id = resolve_ghl_contact_id(ghl, contact_id)
    if not ghl_id:
        logger.warning("write_label_fields: no matching GHL contact, nothing written | reason=%s", reason)
        return False
    resolved = _resolve_to_field_ids(ghl, label_updates)
    if len(resolved) != len(label_updates):
        logger.warning("write_label_fields: a field %s was not found in GHL, nothing written | reason=%s", sorted(label_updates), reason)
        return False
    result = ghl.update_contact_fields(contact_id=ghl_id, field_updates=resolved, mode_flags=flags)
    if isinstance(result, dict) and result.get("shadow"):
        logger.warning("write_label_fields: shadow mode, NOT written | reason=%s", reason)
        return False
    logger.info("write_label_fields: written | reason=%s", reason)
    return True
