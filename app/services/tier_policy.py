"""
Tier policy service — campaign-policy-driven voicemail tier scheduling.

Maps (campaign_type, current_tier) → TierTransitionPolicy containing:
  delay_minutes:              how long to wait before next callback
  schedule_synthflow_callback: whether to schedule a Synthflow call
  is_terminal:                whether this advancement ends the campaign

Canonical tier model (from spec, shared across all campaigns):
  None → '0' → '1' → '2' → '3' (terminal)

Cold Lead delays (from settings, pre-configured):
  None → '0': 120 minutes (2 hours)
  '0'  → '1': 2880 minutes (2 days)
  '1'  → '2': 2880 minutes (2 days)
  '2'  → '3': terminal, no Synthflow callback

New Lead delays (from settings, currently UNRESOLVED):
  Callers MUST call settings.validate_for_new_lead_vm_policy() before
  using New Lead policy. This raises ConfigError if any delay is unset.

Stop condition (from autonomous execution contract §2):
  Do not advance New Lead tier without all delay settings resolved.
  Raise ConfigError via validate_for_new_lead_vm_policy() — do not guess.

Duplicate callback prevention:
  has_pending_callback() checks scheduled_jobs for an existing
  pending/claimed callback for the same entity before scheduling.
  This enforces one scheduled callback per tier advancement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TierTransitionPolicy:
    """
    Policy for a single voicemail tier advancement.

    delay_minutes:               delay before scheduling the Synthflow callback.
    schedule_synthflow_callback: True for tiers 0-2; False at terminal tier 3.
    is_terminal:                 True only for tier 3 — no further advancement.
    campaign_name:               the campaign this policy applies to.
    current_tier:                the tier before this advancement.
    next_tier:                   the tier after this advancement.
    """

    delay_minutes: int
    schedule_synthflow_callback: bool
    is_terminal: bool
    campaign_name: str
    current_tier: Optional[str]
    next_tier: str


def get_cold_lead_policy(
    current_tier: Optional[str],
    settings: Settings | None = None,
) -> TierTransitionPolicy:
    """
    Return the Cold Lead tier advancement policy for the given current tier.

    Uses settings.cold_vm_tier_* for delay durations.
    Cold Lead delays are fully resolved in config (no stop condition).
    """
    settings = settings or get_settings()

    tier_map: dict[Optional[str], tuple[int, str]] = {
        None: (settings.cold_vm_tier_none_delay_minutes, "0"),
        "0": (settings.cold_vm_tier_0_delay_minutes, "1"),
        "1": (settings.cold_vm_tier_1_delay_minutes, "2"),
        "2": (0, "3"),  # terminal: no delay, no callback
    }

    if current_tier not in tier_map:
        raise ValueError(
            f"Invalid Cold Lead tier: {current_tier!r}. "
            "Valid values: None, '0', '1', '2'."
        )

    delay_minutes, next_tier = tier_map[current_tier]
    is_terminal = next_tier == "3"

    return TierTransitionPolicy(
        delay_minutes=delay_minutes,
        schedule_synthflow_callback=not is_terminal,
        is_terminal=is_terminal,
        campaign_name="Cold Lead",
        current_tier=current_tier,
        next_tier=next_tier,
    )


def get_new_lead_policy(
    current_tier: Optional[str],
    settings: Settings | None = None,
) -> TierTransitionPolicy:
    """
    Return the New Lead tier advancement policy for the given current tier.

    Stop condition: requires all NEW_VM_TIER_* settings to be resolved.
    Calls validate_for_new_lead_vm_policy() which raises ConfigError if
    any delay is unset. Do not proceed without all delays configured.
    """
    settings = settings or get_settings()
    settings.validate_for_new_lead_vm_policy()  # stop condition enforcement

    tier_map: dict[Optional[str], tuple[int, str]] = {
        None: (settings.new_vm_tier_none_delay_minutes, "0"),  # type: ignore[dict-item]
        "0": (settings.new_vm_tier_0_delay_minutes, "1"),  # type: ignore[dict-item]
        "1": (settings.new_vm_tier_1_delay_minutes, "2"),  # type: ignore[dict-item]
        "2": (0, "3"),
    }

    if current_tier not in tier_map:
        raise ValueError(
            f"Invalid New Lead tier: {current_tier!r}. "
            "Valid values: None, '0', '1', '2'."
        )

    delay_minutes, next_tier = tier_map[current_tier]
    is_terminal = next_tier == "3"

    return TierTransitionPolicy(
        delay_minutes=delay_minutes or 0,
        schedule_synthflow_callback=not is_terminal,
        is_terminal=is_terminal,
        campaign_name="New Lead",
        current_tier=current_tier,
        next_tier=next_tier,
    )


def get_tier_policy(
    campaign_name: str,
    current_tier: Optional[str],
    settings: Settings | None = None,
) -> TierTransitionPolicy:
    """
    Dispatch to the appropriate campaign policy by campaign_name.

    Supported campaigns: 'Cold Lead', 'New Lead'.
    Unknown campaigns surface as a ValueError (escalation trigger).
    """
    settings = settings or get_settings()
    name_lower = (campaign_name or "").strip().lower()

    if name_lower == "cold lead":
        return get_cold_lead_policy(current_tier, settings)
    if name_lower == "new lead":
        return get_new_lead_policy(current_tier, settings)

    raise ValueError(
        f"Unknown campaign type: {campaign_name!r}. "
        "Supported: 'Cold Lead', 'New Lead'."
    )


# ── Duplicate callback prevention ─────────────────────────────────────────────

def has_pending_callback(session: Session, entity_id: str) -> bool:
    """
    Return True if a pending or in-flight Synthflow callback already exists
    for the given entity_id.

    This prevents duplicate callbacks when a voicemail event is replayed
    or a tier job is retried. Callers must check this before scheduling.
    """
    from sqlalchemy import select

    from app.models.scheduled_job import ScheduledJob

    existing = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "synthflow_callback",
            ScheduledJob.entity_id == entity_id,
            ScheduledJob.status.in_(["pending", "claimed", "running"]),
        )
    ).first()

    if existing:
        logger.info(
            "has_pending_callback: duplicate detected | entity_id=%s job_id=%s status=%s",
            entity_id, existing.id, existing.status,
        )
    return existing is not None
