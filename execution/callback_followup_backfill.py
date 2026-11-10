"""History load for the Call-back Follow-ups card (spec/50, Kes 2026-11-10).

Puts the failed-transfer calls and unanswered lead emails of the last N days (default 21) on the dashboard card as PENDING,
dated at the original call / email. Sends NO email to anyone (no "waiting for approval" notice, nothing to staff or leads) and
writes nothing to GHL: it only adds rows to the callback_followups ledger and reads GHL. Approving a row on the dashboard still
books / emails the staff member as usual. Safe to rerun: rows already in the ledger are skipped.

Run on the server:  docker compose exec -T worker-default python -m execution.callback_followup_backfill --days 21
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from app.config import get_settings
from app.db import get_sync_session
from app.services import callback_followup as svc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    args = ap.parse_args()
    settings = get_settings()
    with get_sync_session() as session:
        calls = svc.backfill_calls(session, settings, days=args.days)
        emails = svc.run_unanswered_email_check(
            session, settings, force=True, lookback=timedelta(days=args.days), notify_pending=False, history=True, limit=200)
    print("calls:", calls)
    print("emails:", emails)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
