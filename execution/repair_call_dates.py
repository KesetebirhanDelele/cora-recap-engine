"""
Repair call_events rows whose call time was stored as an impossible date (spec/45).

`_parse_datetime` used to read a 13-digit epoch-milliseconds STRING ("1793061552134") with fromisoformat, giving year 1793. Those calls then sat in the
18th century and dropped out of every weekly report (238 rows by 2026-10-26, ~21 a day). The parser is fixed; this repairs the existing rows.

For each row with start_time_utc before 2000 the real time is taken, in order, from:
  1. the Synthflow payload (`start_time` / `start_time_utc`) parsed with the FIXED parser  - exact
  2. the row's own call_started_at when that one is sane                                  - exact
  3. created_at (when Cora received the webhook, 10-15 minutes after the call)            - approximate
and BOTH start_time_utc and call_started_at are set to it. Rows already fine are never touched.

Dry run by default (counts only). Usage inside the worker container:

    docker compose exec worker-default python execution/repair_call_dates.py            # dry run
    docker compose exec worker-default python execution/repair_call_dates.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CUTOFF = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def plan(row: Any) -> tuple[datetime | None, str]:
    """(the corrected time, where it came from) for one bad row."""
    from app.worker.jobs.call_processing import _parse_datetime

    payload = row.raw_payload_json or {}
    for key in ("start_time", "start_time_utc"):
        fixed = _parse_datetime(payload.get(key))
        if fixed is not None and fixed >= CUTOFF:
            return fixed, "payload"
    started = _aware(row.call_started_at)
    if started is not None and started >= CUTOFF:
        return started, "call_started_at"
    created = _aware(row.created_at)
    if created is not None:
        return created, "created_at (approximate)"
    return None, "nothing usable"


def run(session: Any, apply: bool = False, out=print) -> dict[str, int]:
    from sqlalchemy import select

    from app.models.call_event import CallEvent

    rows = [r for r in session.scalars(select(CallEvent)).all() if _aware(r.start_time_utc) is not None and _aware(r.start_time_utc) < CUTOFF]
    sources = Counter()
    fixes = []
    for r in rows:
        when, source = plan(r)
        sources[source] += 1
        if when is not None:
            fixes.append((r, when))
    out(f"call records with an impossible call time (before 2000): {len(rows)}")
    out(f"  repairable from: {dict(sources)}")
    result = {"bad": len(rows), "repaired": 0, "unfixable": len(rows) - len(fixes)}
    if not apply:
        out("DRY RUN - nothing was changed. Re-run with --apply.")
        return result
    for r, when in fixes:
        r.start_time_utc = when
        r.call_started_at = when
        result["repaired"] += 1
    session.commit()
    out(f"APPLIED: repaired {result['repaired']} rows ({result['unfixable']} had no usable time)")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="really write the repair (default is a dry run)")
    args = ap.parse_args(argv)
    from app.db import get_sync_session

    with get_sync_session() as session:
        run(session, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
