"""
Backfill detected_intent on existing call_events rows.

Queries all call_events that have a transcript but no detected_intent,
runs detect_intent() on each transcript, and writes the result back.

Usage (run via docker exec so DATABASE_URL resolves correctly):

    docker exec cora-recap-engine-api-1 python execution/backfill_detected_intent.py

Or locally (ensure DATABASE_URL in .env points to the correct host):

    python execution/backfill_detected_intent.py

Options:
    --dry-run   Print what would be updated without writing to the DB.
    --limit N   Process at most N rows (default: all).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select, update  # noqa: E402

from app.core.intent_detection import detect_intent  # noqa: E402
from app.db import get_sync_session  # noqa: E402
from app.models.call_event import CallEvent  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="Print results without writing to DB")
    p.add_argument("--limit", type=int, default=None,
                   help="Max rows to process (default: all)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    dry_run = args.dry_run
    limit = args.limit

    updated = 0
    skipped = 0
    errors = 0

    with get_sync_session() as session:
        q = (
            select(CallEvent)
            .where(
                CallEvent.status == "completed",
                CallEvent.transcript.isnot(None),
                CallEvent.transcript != "",
                CallEvent.detected_intent.is_(None),
            )
            .order_by(CallEvent.created_at.asc())
        )
        if limit:
            q = q.limit(limit)

        rows = session.scalars(q).all()
        print(f"Found {len(rows)} call_events to process.")

        for ce in rows:
            try:
                transcript = (ce.transcript or "").strip()
                if not transcript:
                    skipped += 1
                    continue

                # Pass executed_actions and duration if available in raw payload
                raw = ce.raw_payload_json or {}
                ea = raw.get("executed_actions")
                dur = ce.duration_seconds

                result = detect_intent(transcript, executed_actions=ea, duration_seconds=dur)
                intent_val = result["intent"] if result else None

                if dry_run:
                    print(
                        f"  call_id={ce.call_id!r:40s}  "
                        f"status={ce.status or '?':12s}  "
                        f"intent={intent_val or '(none)'}"
                    )
                else:
                    session.execute(
                        update(CallEvent)
                        .where(CallEvent.id == ce.id)
                        .values(detected_intent=intent_val)
                    )
                    updated += 1

            except Exception as exc:
                errors += 1
                print(f"  ERROR for call_id={ce.call_id!r}: {exc}", file=sys.stderr)

        if not dry_run:
            session.commit()

    if dry_run:
        print(f"\nDry run complete. {len(rows)} rows inspected, {errors} errors.")
    else:
        print(f"\nDone. {updated} updated, {skipped} skipped (blank transcript), {errors} errors.")


if __name__ == "__main__":
    main()
