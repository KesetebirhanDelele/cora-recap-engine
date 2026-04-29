"""
Recovery script: inject synthetic call_events and advance campaign state for
contacts whose Synthflow webhook was not received on 2026-04-28.

Background
----------
Between 14:00–15:26 UTC on 2026-04-28 Synthflow webhook delivery dropped to
59–84% under burst load (50 calls/10-min slot). A second drop at 21:10 UTC
(0% for 10 calls) affected additional contacts. In total ~128 launch_outbound_call
jobs completed with no corresponding call_events row received.

The Synthflow export (calls-files.csv) shows the outcome for calls where
Synthflow has a record (completed / no-answer / failed). Contacts with a
completed job but no Synthflow record are treated as hangup_on_voicemail.

Two recovery paths
------------------
Path A — Synthflow record exists (calls-files.csv):
  completed  → create CallEvent with recording_url + schedule run_call_analysis
  no-answer  → create CallEvent only (same as normal pipeline — no downstream)
  failed     → create CallEvent only (same as normal pipeline — no downstream)

Path B — No Synthflow record (hangup_on_voicemail):
  All completed launch_outbound_call jobs from 2026-04-28 with no matching
  call_event AND not handled in Path A.
  → create CallEvent (status=hangup_on_voicemail)
  → advance lead_state.ai_campaign_value 1→2 (if still at 1, no pending job)
  → schedule tier-2 launch_outbound_call at:
       run_at = original_executed_at + cold_vm_tier_1_delay_minutes
    (preserves the original scheduling intent; applied with anti-burst slot)

Idempotency
-----------
- CallEvent rows: dedupe_key unique constraint prevents duplicates on re-run.
- Tier advancement: guarded by pending-job check and version-based UPDATE.
- Tier-2 scheduling: guarded by existing-pending-job check.

Usage
-----
  # Dry-run (default — prints what would happen, writes nothing):
  python execution/recover_webhook_drop_20260428.py

  # Live — writes to DB:
  python execution/recover_webhook_drop_20260428.py --live

  # Override CSV path (default: tmp/calls-files.csv inside the container):
  python execution/recover_webhook_drop_20260428.py --live --csv /path/to/calls-files.csv

Run on the server inside a worker container:
  # 1. Copy CSV to server:
  scp "C:\\Users\\keset\\Downloads\\calls-files.csv" user@server:/opt/cora-recap-engine/tmp/calls-files.csv

  # 2. Execute:
  docker compose exec worker-default python execution/recover_webhook_drop_20260428.py --live
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

# Date window for the webhook-drop incident (UTC)
_INCIDENT_START = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
_INCIDENT_END   = datetime(2026, 4, 29, 0, 0, 0, tzinfo=timezone.utc)

# Give Synthflow 20 min to deliver before declaring a webhook missing
_WEBHOOK_GRACE_MINUTES = 20

# Default CSV path (when running inside the container)
_DEFAULT_CSV = Path("/opt/cora-recap-engine/tmp/calls-files.csv")


# ── Phone normalisation ────────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str | None:
    """Extract 10-digit US number from any common format."""
    if not raw:
        return None
    s = str(raw).strip()
    # 'Name (+14841234567)' → extract from parens
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
    Load calls-files.csv into a dict keyed by normalized 10-digit phone.

    Row shape: {status, call_id, duration_s, timestamp, recording_url, error}
    Status values: completed | no-answer | failed
    """
    records: dict[str, dict] = {}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(csv_path, encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Synthflow uses 'To' as the called (contact) number
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
                        "duration_s":    int(row.get("Duration (s)") or 0),
                        "timestamp":     row.get("Timestamp", ""),
                        "recording_url": row.get("Recording Link", ""),
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
    REGEXP_REPLACE(sj.payload_json->>'phone_number', '[^0-9]', '', 'g') AS digits,
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
        AND ce.created_at <= sj.updated_at + INTERVAL '4 hours'
  )
