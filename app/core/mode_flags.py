"""
Runtime mode flags — DB-first, settings fallback.

All shadow/live toggles flow through get_mode_flags(session, settings).
Workers must call this instead of reading settings.shadow_mode_enabled or
settings.ghl_writes_enabled directly, so that dashboard changes take effect
immediately on the next job execution without a process restart.

Lookup chain (same as app_config):
  1. app_config table in Postgres  (runtime-editable via dashboard)
  2. Settings field of the same name (from .env / environment)
  3. Safe fallback (shadow/paused = safe defaults)

Usage in a worker job:
    from app.core.mode_flags import get_mode_flags
    flags = get_mode_flags(session, settings)
    if flags.system_paused:
        release_job_to_pending(session, job)
        return
    if flags.shadow_mode_enabled:
        # intercept outbound action
        ...

Pre-flight helper:
    from app.core.mode_flags import get_preflight_status
    checks = get_preflight_status(session, settings)
    # returns list of PreflightCheck with ok/warning/error status
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings

logger = logging.getLogger(__name__)


# ── ModeFlags dataclass ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModeFlags:
    """
    Snapshot of all operational mode flags at a point in time.

    Frozen so callers can't mutate a flag mid-job after reading it.
    Re-read at the start of each job to pick up dashboard changes.
    """
    shadow_mode_enabled: bool       # outbound calls / SMS / email
    ghl_write_mode: str             # "shadow" | "live"
    ghl_write_shadow_log_only: bool
    ghl_write_contact_fields: bool
    ghl_write_tasks: bool
    ghl_write_summary: bool
    ghl_write_campaign_state: bool
    ghl_write_finalization: bool
    system_paused: bool

    @property
    def ghl_writes_enabled(self) -> bool:
        """True when GHL should execute real API write calls."""
        return self.ghl_write_mode == "live"

    def as_dict(self) -> dict[str, Any]:
        return {
            "shadow_mode_enabled": self.shadow_mode_enabled,
            "ghl_write_mode": self.ghl_write_mode,
            "ghl_write_shadow_log_only": self.ghl_write_shadow_log_only,
            "ghl_write_contact_fields": self.ghl_write_contact_fields,
            "ghl_write_tasks": self.ghl_write_tasks,
            "ghl_write_summary": self.ghl_write_summary,
            "ghl_write_campaign_state": self.ghl_write_campaign_state,
            "ghl_write_finalization": self.ghl_write_finalization,
            "system_paused": self.system_paused,
            # derived
            "ghl_writes_enabled": self.ghl_writes_enabled,
        }


# ── Reader ────────────────────────────────────────────────────────────────────

def get_mode_flags(session: Session, settings: Settings) -> ModeFlags:
    """
    Read all mode flags from app_config (DB-first, settings fallback).

    Never raises — falls back to the safe shadow defaults on any DB error.
    This function deliberately avoids lru_cache so every call fetches the
    current value, ensuring dashboard changes propagate to the next job
    execution without a process restart.
    """
    from app.core.app_config import get_bool, get_str

    try:
        shadow_mode_enabled = get_bool(
            "shadow_mode_enabled", session, settings,
            fallback=getattr(settings, "shadow_mode_enabled", True),
        )
        ghl_write_mode = get_str(
            "ghl_write_mode", session, settings,
            fallback=getattr(settings, "ghl_write_mode", "shadow"),
        )
        ghl_write_shadow_log_only = get_bool(
            "ghl_write_shadow_log_only", session, settings,
            fallback=getattr(settings, "ghl_write_shadow_log_only", True),
        )
        ghl_write_contact_fields = get_bool(
            "ghl_write_contact_fields", session, settings,
            fallback=getattr(settings, "ghl_write_contact_fields", False),
        )
        ghl_write_tasks = get_bool(
            "ghl_write_tasks", session, settings,
            fallback=getattr(settings, "ghl_write_tasks", False),
        )
        ghl_write_summary = get_bool(
            "ghl_write_summary", session, settings,
            fallback=getattr(settings, "ghl_write_summary", False),
        )
        ghl_write_campaign_state = get_bool(
            "ghl_write_campaign_state", session, settings,
            fallback=getattr(settings, "ghl_write_campaign_state", False),
        )
        ghl_write_finalization = get_bool(
            "ghl_write_finalization", session, settings,
            fallback=getattr(settings, "ghl_write_finalization", False),
        )
        system_paused = get_bool(
            "system_paused", session, settings,
            fallback=False,
        )
    except Exception:
        logger.warning(
            "get_mode_flags: DB read failed — using safe shadow defaults",
            exc_info=True,
        )
        return _safe_shadow_defaults()

    return ModeFlags(
        shadow_mode_enabled=shadow_mode_enabled,
        ghl_write_mode=ghl_write_mode,
        ghl_write_shadow_log_only=ghl_write_shadow_log_only,
        ghl_write_contact_fields=ghl_write_contact_fields,
        ghl_write_tasks=ghl_write_tasks,
        ghl_write_summary=ghl_write_summary,
        ghl_write_campaign_state=ghl_write_campaign_state,
        ghl_write_finalization=ghl_write_finalization,
        system_paused=system_paused,
    )


def _safe_shadow_defaults() -> ModeFlags:
    """Return the safest possible defaults — everything in shadow, not paused."""
    return ModeFlags(
        shadow_mode_enabled=True,
        ghl_write_mode="shadow",
        ghl_write_shadow_log_only=True,
        ghl_write_contact_fields=True,
        ghl_write_tasks=True,
        ghl_write_summary=True,
        ghl_write_campaign_state=True,
        ghl_write_finalization=True,
        system_paused=False,
    )


# ── Pre-flight checks ─────────────────────────────────────────────────────────

@dataclass
class PreflightCheck:
    key: str
    label: str
    status: str   # "ok" | "warning" | "error"
    detail: str


def get_preflight_status(session: Session, settings: Settings) -> list[PreflightCheck]:
    """
    Return a list of pre-flight readiness checks required before going live.

    Used by the System Controls dashboard page to gate the go-live buttons.
    A check with status='error' must be resolved before enabling that mode.
    """
    checks: list[PreflightCheck] = []

    def _check(key: str, label: str, ok: bool, detail_ok: str, detail_fail: str, critical: bool = True) -> None:
        checks.append(PreflightCheck(
            key=key,
            label=label,
            status="ok" if ok else ("error" if critical else "warning"),
            detail=detail_ok if ok else detail_fail,
        ))

    # ── Credentials ──────────────────────────────────────────────────────────
    _check(
        "ghl_api_key", "GHL API Key",
        bool(settings.ghl_api_key),
        "Configured",
        "GHL_API_KEY is missing — GHL writes will fail",
    )
    _check(
        "synthflow_api_key", "Synthflow API Key",
        bool(settings.synthflow_api_key),
        "Configured",
        "SYNTHFLOW_API_KEY is missing — outbound calls will fail",
    )
    _check(
        "synthflow_launch_url", "Synthflow Launch URL",
        bool(settings.synthflow_launch_workflow_url),
        "Configured",
        "SYNTHFLOW_LAUNCH_WORKFLOW_URL is missing — outbound calls will fail",
    )

    # ── GHL field mappings ────────────────────────────────────────────────────
    _check(
        "ghl_field_ai_campaign", "GHL Field: AI Campaign",
        bool(settings.ghl_field_ai_campaign),
        f"'{settings.ghl_field_ai_campaign}'",
        "GHL_FIELD_AI_CAMPAIGN not set — campaign state writes will silently skip",
    )
    _check(
        "ghl_field_ai_campaign_value", "GHL Field: AI Campaign Value",
        bool(settings.ghl_field_ai_campaign_value),
        f"'{settings.ghl_field_ai_campaign_value}'",
        "GHL_FIELD_AI_CAMPAIGN_VALUE not set — voicemail tier mirror will silently skip",
    )
    _check(
        "ghl_field_mark_as_lead", "GHL Field: Mark as Lead",
        bool(settings.ghl_field_mark_as_lead),
        f"'{settings.ghl_field_mark_as_lead}'",
        "GHL_FIELD_MARK_AS_LEAD not set — lead flag writes will silently skip",
    )
    _check(
        "ghl_field_support_ticket_3", "GHL Field: Support Ticket #3 (task description)",
        bool(settings.ghl_field_support_ticket_3),
        f"'{settings.ghl_field_support_ticket_3}'",
        "GHL_FIELD_SUPPORT_TICKET_3 not set — task description writes will silently skip",
    )
    _check(
        "ghl_field_support_ticket_4", "GHL Field: Support Ticket #4 (student summary)",
        bool(settings.ghl_field_support_ticket_4),
        f"'{settings.ghl_field_support_ticket_4}'",
        "GHL_FIELD_SUPPORT_TICKET_4 not set — student summary writes will silently skip",
    )
    _check(
        "ghl_field_vm_sms_text", "GHL Field: VM SMS Text",
        bool(settings.ghl_field_vm_sms_text),
        f"'{settings.ghl_field_vm_sms_text}'",
        "GHL_FIELD_VM_SMS_TEXT not set — voicemail SMS field writes will silently skip",
    )
    _check(
        "ghl_field_vm_email_html", "GHL Field: VM Email Body",
        bool(settings.ghl_field_vm_email_html),
        f"'{settings.ghl_field_vm_email_html}'",
        "GHL_FIELD_VM_EMAIL_HTML not set — voicemail email writes will silently skip",
    )

    # ── Queue health (warning only — don't block go-live) ────────────────────
    try:
        from sqlalchemy import text
        row = session.execute(text("""
            SELECT COUNT(*) FROM scheduled_jobs
            WHERE status = 'pending'
              AND run_at <= NOW()
        """)).fetchone()
        overdue_count = int(row[0]) if row else 0
        _check(
            "queue_health", "Queue Health (overdue jobs)",
            overdue_count < 20,
            f"{overdue_count} overdue jobs (healthy)",
            f"{overdue_count} overdue jobs — investigate before going live",
            critical=False,
        )
    except Exception:
        checks.append(PreflightCheck(
            key="queue_health",
            label="Queue Health",
            status="warning",
            detail="Could not check queue health",
        ))

    try:
        from sqlalchemy import text
        row = session.execute(text("""
            SELECT COUNT(*) FROM exceptions WHERE status = 'open'
        """)).fetchone()
        open_exc = int(row[0]) if row else 0
        _check(
            "open_exceptions", "Open Exceptions",
            open_exc < 10,
            f"{open_exc} open exceptions",
            f"{open_exc} open exceptions — resolve critical ones before going live",
            critical=False,
        )
    except Exception:
        checks.append(PreflightCheck(
            key="open_exceptions",
            label="Open Exceptions",
            status="warning",
            detail="Could not check exception count",
        ))

    return checks
