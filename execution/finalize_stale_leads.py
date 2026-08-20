"""
Finalize stale leads that GHL marks as do-not-call or campaign-off.

Runs after sync_stale_leads_from_ghl.py --apply has synced GHL fields into
lead_state. Uses the synced do_not_call and ai_campaign columns to identify
the two finalize groups and closes them in the DB.

Two groups closed:
  DNC (159)          — lead_state.do_not_call = true
  Campaign-off (34)  — lead_state.ai_campaign in No/False/0, not already DNC

What this script does per lead:
  1. Set lead_state.status = 'closed'
  2. Bump version (concurrency safety)
  No GHL writes — GHL state is already correct (tags/DND set there first).

Usage
-----
  # Dry-run (default):
  docker compose exec worker-default python execution/finalize_stale_leads.py

  # Live — writes to DB:
  docker compose exec worker-default python execution/finalize_stale_leads.py --live
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
SELECT
    contact_id,
    campaign_name,
    ai_campaign_value AS vm_tier,
    do_not_call,
    ai_campaign,
    CASE
        WHEN do_not_call = true THEN 'do_not_call'
        ELSE 'campaign_off'
    END AS reason
FROM lead_state
WHERE
    (status IS NULL OR status NOT IN ('closed', 'terminal'))
    AND (ai_campaign_value IS NULL OR ai_campaign_value != '3')
    AND (
        do_not_call = true
        OR LOWER(TRIM(ai_campaign)) IN ('no', 'false', '0')
    )
ORDER BY do_not_call DESC, contact_id
"""

_CLOSE_SQL = """
UPDATE lead_state
SET
    status     = 'closed',
    version    = version + 1,
    updated_at = NOW()
WHERE contact_id = :contact_id
  AND (status IS NULL OR status NOT IN ('closed', 'terminal'))
"""


def main(live: bool) -> None:
    mode = "LIVE" if live else "DRY-RUN"
    logger.info("finalize_stale_leads starting | mode=%s", mode)

    from sqlalchemy import text

    from app.db import get_sync_session

    with get_sync_session() as session:
        rows = session.execute(text(_ELIGIBLE_SQL)).fetchall()

    dnc_count = sum(1 for r in rows if r.do_not_call)
    off_count = len(rows) - dnc_count
    logger.info(
        "Leads to finalize: %d total (do_not_call=%d campaign_off=%d)",
        len(rows), dnc_count, off_count,
    )

    if not rows:
        logger.info("Nothing to do.")
        return

    succeeded = 0
    skipped = 0
    failed = 0

    for row in rows:
        contact_id = row.contact_id
        reason = row.reason
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
                    "closed | contact_id=%s reason=%s campaign=%s tier=%s",
                    contact_id, reason, row.campaign_name, row.vm_tier,
                )
            else:
                logger.info(
                    "[dry-run] would close | contact_id=%s reason=%s campaign=%s tier=%s",
                    contact_id, reason, row.campaign_name, row.vm_tier,
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
    parser = argparse.ArgumentParser(description="Finalize DNC + campaign-off stale leads")
    parser.add_argument("--live", action="store_true", help="Commit writes (default: dry-run)")
    args = parser.parse_args()
    main(live=args.live)