ORDER BY sj.updated_at
"""


def load_missing_webhook_jobs(session) -> dict[str, dict]:
    """
    Query: all 2026-04-28 launch_outbound_call jobs with no matching call_event.
    Returns dict keyed by normalized 10-digit phone digits.
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
    source_label: str = "webhook_recovery_20260428",
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
            "source":       source_label,
            "call_id":      call_id,
            "contact_id":   contact_id,
            "status":       status,
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
    """Schedule run_call_analysis on the AI queue (same as _route_to_call_through)."""
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
            "source":        "webhook_recovery_20260428",
        },
    )
    logger.info("scheduled run_call_analysis | call_id=%s contact_id=%s", call_id, contact_id)


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
    Advance lead_state from tier-1 to tier-2 and schedule the tier-2
    launch_outbound_call at: executed_at + cold_vm_tier_1_delay_minutes.

    Guards:
    - Only advances if ai_campaign_value == '1'
    - Skips if a pending launch_outbound_call already exists for this contact
    - Uses optimistic concurrency (version check) on lead_state update
    """
    from sqlalchemy import select, text, update
    from app.models.lead_state import LeadState
    from app.models.scheduled_job import ScheduledJob
    from app.worker.scheduler import schedule_job

    # Check lead_state
    ls = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()

    if ls is None:
        logger.warning("lead_state not found | contact_id=%s — skipping tier advance", contact_id)
        return

    if ls.do_not_call:
        logger.info("do_not_call=True — skipping | contact_id=%s", contact_id)
        return

    current_tier = ls.ai_campaign_value
    if current_tier != "1":
        logger.info(
            "contact not at tier-1 (tier=%r) — skipping | contact_id=%s",
            current_tier, contact_id,
        )
        return

    # Check for existing pending tier-2 job
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

    # Calculate tier-2 run_at from original call time
    delay_minutes = getattr(settings, "cold_vm_tier_1_delay_minutes", 2880)
    run_at = executed_at + timedelta(minutes=delay_minutes)

    logger.info(
        "%s tier 1→2 | contact_id=%s executed_at=%s delay=%dm run_at=%s",
        "[DRY-RUN]" if not live else "[LIVE]",
        contact_id,
        executed_at.isoformat(),
        delay_minutes,
        run_at.isoformat(),
    )

    if not live:
        return

    # Advance lead_state tier
    result = session.execute(
        update(LeadState)
        .where(
            LeadState.contact_id == contact_id,
            LeadState.version == ls.version,
        )
        .values(
            ai_campaign_value="2",
            last_call_status="hangup_on_voicemail",
            version=ls.version + 1,
            updated_at=datetime.now(tz=timezone.utc),
        )
    )
    if result.rowcount == 0:
        logger.warning("lead_state version conflict — skipping | contact_id=%s", contact_id)
        return

    # Schedule tier-2 launch_outbound_call
    schedule_job(
        session=session,
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        run_at=run_at,
        payload={
            "phone_number":  phone_number,
            "lead_name":     lead_name,
            "campaign_name": campaign_name,
            "contact_id":    contact_id,
            "source":        "webhook_recovery_20260428_hangup_vm",
            "correlation_id": contact_id,
        },
    )
    session.flush()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(live: bool, csv_path: Path) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info(
        "recover_webhook_drop_20260428 starting | mode=%s csv=%s",
        mode, csv_path,
    )

    from app.config import get_settings
    from app.db import get_sync_session
    from sqlalchemy import select
    from app.models.call_event import CallEvent

    settings = get_settings()
    csv_records = load_csv(csv_path)  # norm_phone → synthflow row

    counters = {
        "path_a_completed": 0,
        "path_a_noanswer":  0,
        "path_a_failed":    0,
        "path_a_no_db_match": 0,
        "path_b_hangup_vm": 0,
        "path_b_skipped":   0,
        "already_exists":   0,
        "errors":           0,
    }

    with get_sync_session() as session:
        # Pre-load all missing-webhook jobs from DB (keyed by 10-digit phone)
        db_jobs = load_missing_webhook_jobs(session)

        # Track which DB jobs were matched to CSV records
        csv_matched_phones: set[str] = set()

        # ── PATH A: Process Synthflow CSV records ─────────────────────────────
        logger.info("=== PATH A: Processing %d Synthflow records ===", len(csv_records))

        for csv_phone, sf in csv_records.items():
            db_job = db_jobs.get(csv_phone)
            if db_job is None:
                # CSV record doesn't match a missing-webhook job — already processed
                # or belongs to a different date/campaign. Skip silently.
                logger.debug(
                    "csv phone %s not in missing-webhook jobs — skipping", csv_phone
                )
                counters["path_a_no_db_match"] += 1
                continue

            csv_matched_phones.add(csv_phone)
            contact_id    = db_job["contact_id"]
            campaign_name = db_job["campaign_name"]
            executed_at   = db_job["executed_at"]
            status        = sf["status"]
            call_id       = sf["call_id"] or f"recovery-{csv_phone}-20260428"

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

                # Create call_event
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
                    # Look up the newly created call_event id
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
        logger.info(
            "=== PATH B: %d hangup-on-voicemail contacts ===", len(hangup_jobs)
        )

        for phone, job in hangup_jobs.items():
            contact_id    = job["contact_id"]
            campaign_name = job["campaign_name"]
            executed_at   = job["executed_at"]
            call_id       = f"recovery-hangup-{contact_id}-20260428"

            logger.info(
                "%s PATH B | phone=%s contact_id=%s executed_at=%s",
                "[DRY-RUN]" if not live else "[LIVE]",
                phone, contact_id, executed_at.isoformat() if executed_at else "?",
            )

            try:
                if not live:
                    counters["path_b_hangup_vm"] += 1
                    continue

                # Create call_event
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

                # Advance tier and schedule tier-2
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
                logger.exception("PATH B error | phone=%s contact_id=%s: %s", phone, contact_id, exc)
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
        description="Recover 2026-04-28 webhook-drop contacts"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write changes to DB (default: dry-run)",
    )
    parser.add_argument(
        "--csv",
        default=str(_DEFAULT_CSV),
        help=f"Path to calls-files.csv (default: {_DEFAULT_CSV})",
    )
    args = parser.parse_args()
    main(live=args.live, csv_path=Path(args.csv))
