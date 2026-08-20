"""
Finalize tier-3 leads that never received their _finalize_campaign() call.

Targets all leads where:
  - ai_campaign_value = '3'   (GHL/DB tier 3 = VM sequence complete)
  - no pending/claimed/running job in scheduled_jobs
  - status not in ('closed', 'terminal')

Sets status = 'terminal' (VM sequence exhausted).

NOTE: finalize_stale_leads.py intentionally skips tier-3 leads (it targets only
tier 0/1/2 leads for DNC/campaign-off closure).  This script handles tier-3 leads
including any that also carry a DNC flag.

Usage
-----
  # Dry-run (default):
  docker compose exec worker-default python execution/finalize_stuck_tier3.py

  # Live — writes to DB:
  docker compose exec worker-default python execution/finalize_stuck_tier3.py --live
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
    ls.status      AS current_status
FROM lead_state ls
LEFT JOIN pending p ON p.entity_id = ls.contact_id
WHERE
    ls.ai_campaign_value = '3'
    AND (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal'))
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
    logger.info("finalize_stuck_tier3 starting | mode=%s", mode)

    from sqlalchemy import text

    from app.db import get_sync_session

    with get_sync_session() as session:
        rows = session.execute(text(_ELIGIBLE_SQL)).fetchall()

    dnc_count = sum(1 for r in rows if r.do_not_call)
    logger.info(
        "Tier-3 stuck leads to finalize: %d total (do_not_call=%d non_dnc=%d)",
        len(rows), dnc_count, len(rows) - dnc_count,
    )

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
                    "set terminal | contact_id=%s campaign=%s dnc=%s",
                    contact_id, row.campaign_name, row.do_not_call,
                )
            else:
                logger.info(
                    "[dry-run] would set terminal | contact_id=%s campaign=%s dnc=%s",
                    contact_id, row.campaign_name, row.do_not_call,
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
    parser = argparse.ArgumentParser(description="Finalize tier-3 stuck leads as terminal")
    parser.add_argument("--live", action="store_true", help="Commit writes (default: dry-run)")
    args = parser.parse_args()
    main(live=args.live)
