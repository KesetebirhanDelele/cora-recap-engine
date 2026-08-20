"""
Finalize tier-2 leads with no pending job.

Targets leads where:
  - ai_campaign_value = '2'   (tier 2 = last real VM tier)
  - no pending/claimed/running job
  - status not in ('closed', 'terminal')

Tier 2 is the last substantive VM tier (Cold Lead path: tier 0 -> 1 -> 2 -> terminal).
A tier-2 lead with no pending job means the tier-2 call completed but the system
never created the follow-up finalize job.  These leads should be set to 'terminal'.

SAFE: leads that have a pending job (being actively worked) are excluded by SQL.

Usage
-----
  # Dry-run (default):
  docker compose exec worker-default python execution/finalize_stuck_tier2.py

  # Live -- writes to DB:
  docker compose exec worker-default python execution/finalize_stuck_tier2.py --live
"""
from __future__ import annotations

import argparse
import logging
import sys
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

_ELIGIBLE_SQL = """
WITH pending AS (
    SELECT entity_id
    FROM scheduled_jobs
    WHERE status IN ('pending', 'claimed', 'running')
    GROUP BY entity_id
)
SELECT
    ls.contact_id,
    ls.campaign_name,
    ls.do_not_call,
    ls.ai_campaign,
    ls.last_call_status,
    ls.status AS current_status
FROM lead_state ls
LEFT JOIN pending p ON p.entity_id = ls.contact_id
WHERE
    ls.ai_campaign_value = '2'
    AND (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal'))
    AND ls.do_not_call IS NOT TRUE
    AND p.entity_id IS NULL
ORDER BY ls.contact_id
"""

_CLOSE_SQL = """
UPDATE lead_state
SET
    status     = 'terminal',
    version    = version + 1,
    updated_at = NOW()
WHERE contact_id = :contact_id
  AND (status IS NULL OR status NOT IN ('closed', 'terminal'))
"""


def main(live: bool) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info("finalize_stuck_tier2 starting | mode=%s", mode)

    from sqlalchemy import text

    from app.db import get_sync_session

    with get_sync_session() as session:
        rows = session.execute(text(_ELIGIBLE_SQL)).fetchall()

    logger.info("Tier-2 stuck leads (no pending job) to finalize: %d", len(rows))

    if not rows:
        logger.info("Nothing to do.")
        return

    succeeded = 0
    skipped = 0
    failed = 0

    for row in rows:
        contact_id = row.contact_id
        try:
            if live:
                with get_sync_session() as session:
                    result = session.execute(
                        text(_CLOSE_SQL),
                        {"contact_id": contact_id},
                    )
                    session.commit()
                    if result.rowcount == 0:
                        logger.info("already closed, skip | contact_id=%s", contact_id)
                        skipped += 1
                        continue
                logger.info(
                    "set terminal | contact_id=%s campaign=%s last_call=%s",
                    contact_id, row.campaign_name, row.last_call_status,
                )
            else:
                logger.info(
                    "[dry-run] would set terminal | contact_id=%s campaign=%s last_call=%s",
                    contact_id, row.campaign_name, row.last_call_status,
                )
            succeeded += 1

        except Exception as exc:
            logger.exception("failed | contact_id=%s: %s", contact_id, exc)
            failed += 1

    logger.info(
        "Done | mode=%s succeeded=%d skipped=%d failed=%d",
        mode, succeeded, skipped, failed,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finalize tier-2 no-pending leads as terminal")
    parser.add_argument("--live", action="store_true", help="Commit writes (default: dry-run)")
    args = parser.parse_args()
    main(live=args.live)
