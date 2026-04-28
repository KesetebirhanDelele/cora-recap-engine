"""
Recovery script: reschedule Cold Lead tier-1 leads stuck from burst-related
webhook failures.

Targets tier-1 leads whose last completed launch_outbound_call job was in
one of the confirmed burst windows and who have no call_event back from
Synthflow and no pending retry scheduled.

Confirmed burst windows (UTC):
  - 2026-04-27 14:00–14:01  (9 AM CDT burst — 194 leads)
  - 2026-04-20 12:00–13:01  (7–8 AM CDT burst — 256 leads)

Each new job is placed using _compute_window_run_at() against the target
window start so calls spread at 10 per 2-minute slot — the same anti-burst
logic now applied at scheduling time.

Usage
-----
  # Dry run (default):
  python execution/reschedule_burst_tier1.py

  # Live — writes jobs, starting at 9 AM CDT tomorrow (2026-04-28):
  python execution/reschedule_burst_tier1.py --live

  # Override window start (ISO 8601 UTC):
  python execution/reschedule_burst_tier1.py --live --window-start 2026-04-29T14:00:00+00:00

Run inside the worker container:
  docker compose exec worker-default python execution/reschedule_burst_tier1.py --live
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Default: 9 AM CDT on 2026-04-28 = 14:00 UTC
_DEFAULT_WINDOW_START = datetime(2026, 4, 28, 14, 0, 0, tzinfo=timezone.utc)

_BURST_LEADS_SQL = """
SELECT
    ls.contact_id,
    last_job.payload_json,
    last_job.run_at AS burst_run_at,
    ls.ai_campaign_value
FROM lead_state ls
JOIN LATERAL (
    SELECT run_at, payload_json
    FROM scheduled_jobs
    WHERE entity_id = ls.contact_id
      AND job_type  = 'launch_outbound_call'
      AND status    = 'completed'
    ORDER BY run_at DESC
    LIMIT 1
) last_job ON (
    -- Apr 27 09:00 CDT burst
    (last_job.run_at >= '2026-04-27 14:00:00+00' AND last_job.run_at < '2026-04-27 14:01:00+00')
    -- Apr 20 07:00–08:00 CDT burst
    OR (last_job.run_at >= '2026-04-20 12:00:00+00' AND last_job.run_at < '2026-04-20 13:01:00+00')
)
WHERE ls.ai_campaign_value = '1'
  AND (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal'))
  AND ls.do_not_call IS NOT TRUE
  AND NOT EXISTS (
      SELECT 1 FROM scheduled_jobs
      WHERE entity_id = ls.contact_id
        AND job_type  = 'launch_outbound_call'
        AND status    IN ('pending', 'claimed')
  )
  AND NOT EXISTS (
      SELECT 1 FROM call_events ce
      WHERE ce.contact_id = ls.contact_id
        AND ce.created_at >= last_job.run_at
  )
ORDER BY ls.contact_id;
"""


def main(live: bool, window_start: datetime) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info(
        "reschedule_burst_tier1 starting | mode=%s window_start=%s",
        mode, window_start.isoformat(),
    )

    from datetime import timedelta

    from app.config import get_settings
    from app.db import get_sync_session
    from app.models.scheduled_job import ScheduledJob
    from app.worker.scheduler import schedule_job
    from sqlalchemy import func, select, text

    _CALL_BATCH_SIZE = 10
    _CALL_SLOT_SECONDS = 120

    def _compute_window_run_at(session, ws):
        pending = session.scalar(
            select(func.count()).select_from(ScheduledJob).where(
                ScheduledJob.job_type == "launch_outbound_call",
                ScheduledJob.status.in_(["pending", "claimed"]),
                ScheduledJob.run_at >= ws,
                ScheduledJob.run_at < ws + timedelta(hours=4),
            )
        ) or 0
        slot = pending // _CALL_BATCH_SIZE
        return ws + timedelta(seconds=slot * _CALL_SLOT_SECONDS)

    settings = get_settings()

    with get_sync_session() as session:
        rows = session.execute(text(_BURST_LEADS_SQL)).fetchall()
        logger.info("Burst-affected tier-1 leads found: %d", len(rows))

        if not rows:
            logger.info("Nothing to do.")
            return

        succeeded = 0
        failed = 0

        for row in rows:
            contact_id  = row[0]
            old_payload = row[1] or {}
            burst_run_at = row[2]

            payload = {
                "phone_number":  old_payload.get("phone_number") or contact_id,
                "lead_name":     old_payload.get("lead_name", ""),
                "campaign_name": old_payload.get("campaign_name", "Cold Lead"),
                "contact_id":    contact_id,
                "source":        "burst_recovery",
                "correlation_id": old_payload.get("correlation_id", contact_id),
            }

            try:
                if live:
                    run_at = _compute_window_run_at(session, window_start)
                    schedule_job(
                        session=session,
                        job_type="launch_outbound_call",
                        entity_type="lead",
                        entity_id=contact_id,
                        run_at=run_at,
                        payload=payload,
                    )
                    # Flush so the next _compute_window_run_at sees this job
                    session.flush()
                    logger.info(
                        "scheduled | contact_id=%s run_at=%s burst_was=%s",
                        contact_id, run_at.isoformat(), burst_run_at,
                    )
                else:
                    run_at = _compute_window_run_at(session, window_start)
                    logger.info(
                        "[dry-run] would schedule | contact_id=%s run_at=%s",
                        contact_id, run_at.isoformat(),
                    )

                succeeded += 1

            except Exception as exc:
                logger.exception("failed | contact_id=%s: %s", contact_id, exc)
                session.rollback()
                failed += 1

        if live:
            session.commit()

        logger.info(
            "Done | mode=%s succeeded=%d failed=%d window_start=%s",
            mode, succeeded, failed, window_start.isoformat(),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reschedule burst-stuck tier-1 Cold Lead leads"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write jobs to DB (default: dry-run)",
    )
    parser.add_argument(
        "--window-start",
        default=None,
        help="Window start in ISO 8601 UTC (default: 2026-04-28T14:00:00+00:00)",
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
