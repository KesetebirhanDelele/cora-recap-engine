"""
Recovery script: reschedule Cold Lead tier-0 and tier-1 leads whose most
recent Synthflow call returned status='failed' and who have no pending retry.

A 'failed' call_event means Synthflow could not connect (not a voicemail,
not a timeout — a hard failure from the Synthflow API). The voicemail tier
must NOT advance for these leads; we simply retry the same call.

Targets:
  - campaign_name = 'Cold Lead'
  - ai_campaign_value IN ('0', '1')
  - most recent call_event.status = 'failed'
  - no pending or claimed launch_outbound_call job
  - not closed, not do_not_call

As of 2026-04-28 the confirmed affected leads are:
  Tier 0: 13 leads (failures between 2026-04-21 and 2026-04-27)
  Tier 1:  1 lead  (+19103165377, failure 2026-04-27)

Usage
-----
  # Dry run (default — prints what would be scheduled):
  python execution/reschedule_failed_calls.py

  # Live — writes jobs, defaulting to 9 AM CDT 2026-04-29:
  python execution/reschedule_failed_calls.py --live

  # Override window start (ISO 8601 UTC):
  python execution/reschedule_failed_calls.py --live --window-start 2026-04-30T14:00:00+00:00

Run inside the worker container:
  docker compose exec worker-default python execution/reschedule_failed_calls.py --live
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Default: 9 AM CDT on 2026-04-29 = 14:00 UTC
_DEFAULT_WINDOW_START = datetime(2026, 4, 29, 14, 0, 0, tzinfo=timezone.utc)

_CALL_BATCH_SIZE = 10
_CALL_SLOT_SECONDS = 120  # 2 minutes between slots

# Leads where the most recent call_event is 'failed' and no retry is pending.
# Uses a LATERAL join so 'failed' must be the LATEST call event, not just any.
_AFFECTED_LEADS_SQL = """
SELECT
    ls.contact_id,
    ls.ai_campaign_value,
    last_completed_job.payload_json
FROM lead_state ls
-- Payload comes from the last successfully dispatched job (may be NULL for
-- leads whose very first attempt failed before any job completed).
LEFT JOIN LATERAL (
    SELECT payload_json
    FROM scheduled_jobs
    WHERE entity_id = ls.contact_id
      AND job_type  = 'launch_outbound_call'
      AND status    = 'completed'
    ORDER BY run_at DESC
    LIMIT 1
) last_completed_job ON true
-- Only include leads whose MOST RECENT call event was a hard failure.
JOIN LATERAL (
    SELECT status
    FROM call_events
    WHERE contact_id = ls.contact_id
    ORDER BY created_at DESC
    LIMIT 1
) last_call ON last_call.status = 'failed'
WHERE ls.campaign_name = 'Cold Lead'
  AND ls.ai_campaign_value IN ('0', '1')
  AND (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal'))
  AND ls.do_not_call IS NOT TRUE
  AND NOT EXISTS (
      SELECT 1 FROM scheduled_jobs
      WHERE entity_id = ls.contact_id
        AND job_type  = 'launch_outbound_call'
        AND status    IN ('pending', 'claimed')
  )
ORDER BY ls.ai_campaign_value, ls.contact_id;
"""


def _compute_window_run_at(session, window_start: datetime) -> datetime:
    from sqlalchemy import func, select

    from app.models.scheduled_job import ScheduledJob

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


def main(live: bool, window_start: datetime) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info(
        "reschedule_failed_calls starting | mode=%s window_start=%s",
        mode, window_start.isoformat(),
    )

    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job
    from sqlalchemy import text

    with get_sync_session() as session:
        rows = session.execute(text(_AFFECTED_LEADS_SQL)).fetchall()
        logger.info("Affected leads found: %d", len(rows))

        if not rows:
            logger.info("Nothing to do.")
            return

        succeeded = 0
        failed_count = 0

        for row in rows:
            contact_id   = row[0]
            vm_tier      = row[1]
            old_payload  = row[2] or {}

            payload = {
                "phone_number":  old_payload.get("phone_number") or contact_id,
                "lead_name":     old_payload.get("lead_name", ""),
                "campaign_name": old_payload.get("campaign_name", "Cold Lead"),
                "contact_id":    contact_id,
                "source":        "failed_call_recovery",
                "correlation_id": old_payload.get("correlation_id", contact_id),
            }

            try:
                run_at = _compute_window_run_at(session, window_start)

                if live:
                    schedule_job(
                        session=session,
                        job_type="launch_outbound_call",
                        entity_type="lead",
                        entity_id=contact_id,
                        run_at=run_at,
                        payload=payload,
                    )
                    # Flush so the next slot calculation sees this job
                    session.flush()
                    logger.info(
                        "scheduled | contact_id=%s vm_tier=%s run_at=%s",
                        contact_id, vm_tier, run_at.isoformat(),
                    )
                else:
                    logger.info(
                        "[dry-run] would schedule | contact_id=%s vm_tier=%s run_at=%s",
                        contact_id, vm_tier, run_at.isoformat(),
                    )

                succeeded += 1

            except Exception as exc:
                logger.exception("failed | contact_id=%s: %s", contact_id, exc)
                session.rollback()
                failed_count += 1

        if live:
            session.commit()

        logger.info(
            "Done | mode=%s succeeded=%d failed=%d window_start=%s",
            mode, succeeded, failed_count, window_start.isoformat(),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reschedule Cold Lead tier-0/1 leads with failed Synthflow calls"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write jobs to DB (default: dry-run)",
    )
    parser.add_argument(
        "--window-start",
        default=None,
        help="Window start in ISO 8601 UTC (default: 2026-04-29T14:00:00+00:00)",
    )
    args = parser.parse_args()

    window_start = (
        datetime.fromisoformat(args.window_start)
        if args.window_start
        else _DEFAULT_WINDOW_START
    )
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)

    main(live=args.live, window_start=window_start)
