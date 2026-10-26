"""
Relabel past bookings that were recorded as callbacks (spec/42).

A call on which a booking succeeded used to get detected_intent = "callback_with_time". The new intent is "booked". Both carry the same
Sales Queue score and urgency, so nothing changes for staff; this only keeps the intent charts consistent (no artificial break at the
deploy date). Only calls whose executed_actions really show a successful booking are touched. Label-only: no calls are deleted, no jobs
are created or cancelled.

Dry run by default (counts only). Usage inside the worker container:

    docker compose exec worker-default python execution/relabel_booked_calls.py            # dry run
    docker compose exec worker-default python execution/relabel_booked_calls.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def find_booked_calls(session: Any) -> list[Any]:
    """call_events labelled callback_with_time whose executed_actions show a successful booking."""
    from sqlalchemy import select

    from app.core.intent_detection import _extract_executed_actions
    from app.models.call_event import CallEvent

    rows = session.scalars(select(CallEvent).where(CallEvent.detected_intent == "callback_with_time")).all()
    return [r for r in rows if _extract_executed_actions((r.raw_payload_json or {}).get("executed_actions"))["booking_success"]]


def run(session: Any, apply: bool = False, out=print) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.models.call_event import CallEvent

    total = session.scalar(select(func.count()).select_from(CallEvent).where(CallEvent.detected_intent == "callback_with_time")) or 0
    booked = find_booked_calls(session)
    out(f"calls labelled callback_with_time: {total}; of those a successful booking: {len(booked)}; real callback requests (untouched): {total - len(booked)}")
    if not apply:
        out("DRY RUN - nothing was changed. Re-run with --apply.")
        return {"total": total, "booked": len(booked), "relabelled": 0}
    for r in booked:
        r.detected_intent = "booked"
    session.commit()
    out(f"APPLIED: relabelled {len(booked)} calls to 'booked'")
    return {"total": total, "booked": len(booked), "relabelled": len(booked)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="really write the relabel (default is a dry run)")
    args = ap.parse_args(argv)
    from app.db import get_sync_session

    with get_sync_session() as session:
        run(session, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
