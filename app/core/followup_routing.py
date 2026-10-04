"""
Follow-up delivery routing - pure, no I/O (spec/33).

Decides WHICH GHL custom fields Cora writes after it has recorded a voicemail follow-up, because in
GHL the write itself is what sends the message:

  channel "email" -> Support Issue Ticket #2 (subject)  + Message (body)  -> workflow "AI Agent - Send Email"
  channel "sms"   -> Support issue Ticket #4 (the text)                   -> workflow "AI Agent - Send SMS"

An "sms" follow-up is delivered as a real SMS - unless it carries a link other than myfreeaiclass.com
(app/core/sms_links.py), in which case it is delivered as an EMAIL instead. Setting app_config `sms_delivery_mode` to "email"
restores the legacy behaviour (the SMS text goes to Ticket #2 and is emailed) as an instant rollback.

Support issue Ticket #4 is the body of the SMS workflow, so it must NEVER receive anything that is not
meant to be texted - the lead-classification tag that used to be written there (e.g. "warm_lead") would
now be sent to the lead as an SMS, and is no longer written at all.

SMS safety: the text is only written when the live GHL contact is readable, has a phone and is not
DND / opted out for SMS (app/core/sms_eligibility.py); otherwise the SMS is skipped and the reason
returned so the job can log it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.sms_eligibility import contact_block_reason
from app.core.sms_gate import normalize_sms
from app.core.sms_links import (
    email_html_from_text,
    email_subject_from_text,
    parse_allowed_domains,
    sms_needs_email,
)

GHL_MESSAGE_MAX = 2000      # GHL text fields cap around 2000 chars
SMS_TEXT_MAX = 640          # sanity cap; the generator already limits SMS to ~240 chars
LEGACY_TICKET2_MAX = 200


def normalise_sms_mode(value: str | None) -> str:
    """'email' = legacy (SMS text emailed); anything else = real SMS."""
    return "email" if (value or "").strip().lower() == "email" else "sms"


@dataclass(frozen=True)
class FollowupPlan:
    updates: dict[str, str]
    sms_skip_reason: str | None = None
    route: str = "email"          # email | sms | sms_legacy_email | sms_link_to_email


def build_followup_updates(
    settings: Any,
    *,
    channel: str,
    subject: str,
    body: str,
    ghl_contact: dict | None,
    sms_mode: str = "sms",
    allowed_link_domains: str | None = None,
) -> FollowupPlan:
    """Which GHL fields to write for one recorded follow-up (see module docstring)."""
    updates: dict[str, str] = {}

    if settings.ghl_field_mark_as_lead:
        updates[settings.ghl_field_mark_as_lead] = "Yes"
    if settings.ghl_field_ai_campaign:
        updates[settings.ghl_field_ai_campaign] = "Yes"

    body_g = (body or "")[:GHL_MESSAGE_MAX]
    if settings.ghl_field_message and body_g:
        updates[settings.ghl_field_message] = body_g          # record of the last message sent

    if channel == "email":
        # Same eligibility the text path applies (spec/39 follow-up, Ali 2026-10-03): the lead's live GHL record must not say
        # stop (do-not-disturb on all channels or email, or an opt-out tag) and must have an email address. An unreadable
        # record fails closed. A skip removes the send-trigger fields so no GHL workflow sends anything.
        erecord = ghl_contact or {}
        if isinstance(erecord.get("contact"), dict):
            erecord = erecord["contact"]
        eskip = "GHL contact could not be read" if not erecord else contact_block_reason(erecord, "email")
        if eskip:
            updates.pop(settings.ghl_field_message, None)
            return FollowupPlan(updates, eskip, "email")
        if settings.ghl_field_support_ticket_2 and subject:
            updates[settings.ghl_field_support_ticket_2] = subject
        return FollowupPlan(updates, None, "email")

    # channel == "sms"
    if normalise_sms_mode(sms_mode) == "email":
        if settings.ghl_field_support_ticket_2 and body:
            updates[settings.ghl_field_support_ticket_2] = body[:LEGACY_TICKET2_MAX]
        return FollowupPlan(updates, None, "sms_legacy_email")

    body = normalize_sms(body)            # plain GSM-7: keeps the text at 1-2 segments (spec/37)
    record = ghl_contact or {}
    if isinstance(record.get("contact"), dict):
        record = record["contact"]

    # A text may only carry allow-listed links (default myfreeaiclass.com). Any other link means
    # this touch must go by EMAIL (Kes, 2026-10-01).
    if sms_needs_email(body, parse_allowed_domains(allowed_link_domains)):
        if not record:
            return FollowupPlan(updates, "GHL contact could not be read", "sms_link_to_email")
        reason = contact_block_reason(record, "email")
        if reason or not settings.ghl_field_support_ticket_2:
            return FollowupPlan(updates, reason or "GHL_FIELD_SUPPORT_TICKET_2 is not configured", "sms_link_to_email")
        updates[settings.ghl_field_message] = email_html_from_text(body)[:GHL_MESSAGE_MAX]
        updates[settings.ghl_field_support_ticket_2] = email_subject_from_text(body)
        return FollowupPlan(updates, None, "sms_link_to_email")

    skip: str | None
    if not settings.ghl_field_support_ticket_4:
        skip = "GHL_FIELD_SUPPORT_TICKET_4 is not configured"
    elif not record:
        skip = "GHL contact could not be read"
    else:
        skip = contact_block_reason(record, "sms")
    if skip is None and body:
        updates[settings.ghl_field_support_ticket_4] = body[:SMS_TEXT_MAX]
    return FollowupPlan(updates, skip, "sms")
