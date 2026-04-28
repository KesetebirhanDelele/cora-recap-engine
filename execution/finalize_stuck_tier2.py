"""
Recovery script: finalize Cold Lead leads stuck at tier 2.

Targets leads whose last completed launch_outbound_call job produced no
webhook return (Synthflow called but Cora never received the result) and
who have no pending retry scheduled.  Without this recovery they remain
permanently at tier 2 and are never finalized in GHL.

What this script does per lead
-------------------------------
  1. Advance ai_campaign_value: '2' → '3' (optimistic-lock UPDATE)
  2. Write to GHL: AI Campaign = No, Mark as Lead = Yes  (shadow-gated)

Usage
-----
  # Dry run (default) — prints what would happen, no writes:
  python execution/finalize_stuck_tier2.py

  # Live run — commits DB updates and calls GHL:
  python execution/finalize_stuck_tier2.py --live

Run inside the worker container so DB and GHL credentials are available:
  docker compose exec worker-default python execution/finalize_stuck_tier2.py --live
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_ELIGIBLE_SQL = """
SELECT ls.id, ls.contact_id, ls.version, ls.ai_campaign_value
FROM lead_state ls
WHERE ls.ai_campaign_value = '2'
  AND NOT EXISTS (
      SELECT 1 FROM call_events ce
      WHERE ce.contact_id = ls.contact_id
        AND ce.created_at >= (
            SELECT MAX(sj2.run_at)
            FROM scheduled_jobs sj2
            WHERE sj2.entity_id = ls.contact_id
              AND sj2.job_type  = 'launch_outbound_call'
              AND sj2.status    = 'completed'
        )
  )
  AND NOT EXISTS (
      SELECT 1 FROM scheduled_jobs sj
      WHERE sj.entity_id = ls.contact_id
        AND sj.job_type  = 'launch_outbound_call'
        AND sj.status    IN ('pending', 'claimed')
  )
ORDER BY ls.contact_id;
"""


def main(live: bool) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info("finalize_stuck_tier2 starting | mode=%s", mode)

    from app.config import get_settings
    from app.db import get_sync_session
    from app.models.lead_state import LeadState
    from app.worker.jobs.voicemail_jobs import _advance_tier, _finalize_campaign
    from sqlalchemy import text

    settings = get_settings()

    with get_sync_session() as session:
        rows = session.execute(text(_ELIGIBLE_SQL)).fetchall()
        logger.info("Eligible leads found: %d", len(rows))

        if not rows:
            logger.info("Nothing to do.")
            return

        succeeded = 0
        skipped = 0
        failed = 0

        for row in rows:
            contact_id = row[1]
            lead_id = row[0]

            try:
                lead = session.get(LeadState, lead_id)
                if lead is None:
                    logger.warning("lead_state row vanished | contact_id=%s — skipping", contact_id)
                    skipped += 1
                    continue

                if lead.ai_campaign_value == "3":
                    logger.info("already tier 3, skipping | contact_id=%s", contact_id)
                    skipped += 1
                    continue

                if lead.ai_campaign_value != "2":
                    logger.warning(
                        "tier changed since query (now=%s) | contact_id=%s — skipping",
                        lead.ai_campaign_value, contact_id,
                    )
                    skipped += 1
                    continue

                if live:
                    _advance_tier(session, lead, "3")
                    _finalize_campaign(session, lead, settings)
                    session.commit()
                    logger.info("finalized | contact_id=%s", contact_id)
                else:
                    logger.info("[dry-run] would finalize | contact_id=%s", contact_id)

                succeeded += 1

            except Exception as exc:
                logger.exception("failed | contact_id=%s: %s", contact_id, exc)
                session.rollback()
                failed += 1

        logger.info(
            "Done | mode=%s succeeded=%d skipped=%d failed=%d",
            mode, succeeded, skipped, failed,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finalize stuck tier-2 Cold Lead leads")
    parser.add_argument("--live", action="store_true", help="Commit writes (default: dry-run)")
    args = parser.parse_args()
    main(live=args.live)
