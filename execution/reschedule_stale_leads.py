"""
Re-enqueue stale active leads for their next outbound call.

Runs after finalize_stale_leads.py has closed DNC + campaign-off leads.
Targets leads that are genuinely active (AI Campaign still on) but have
no pending launch_outbound_call — they fell off the queue due to the pre-live
scheduling gap (April 13–16) where GHL messages completed but no next call
was enqueued.

Eligibility (all conditions must be true):
  - status NOT IN ('closed', 'terminal')
  - do_not_call IS NOT TRUE
  - ai_campaign_value NOT IN ('2', '3')  — tier 0, 1, or NULL
  - ai_campaign NOT IN ('No', 'False', '0')  — campaign still active
  - no pending/claimed/running launch_outbound_call in scheduled_jobs

Each eligible lead gets one new launch_outbound_call job scheduled using
the same _compute_window_run_at() slot logic as the live scheduler:
  max 4 calls per 5-minute slot, staggered 75 s apart within each slot.

Usage
-----
  # Dry-run (default — prints run_at for each lead, no writes):
  docker compose exec worker-default python execution/reschedule_stale_leads.py

  # Live — writes jobs to scheduled_jobs:
  docker compose exec worker-default python execution/reschedule_stale_leads.py --live

  # Override window start (default: 2026-04-30 14:00 UTC = 9 AM CDT):
  docker compose exec worker-default python execution/reschedule_stale_leads.py --live \\
      --window-start 2026-04-30T14:00:00+00:00
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Default: 9 AM CDT on 2026-04-30 = 14:00 UTC
_DEFAULT_WINDOW_START = datetime(2026, 4, 30, 14, 0, 0, tzinfo=timezone.utc)

# Must match outbound_jobs.py exactly
_CALL_BATCH_SIZE = 4
_CALL_SLOT_SECONDS = 300
_CALL_WITHIN_SLOT_SPACING = _CALL_SLOT_SECONDS // _CALL_BATCH_SIZE  # 75 s

_ELIGIBLE_SQL = """
WITH pending AS (
    SELECT entity_id
    FROM scheduled_jobs
    WHERE status IN ('pending', 'claimed', 'running')
      AND job_type = 'launch_outbound_call'
    GROUP BY entity_id
),
last_payload AS (
    SELECT DISTINCT ON (entity_id)
        entity_id,
        payload_json
    FROM scheduled_jobs
    WHERE job_type = 'launch_outbound_call'
      AND status   IN ('completed', 'failed', 'cancelled')
    ORDER BY entity_id, run_at DESC
)
SELECT
    ls.contact_id,
    ls.campaign_name,
    ls.ai_campaign_value  AS vm_tier,
    lp.payload_json       AS last_payload
FROM lead_state ls
LEFT JOIN pending    p  ON p.entity_id  = ls.contact_id
LEFT JOIN last_payload lp ON lp.entity_id = ls.contact_id
WHERE
    (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal'))
    AND ls.do_not_call IS NOT TRUE
    AND (ls.ai_campaign_value IS NULL OR ls.ai_campaign_value NOT IN ('2', '3'))
    AND LOWER(TRIM(COALESCE(ls.ai_campaign, 'yes'))) NOT IN ('no', 'false', '0')
    AND p.entity_id IS NULL
ORDER BY ls.contact_id
"""


def _compute_window_run_at(session, window_start: datetime) -> datetime:
    """Slot-aware run_at — mirrors outbound_jobs._compute_window_run_at() exactly."""
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
    within_slot = (pending % _CALL_BATCH_SIZE) * _CALL_WITHIN_SLOT_SPACING
    return window_start + timedelta(seconds=slot * _CALL_SLOT_SECONDS + within_slot)


def main(live: bool, window_start: datetime) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info(
        "reschedule_stale_leads starting | mode=%s window_start=%s",
        mode, window_start.isoformat(),
    )

    from sqlalchemy import text

    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job

    with get_sync_session() as session:
        rows = session.execute(text(_ELIGIBLE_SQL)).fetchall()

    logger.info("Eligible stale leads to reschedule: %d", len(rows))
    if not rows:
        logger.info("Nothing to do.")
        return

    succeeded = 0
    skipped = 0
    failed = 0

    with get_sync_session() as session:
        for row in rows:
            contact_id    = row.contact_id
            campaign_name = row.campaign_name or "Cold Lead"
            vm_tier       = row.vm_tier
            last_payload  = row.last_payload or {}

            # Build job payload — inherit lead_name from last known job if available
            payload = {
                "phone_number":  last_payload.get("phone_number") or contact_id,
                "lead_name":     last_payload.get("lead_name", ""),
                "campaign_name": last_payload.get("campaign_name") or campaign_name,
                "contact_id":    contact_id,
                "source":        "stale_recovery_20260429",
                "correlation_id": last_payload.get("correlation_id", contact_id),
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
                    session.flush()
                    logger.info(
                        "scheduled | contact_id=%s campaign=%s tier=%s run_at=%s",
                        contact_id, campaign_name, vm_tier, run_at.isoformat(),
                    )
                else:
                    logger.info(
                        "[dry-run] would schedule | contact_id=%s campaign=%s tier=%s run_at=%s",
                        contact_id, campaign_name, vm_tier, run_at.isoformat(),
                    )

                succeeded += 1

            except Exception as exc:
                logger.exception("failed | contact_id=%s: %s", contact_id, exc)
                session.rollback()
                failed += 1

        if live:
            session.commit()

    logger.info(
        "Done | mode=%s succeeded=%d skipped=%d failed=%d window_start=%s",
        mode, succeeded, skipped, failed, window_start.isoformat(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-enqueue stale active leads")
    parser.add_argument("--live", action="store_true", help="Write jobs (default: dry-run)")
    parser.add_argument(
        "--window-start", default=None,
        help="Window start ISO 8601 UTC (default: 2026-04-30T14:00:00+00:00)",
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
