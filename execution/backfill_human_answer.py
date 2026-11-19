"""Backfill staff_call_quality.answered_by_human (live person vs voicemail) for already-scanned calls.

Default is a DRY RUN: counts the outbound connected calls that have a transcript but no verdict. Nothing is
judged or written. --apply judges them (newest first) and writes only that one column. Safe to rerun: judged rows
are skipped, failures stay NULL. Transcripts go to the same OpenAI model the scan already uses; the output here is
counts only (no names, phones or transcript text).

  docker compose exec -T worker-quality python -m execution.backfill_human_answer
  docker compose exec -T worker-quality python -m execution.backfill_human_answer --apply --days 30 --limit 2000
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.core.human_answer_judge import judge_pending
from app.db import get_sync_session
from app.models.staff_call_quality import StaffCallQuality as S


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args(argv)
    since = datetime.now(tz=timezone.utc) - timedelta(days=a.days)
    s = get_settings()
    with get_sync_session() as session:
        pending = session.execute(select(func.count()).where(
            S.call_connected.is_(True), S.direction == "outbound", S.answered_by_human.is_(None),
            S.transcript_text.is_not(None), S.transcript_text != "", S.call_time >= since)).scalar() or 0
        if not a.apply:
            print(f"# DRY RUN: {pending} calls in the last {a.days} days need a verdict (nothing written)", file=sys.stderr)
            return 0
        total = {"judged": 0, "human": 0, "not_human": 0, "failed": 0}
        remaining = a.limit
        while remaining > 0:
            counts = judge_pending(session, s, since=since, limit=min(200, remaining))
            session.commit()
            for k in total:
                total[k] += counts[k]
            remaining -= 200
            if counts["judged"] == 0:  # nothing left, or only failures: stop rather than loop
                break
        print(f"# APPLIED pending_before={pending} {total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
