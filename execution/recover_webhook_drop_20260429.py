"""
Recovery script: inject synthetic call_events and advance campaign state for
contacts whose Synthflow webhook was not received on 2026-04-29.

Background
----------
Between 14:10–15:30 UTC on 2026-04-29, Synthflow webhook delivery failed for 38
contacts affected by burst clustering before the slot-redistribution fix was
applied. The burst at 14:10–14:20 UTC (slots showing 15, 7, 18 concurrent calls)
overwhelmed Synthflow's HTTP step concurrency limit and silently dropped webhooks.

The Synthflow export (calls-files4.csv) shows outcomes for 5 contacts:
  2 completed, 2 failed, 1 no-answer.
The remaining 33 are treated as hangup_on_voicemail.

Two recovery paths
------------------
Path A — Synthflow record exists (calls-files4.csv):
  completed  → create CallEvent with recording_url + schedule run_call_analysis
  no-answer  → create CallEvent only (no downstream)
  failed     → create CallEvent only (no downstream)

Path B — No Synthflow record (hangup_on_voicemail):
  All completed launch_outbound_call jobs in the incident window with no matching
  call_event AND not handled in Path A.
  → create CallEvent (status=hangup_on_voicemail)
  → advance lead_state.ai_campaign_value to next tier (any tier: None/0/1/2)
  → schedule next launch_outbound_call if not terminal
    run_at = executed_at + policy_delay_minutes (slot-aligned, max 4 calls/5 min)
  → if terminal tier: run GHL finalization writes (shadow-gated)

Idempotency
-----------
- CallEvent rows: dedupe_key unique constraint prevents duplicates on re-run.
- Tier advancement: guarded by pending-job check and version-based UPDATE.
- Outbound scheduling: guarded by existing-pending-job check.

Usage
-----
  # Dry-run (default):
  python execution/recover_webhook_drop_20260429.py

  # Live — writes to DB:
  python execution/recover_webhook_drop_20260429.py --live

  # Override CSV path:
  python execution/recover_webhook_drop_20260429.py --live --csv /path/to/calls-files4.csv

Run on the server inside a worker container:
  # 1. Copy CSV to server:
  scp "C:\\Users\\keset\\Downloads\\calls-files4.csv" user@server:/opt/cora-recap-engine/tmp/calls-files4.csv

  # 2. Execute:
  docker compose exec worker-default python execution/recover_webhook_drop_20260429.py --live
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Incident window (UTC) — covers full April 29 UTC day; NOT EXISTS clause
# ensures only contacts with no matching call_event are recovered.
_INCIDENT_START = datetime(2026, 4, 29, 14, 0, 0, tzinfo=timezone.utc)
_INCIDENT_END   = datetime(2026, 4, 30, 0, 0, 0, tzinfo=timezone.utc)

# Slot cap constants (must match outbound_jobs.py)
_CALL_BATCH_SIZE    = 4
_CALL_SLOT_SECONDS  = 300
_SLOT_EPOCH         = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Default CSV path (when running inside the container)
_DEFAULT_CSV = Path("/app/tmp/calls-files4.csv")


# ── Phone normalisation ────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str | None:
    """Extract 10-digit US number from any common format."""
    if not raw:
        return None
    s = str(raw).strip()
    m = re.search(r"\((\+?\d+)\)", s)
    if m:
        s = m.group(1)
    digits = re.sub(r"[^\d]", "", s)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits if len(digits) == 10 else None


# ── Load Synthflow CSV ────────────────────────────────────────────────────────

def load_csv(csv_path: Path) -> dict[str, dict]:
    """
    Load calls-files4.csv into a dict keyed by normalized 10-digit phone.

    Row shape: {status, call_id, duration_s, timestamp, recording_url, error}
    Status values: completed | no-answer | failed
    """
    records: dict[str, dict] = {}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(csv_path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    phone = normalize_phone(row.get("To") or row.get("From", ""))
                    if not phone:
                        continue
                    status = (row.get("Call Status") or "").strip().lower()
                    if status not in ("completed", "no-answer", "failed"):
                        continue
                    call_id = (row.get("ID") or row.get("Call_ID") or "").strip()
                    records[phone] = {
                        "status":        status,
                        "call_id":       call_id,
                        "duration_s":    int(row.get("Duration (s)") or row.get("Duration") or 0),
                        "timestamp":     row.get("Timestamp", ""),
                        "recording_url": row.get("Recording Link") or row.get("Recording URL", ""),
                        "error":         row.get("Error", ""),
                    }
            logger.info("Loaded %d records from %s (encoding=%s)", len(records), csv_path, enc)
            return records
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Cannot decode {csv_path} — tried utf-8-sig, utf-8, latin-1")


# ── DB helpers ────────────────────────────────────────────────────────────────

_MISSING_WEBHOOK_SQL = """
SELECT
    RIGHT(REGEXP_REPLACE(sj.payload_json->>'phone_number', '[^0-9]', '', 'g'), 10) AS digits,
    sj.payload_json->>'contact_id'    AS contact_id,
    sj.payload_json->>'phone_number'  AS phone_number,
    sj.payload_json->>'campaign_name' AS campaign_name,
    sj.payload_json->>'lead_name'     AS lead_name,
    sj.updated_at                     AS executed_at,
    sj.run_at,
    sj.id                             AS job_id
