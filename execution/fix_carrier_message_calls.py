"""
One-off cleanup for the carrier-message redial loop (spec/41).

Before the fix, a call answered by a carrier recording ("We could not complete your call. Please try again.", "This number is not in
service...") was classified as the lead asking to be called later, so the engine redialled every 2 hours with no cap. This script
repairs what is already in the database:

  1. relabels those calls: call_events.detected_intent  call_later_no_time -> carrier_message
  2. cancels any still-pending call_later_no_time redial of those contacts
  3. marks a number invalid (lead_state, same as a wrong number) when it has CARRIER_MESSAGE_STRIKES or more such calls inside the
     live rule's window (CARRIER_MESSAGE_WINDOW_DAYS = 30), even though step 1 looks back further (--days, default 60)

Dry run by default: prints counts only (no phone numbers or names). Nothing is written without --apply.

Usage (inside the worker container so DATABASE_URL and GHL settings resolve):

    docker compose exec worker-default python execution/fix_carrier_message_calls.py            # dry run
    docker compose exec worker-default python execution/fix_carrier_message_calls.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def find_carrier_calls(session: Any, days: int) -> list[Any]:
    """Outbound calls of the last `days` days whose transcript is a carrier / phone-system recording."""
    from sqlalchemy import select

    from app.core.intent_detection import is_carrier_message
    from app.models.call_event import CallEvent

    since = datetime.now(tz=timezone.utc) - timedelta(days=days)
    rows = session.scalars(select(CallEvent).where(
        CallEvent.direction == "outbound", CallEvent.transcript.is_not(None), CallEvent.created_at >= since)).all()
    return [r for r in rows if is_carrier_message(r.transcript)]


def run(session: Any, settings: Any, days: int = 60, apply: bool = False, out=print) -> dict[str, int]:
    from app.core import intent_actions as ia

    calls = find_carrier_calls(session, days)
    per_contact = Counter(c.contact_id for c in calls if c.contact_id)
    relabel = [c for c in calls if c.detected_intent == "call_later_no_time"]
    strikes = ia.CARRIER_MESSAGE_STRIKES
    recent_since = datetime.now(tz=timezone.utc) - timedelta(days=ia.CARRIER_MESSAGE_WINDOW_DAYS)

    def _when(c: Any) -> datetime:
        t = c.call_started_at or c.created_at
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    recent = Counter(c.contact_id for c in calls if c.contact_id and _when(c) >= recent_since)
    dead = sorted(cid for cid, n in recent.items() if n >= strikes)
    out(f"carrier-recording calls in the last {days} days: {len(calls)} to {len(per_contact)} contacts")
    out(f"  mislabelled call_later_no_time (to relabel carrier_message): {len(relabel)}")
    out(f"  contacts with {strikes}+ such calls in the last {ia.CARRIER_MESSAGE_WINDOW_DAYS} days (to mark invalid): {len(dead)}")
    out(f"  heaviest contacts (calls): {[n for _c, n in per_contact.most_common(5)]}")
    result = {"calls": len(calls), "contacts": len(per_contact), "relabelled": 0, "redials_cancelled": 0, "marked_invalid": 0}
    if not apply:
        out("DRY RUN - nothing was changed. Re-run with --apply.")
        return result
    for c in relabel:
        c.detected_intent = "carrier_message"
        result["relabelled"] += 1
    for cid in per_contact:
        result["redials_cancelled"] += ia._cancel_pending_redials(session, cid)
    for cid in dead:
        ia._handle_wrong_number(session, cid, "", {}, settings)
        result["marked_invalid"] += 1
    session.commit()
    out(f"APPLIED: {result}")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=60, help="how far back to look (default 60)")
    ap.add_argument("--apply", action="store_true", help="really write the changes (default is a dry run)")
    args = ap.parse_args(argv)

    from app.config import get_settings
    from app.db import get_sync_session

    with get_sync_session() as session:
        run(session, get_settings(), args.days, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
