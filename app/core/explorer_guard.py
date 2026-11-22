"""
Pre-call Explorer guard (spec/51).

Explorer sign-ups (the people on Ali's accelerator list) are worked by Rose and Taiwo, not by Cora's AI
outbound campaigns (Kes, 2026-11-21). GHL's "Contact Created" workflow still sends every new lead with a
phone number to Cora's New Lead campaign, and the Cold Lead tag workflows re-send people every 45 days, so
Cora would call them first (86 of the 87 people on Rose's first list with a known account date had their
first Cora call within 11 hours after it). This module is the Cora-side backstop: anyone carrying an Explorer tag is never dialed by an AI
campaign, whatever GHL sends.

Same shape as app/core/student_guard.py: reads the GHL contact live by phone, returns details when the
contact carries one of the configured tags (default "ai explorer"), else None. Callers hold campaign entry
and cancel outbound dialing when this returns non-None.

Fails OPEN: a GHL lookup problem logs a warning and returns None (the campaign proceeds), because a
backstop that can cause a full outbound outage is worse than the gap it closes. It is switched off by
explorer_call_exclusion_enabled=false.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def exclusion_tags(settings: Any) -> frozenset[str]:
    """Lower-cased tags that keep a contact away from Cora's AI calls (comma separated in settings)."""
    raw = getattr(settings, "explorer_call_exclusion_tags", "") or ""
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


def matching_tags(contact_tags: Any, wanted: frozenset[str]) -> list[str]:
    """The contact's tags (original spelling) that are in `wanted`, compared case-insensitively. Pure."""
    return [t for t in (contact_tags or []) if isinstance(t, str) and t.strip().lower() in wanted]


def check_is_explorer(phone: str, settings: Any) -> Optional[dict[str, Any]]:
    """
    Return ``{"ghl_contact_id", "matched_tags"}`` when the GHL contact for ``phone`` carries an Explorer
    exclusion tag, else ``None``. Never raises.
    """
    if not phone or not getattr(settings, "explorer_call_exclusion_enabled", False):
        return None
    wanted = exclusion_tags(settings)
    if not wanted:
        return None
    try:
        from app.adapters.ghl import GHLClient

        ghl = GHLClient(settings=settings)
        summary = ghl.search_contact_by_phone(phone)
        contact_id = (summary or {}).get("id")
        if not contact_id:
            return None  # unknown contact: nothing to exclude
        record = ghl.get_contact(contact_id)
        contact = record.get("contact", record) or {}
        hit = matching_tags(contact.get("tags"), wanted)
        if not hit:
            return None
        return {"ghl_contact_id": contact_id, "matched_tags": hit}
    except Exception as exc:  # noqa: BLE001 - backstop must never break dialing
        logger.warning("check_is_explorer: GHL lookup failed (failing open) | phone=<redacted>: %s", exc)
        return None