FROM scheduled_jobs sj
WHERE sj.job_type   = 'launch_outbound_call'
  AND sj.status     = 'completed'
  AND sj.updated_at >= :start
  AND sj.updated_at <= :end
  AND sj.updated_at <= NOW() - INTERVAL '20 minutes'
  AND NOT EXISTS (
      SELECT 1 FROM call_events ce
      WHERE ce.contact_id = sj.payload_json->>'contact_id'
        AND ce.created_at >= sj.updated_at - INTERVAL '10 minutes'
        AND ce.created_at <= sj.updated_at + INTERVAL '7 days'
  )
ORDER BY sj.updated_at
"""


def load_missing_webhook_jobs(session) -> dict[str, dict]:
    """
    Query: all 2026-04-29 incident-window launch_outbound_call jobs with no
    matching call_event. Returns dict keyed by normalized 10-digit phone.
    """
    from sqlalchemy import text

    rows = session.execute(
        text(_MISSING_WEBHOOK_SQL),
        {"start": _INCIDENT_START, "end": _INCIDENT_END},
    ).fetchall()
    result: dict[str, dict] = {}
    for r in rows:
        digits = str(r[0]) if r[0] else None
        if digits and len(digits) == 10:
            result[digits] = {
                "contact_id":    r[1],
                "phone_number":  r[2],
                "campaign_name": r[3] or "Cold Lead",
                "lead_name":     r[4] or "",
                "executed_at":   r[5],
                "run_at":        r[6],
                "job_id":        r[7],
            }
    logger.info("Missing-webhook jobs in DB: %d", len(result))
    return result


def _create_call_event_row(
    session,
    *,
    call_id: str,
    contact_id: str | None,
    status: str,
    campaign_name: str,
    executed_at: datetime,
    duration_s: int = 0,
    recording_url: str = "",
    end_call_reason: str = "",
    source_label: str = "webhook_recovery_20260429",
) -> bool:
    """
    Insert a CallEvent row. Idempotent via dedupe_key unique constraint.
    Returns True if a new row was created, False if it already existed.
    """
    from sqlalchemy import select

    from app.models.call_event import CallEvent

    dedupe_key = f"{call_id}:process_call_event"
    existing = session.scalars(
        select(CallEvent).where(CallEvent.dedupe_key == dedupe_key)
    ).first()
    if existing:
        logger.debug("call_event already exists | call_id=%s", call_id)
        return False

    event = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        direction="outbound",
        status=status,
        end_call_reason=end_call_reason or None,
        duration_seconds=duration_s or None,
        recording_url=recording_url or None,
        start_time_utc=executed_at,
        call_started_at=executed_at,
        voice_agent="ColdLead",
        campaign_name=campaign_name,
        dedupe_key=dedupe_key,
        raw_payload_json={
            "source":        source_label,
            "call_id":       call_id,
            "contact_id":    contact_id,
            "status":        status,
            "campaign_name": campaign_name,
        },
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(event)
    session.flush()
    logger.info(
        "created call_event | call_id=%s contact_id=%s status=%s",
        call_id, contact_id, status,
    )
    return True


def _schedule_ai_analysis(session, call_id: str, contact_id: str | None, call_event_id: str, settings) -> None:
    """Schedule run_call_analysis on the AI queue."""
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session,
        job_type="run_call_analysis",
        entity_type="call",
        entity_id=call_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={
            "call_id":       call_id,
            "contact_id":    contact_id,
            "call_event_id": call_event_id,
            "campaign_name": "Cold Lead",
            "source":        "webhook_recovery_20260429",
        },
    )
    logger.info("scheduled run_call_analysis | call_id=%s contact_id=%s", call_id, contact_id)


def _slot_aligned_run_at(session, base_time: datetime, delay_minutes: int) -> datetime:
    """
    Compute a slot-aware run_at starting from base_time + delay_minutes.
    Rounds to the nearest slot boundary and assigns the next open slot using
    the pending job count — same logic as _slot_aware_run_at in voicemail_jobs.py.
    """
    from sqlalchemy import func, select

    from app.models.scheduled_job import ScheduledJob

    raw = base_time + timedelta(minutes=delay_minutes)
    slot_idx = int((raw - _SLOT_EPOCH).total_seconds() / _CALL_SLOT_SECONDS)
    window_start = _SLOT_EPOCH + timedelta(seconds=slot_idx * _CALL_SLOT_SECONDS)

    pending = session.scalar(
        select(func.count()).select_from(ScheduledJob).where(
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status.in_(["pending", "claimed"]),
            ScheduledJob.run_at >= window_start,
            ScheduledJob.run_at < window_start + timedelta(hours=4),
        )
    ) or 0
    slot = pending // _CALL_BATCH_SIZE
    return window_start + timedelta(seconds=slot * _CALL_SLOT_SECONDS)


def _advance_tier_and_schedule(
    session,
    *,
    contact_id: str,
    phone_number: str,
    campaign_name: str,
    lead_name: str,
    executed_at: datetime,
    settings,
    live: bool,
) -> None:
    """
    Advance lead_state to the next voicemail tier and schedule the follow-up call.
    Handles any current tier (None, '0', '1', '2').

    Guards:
    - Skips if already at terminal tier
    - Skips if do_not_call=True
    - Skips if a pending launch_outbound_call already exists for this contact
    - Uses optimistic concurrency (version check) on lead_state update
    """
    from sqlalchemy import select, update

    from app.models.lead_state import LeadState
    from app.models.scheduled_job import ScheduledJob
    from app.services.tier_policy import get_tier_policy
    from app.worker.scheduler import schedule_job

    _TIER_SEQUENCE = [None, "0", "1", "2", "3"]
    _TERMINAL_TIER = "3"

    ls = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()

    if ls is None:
        logger.warning("lead_state not found | contact_id=%s — skipping", contact_id)
        return

    if ls.do_not_call:
        logger.info("do_not_call=True — skipping | contact_id=%s", contact_id)
        return

    current_tier = ls.ai_campaign_value
    if current_tier == _TERMINAL_TIER:
        logger.info("already terminal | contact_id=%s", contact_id)
        return

    try:
        idx = _TIER_SEQUENCE.index(current_tier)
        next_tier = _TIER_SEQUENCE[idx + 1]
    except (ValueError, IndexError):
        logger.warning("unknown tier %r | contact_id=%s — skipping", current_tier, contact_id)
        return

    policy = get_tier_policy(campaign_name, current_tier, settings, session)

    # Check for existing pending outbound job (deduplication guard)
    pending = session.scalar(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status.in_(["pending", "claimed"]),
        )
    )
    if pending:
        logger.info("pending job already exists — skipping | contact_id=%s", contact_id)
        return

    # Compute slot-aware run_at (only needed when not terminal)
    run_at = None
    delay_minutes = 0
    if not policy.is_terminal and policy.schedule_synthflow_callback:
        delay_minutes = policy.delay_minutes
        run_at = _slot_aligned_run_at(session, executed_at, delay_minutes)

    logger.info(
        "%s tier %r→%r | contact_id=%s executed_at=%s delay=%dm run_at=%s terminal=%s",
        "[DRY-RUN]" if not live else "[LIVE]",
        current_tier, next_tier, contact_id,
        executed_at.isoformat(), delay_minutes,
        run_at.isoformat() if run_at else "N/A",
        policy.is_terminal,
    )

    if not live:
        return

    # Advance lead_state (optimistic concurrency)
    result = session.execute(
        update(LeadState)
        .where(
            LeadState.contact_id == contact_id,
            LeadState.version == ls.version,
        )
        .values(
            ai_campaign_value=next_tier,
            last_call_status="hangup_on_voicemail",
            version=ls.version + 1,
            updated_at=datetime.now(tz=timezone.utc),
        )
    )
    if result.rowcount == 0:
        logger.warning("version conflict — skipping | contact_id=%s", contact_id)
        return

    if run_at is not None:
        schedule_job(
            session=session,
            job_type="launch_outbound_call",
            entity_type="lead",
            entity_id=contact_id,
            run_at=run_at,
            payload={
                "phone_number":   phone_number,
                "lead_name":      lead_name,
                "campaign_name":  campaign_name,
                "contact_id":     contact_id,
                "source":         "webhook_recovery_20260429_hangup_vm",
                "correlation_id": contact_id,
            },
        )
    else:
        # Terminal tier — finalize (GHL writes are shadow-gated)
        from app.adapters.ghl import GHLClient
        from app.worker.jobs.crm_jobs import _resolve_to_field_ids

        ghl = GHLClient(settings=settings)
        label_updates: dict[str, str] = {}
        if settings.ghl_field_mark_as_lead:
            label_updates[settings.ghl_field_mark_as_lead] = "Yes"
        ai_label = settings.ghl_field_ai_campaign or "AI Campaign"
        label_updates[ai_label] = "No"
        field_updates = _resolve_to_field_ids(ghl, label_updates)
        if field_updates:
            ghl.update_contact_fields(contact_id=contact_id, field_updates=field_updates)

    session.flush()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(live: bool, csv_path: Path) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info(
        "recover_webhook_drop_20260429 starting | mode=%s csv=%s",
        mode, csv_path,
    )

    from sqlalchemy import select

    from app.config import get_settings
    from app.db import get_sync_session
    from app.models.call_event import CallEvent

    settings = get_settings()
    csv_records = load_csv(csv_path)

    counters = {
        "path_a_completed":    0,
        "path_a_noanswer":     0,
        "path_a_failed":       0,
        "path_a_no_db_match":  0,
        "path_b_hangup_vm":    0,
        "path_b_skipped":      0,
        "already_exists":      0,
        "errors":              0,
    }

    with get_sync_session() as session:
        db_jobs = load_missing_webhook_jobs(session)
        csv_matched_phones: set[str] = set()

        # ── PATH A: Process Synthflow CSV records ─────────────────────────────
        logger.info("=== PATH A: Processing %d Synthflow records ===", len(csv_records))

        for csv_phone, sf in csv_records.items():
            db_job = db_jobs.get(csv_phone)
            if db_job is None:
                logger.debug("csv phone %s not in missing-webhook jobs — skipping", csv_phone)
                counters["path_a_no_db_match"] += 1
                continue

            csv_matched_phones.add(csv_phone)
            contact_id    = db_job["contact_id"]
            campaign_name = db_job["campaign_name"]
            executed_at   = db_job["executed_at"]
            status        = sf["status"]
            call_id       = sf["call_id"] or f"recovery-{csv_phone}-20260429"

            logger.info(
                "%s PATH A | phone=%s contact_id=%s status=%s call_id=%s",
                "[DRY-RUN]" if not live else "[LIVE]",
                csv_phone, contact_id, status, call_id,
            )

            try:
                if not live:
                    if status == "completed":
                        counters["path_a_completed"] += 1
                    elif status == "no-answer":
                        counters["path_a_noanswer"] += 1
                    else:
                        counters["path_a_failed"] += 1
                    continue

                created = _create_call_event_row(
                    session,
                    call_id=call_id,
                    contact_id=contact_id,
                    status=status,
                    campaign_name=campaign_name,
                    executed_at=executed_at,
                    duration_s=sf["duration_s"],
                    recording_url=sf["recording_url"],
                    end_call_reason=sf["error"] or None,
                )

                if not created:
                    counters["already_exists"] += 1
                    continue

                if status == "completed":
                    ce = session.scalars(
                        select(CallEvent).where(
                            CallEvent.dedupe_key == f"{call_id}:process_call_event"
                        )
                    ).first()
                    if ce:
                        _schedule_ai_analysis(session, call_id, contact_id, ce.id, settings)
                    counters["path_a_completed"] += 1
                elif status == "no-answer":
                    counters["path_a_noanswer"] += 1
                elif status == "failed":
                    counters["path_a_failed"] += 1

                session.flush()

            except Exception as exc:
                logger.exception("PATH A error | phone=%s: %s", csv_phone, exc)
                session.rollback()
                counters["errors"] += 1

        # ── PATH B: Hangup-on-voicemail (not in CSV) ──────────────────────────
        hangup_jobs = {
            phone: job
            for phone, job in db_jobs.items()
            if phone not in csv_matched_phones
        }
        logger.info("=== PATH B: %d hangup-on-voicemail contacts ===", len(hangup_jobs))

        for phone, job in hangup_jobs.items():
            contact_id    = job["contact_id"]
            campaign_name = job["campaign_name"]
            executed_at   = job["executed_at"]
            call_id       = f"recovery-hangup-{contact_id}-20260429"

            logger.info(
                "%s PATH B | phone=%s contact_id=%s executed_at=%s",
                "[DRY-RUN]" if not live else "[LIVE]",
                phone, contact_id,
                executed_at.isoformat() if executed_at else "?",
            )

            try:
                if not live:
                    counters["path_b_hangup_vm"] += 1
                    continue

                created = _create_call_event_row(
                    session,
                    call_id=call_id,
                    contact_id=contact_id,
                    status="hangup_on_voicemail",
                    campaign_name=campaign_name,
                    executed_at=executed_at,
                )

                if not created:
                    counters["already_exists"] += 1
                    continue

                _advance_tier_and_schedule(
                    session,
                    contact_id=contact_id,
                    phone_number=job["phone_number"],
                    campaign_name=campaign_name,
                    lead_name=job["lead_name"],
                    executed_at=executed_at,
                    settings=settings,
                    live=live,
                )
                counters["path_b_hangup_vm"] += 1
                session.flush()

            except Exception as exc:
                logger.exception(
                    "PATH B error | phone=%s contact_id=%s: %s", phone, contact_id, exc
                )
                session.rollback()
                counters["errors"] += 1

        if live:
            session.commit()
            logger.info("Committed.")

    # Summary
    logger.info("=== SUMMARY | mode=%s ===", mode)
    logger.info("  Path A — completed (AI queued):  %d", counters["path_a_completed"])
    logger.info("  Path A — no-answer (logged):     %d", counters["path_a_noanswer"])
    logger.info("  Path A — failed (logged):        %d", counters["path_a_failed"])
    logger.info("  Path A — no DB match (skipped):  %d", counters["path_a_no_db_match"])
    logger.info("  Path B — hangup_on_voicemail:    %d", counters["path_b_hangup_vm"])
    logger.info("  Already existed (deduped):        %d", counters["already_exists"])
    logger.info("  Errors:                           %d", counters["errors"])
    total_a = counters["path_a_completed"] + counters["path_a_noanswer"] + counters["path_a_failed"]
    logger.info("  Total processed: %d", total_a + counters["path_b_hangup_vm"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recover 2026-04-29 webhook-drop contacts"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write changes to DB (default: dry-run)",
    )
    parser.add_argument(
        "--csv",
        default=str(_DEFAULT_CSV),
        help=f"Path to calls-files4.csv (default: {_DEFAULT_CSV})",
    )
    args = parser.parse_args()
    main(live=args.live, csv_path=Path(args.csv))
