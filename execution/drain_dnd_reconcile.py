"""
Drain the do-not-disturb reconcile backlog (spec/45).

`optout.reconcile_step` compares every lead Cora marked do-not-call with GHL's own Do-Not-Disturb: already set -> an 'ok' row; missing -> a 'review' row for an
operator (Opt-outs page). It handles at most 20 leads / 15 seconds per run and ran about 4 an hour, so 238 do-not-call leads (135 of them older than 60 days) had
never been looked at. This calls it in a loop until nothing is left or the time limit is reached.

It READS GHL and writes only `optout_actions` rows. It never turns Do-Not-Disturb on: that stays an operator decision on the Opt-outs page, because the
do-not-call intent can be a false positive ("don't call me at work").

Usage (inside the worker container; run detached for the full backlog):

    docker compose exec -T -d worker-default sh -c 'python execution/drain_dnd_reconcile.py > /tmp/drain.log 2>&1'
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def drain(session: Any, settings: Any, ghl: Any = None, max_minutes: float = 25.0, step_limit: int = 20, step_budget: float = 60.0,
          out=print, step=None, clock=time.monotonic) -> dict[str, int]:
    """Call reconcile_step repeatedly. Stops when a step finds nothing to check, or after `max_minutes`."""
    if step is None:
        from app.services.optout import reconcile_step as step
    totals = {"checked": 0, "ok": 0, "review": 0, "failed": 0, "steps": 0}
    deadline = clock() + max_minutes * 60
    while clock() < deadline:
        r = step(session, settings, ghl, limit=step_limit, budget_seconds=step_budget)
        totals["steps"] += 1
        for k in ("checked", "ok", "review", "failed"):
            totals[k] += int(r.get(k, 0))
        out(f"step {totals['steps']}: {r} | running total {totals}")
        if not any(r.get(k) for k in ("checked", "failed")):
            break
    return totals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max-minutes", type=float, default=25.0)
    args = ap.parse_args(argv)
    from app.config import get_settings
    from app.db import get_sync_session

    with get_sync_session() as session:
        t = drain(session, get_settings(), max_minutes=args.max_minutes)
    print(f"DONE: {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
