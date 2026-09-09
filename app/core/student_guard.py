"""
Pre-call enrolled-student guard (spec/27).

Problem this closes: GHL's Cold Lead / New Lead outbound workflows trigger
on a stale ``AI Campaign = Yes`` custom field that is never cleared when a
lead converts to an enrolled student, so GHL keeps re-enrolling current
students into the cold-pitch campaign. Neither ``enter_campaign()`` nor
``launch_outbound_call_job()`` checked student status before dialing — real
enrolled students were auto-dialed with a cold sales pitch (see
PROGRESS.md 2026-09-09).

The durable fix is GHL-side (clear the field on enrollment; exclude
student-tagged contacts from the workflow trigger — tracked with Ali).
This module is the Cora-side backstop: "current students must not be
cold-called" enforced at the door regardless of GHL's list.

Detection reuses spec/23's classifier (``call_classification``) — a
``"support"`` result means "student". One carve-out is layered on top:
spec/23's keyword rule matches ``"enrolled"`` as a substring, so a
``"registered - not enrolled"`` tag (a lead who registered for an info
session but never enrolled — a *sales* target) is a false positive and is
excluded here.

Fails **open**: any GHL lookup problem logs a warning and returns ``None``
(campaign proceeds). A backstop that can cause a full outbound outage is
worse than the gap it closes.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Substrings that, when present in an otherwise student-matched tag, mean the
# contact is NOT a current student (a prospect / lapsed registrant — a sales
# target). Checked case-insensitively. Deliberately short; widen only with a
# real tag that is being mis-blocked (see spec/27 escalation trigger).
_NOT_STUDENT_TAG_MARKERS = (
    "not enrolled",
    "unenrolled",
    "non-enrolled",
    "non enrolled",
    "not a student",
    "prospective student",
    "potential student",
)

_STUDENT_TAG_KEYWORDS = ("student", "enrolled")


def _is_negated_student_tag(tag: str) -> bool:
    lowered = tag.lower()
    return any(marker in lowered for marker in _NOT_STUDENT_TAG_MARKERS)


def check_is_student(phone: str, settings: Any) -> Optional[dict[str, Any]]:
    """
    Return student details if the GHL contact for ``phone`` classifies as a
    (current or former) student, else ``None``.

    Callers should hold campaign entry / cancel outbound dialing when this
    returns non-``None``.

    Never raises — any lookup or classification failure logs a warning and
    returns ``None`` (fail open).

    Returns ``{"ghl_contact_id", "classification_source", "matched_tags"}``.
    """
    if not phone:
        return None

    try:
        from app.adapters.ghl import GHLClient
        from app.core.call_classification import (
            classify_from_known_signals,
            extract_classification_signals,
        )

        ghl = GHLClient(settings=settings)
        summary = ghl.search_contact_by_phone(phone)
        contact_id = (summary or {}).get("id")
        if not contact_id:
            return None  # unknown contact — nothing to classify

        record = ghl.get_contact(contact_id)
        contact = record.get("contact", record) or {}

        signals = extract_classification_signals(contact)
        conv_type, source = classify_from_known_signals(**signals)
        if conv_type != "support":
            return None

        matched_tags = [
            t for t in signals["tags"]
            if any(k in t.lower() for k in _STUDENT_TAG_KEYWORDS)
        ]

        # Negation carve-out — only relevant when tags were the deciding
        # signal. If every student-matched tag is a negation ("registered -
        # not enrolled"), this is a prospect, not a student.
        if source == "ghl_tags" and matched_tags and all(
            _is_negated_student_tag(t) for t in matched_tags
        ):
            return None

        return {
            "ghl_contact_id": contact_id,
            "classification_source": source,
            "matched_tags": matched_tags,
        }
    except Exception as exc:  # noqa: BLE001 — backstop must never break dialing
        logger.warning(
            "check_is_student: GHL lookup failed (failing open) | phone=<redacted>: %s",
            exc,
        )
        return None
