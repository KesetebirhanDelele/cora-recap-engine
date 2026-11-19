"""Operator tool for the daily "who staff reached" push to Ali (spec/49 addendum).

Default is a DRY RUN: builds the exact file the job would send and prints it (CSV to stdout, counts to stderr).
Nothing is sent and nothing is recorded. Use this to look at the first file with Ali before anything goes out.

  --send   really POST it (needs explorer_contact_activity_url + CORA_SERVICE_TOKEN). Records the success time, so
           the next run only re-sends from a day earlier. Ignores the enabled flag on purpose: this is the
           deliberate manual first push (the 14-day file) that Ali asked to check together.

Never prints the token. Contact ids are printed only in the dry-run CSV (they are GHL ids, no names/emails).

Run on the server:
  docker compose exec -T worker-quality python -m execution.explorer_contact_activity_push > file.csv
  docker compose exec -T worker-quality python -m execution.explorer_contact_activity_push --send
"""
from __future__ import annotations

import csv
import sys

from app.config import get_settings
from app.db import get_sync_session
from app.services import explorer_contact_activity as eca


def main(argv: list[str]) -> int:
    send = "--send" in argv
    s = get_settings()
    with get_sync_session() as session:
        if not send:
            res = eca.push_contact_activity(session, s, dry_run=True)
            rows = eca.build_rows(session, res.window_start, res.window_end, eca.rep_ids(s))
            w = csv.writer(sys.stdout, lineterminator="\n")
            w.writerow(["ghl_contact_id", "last_connected_call_at"])
            w.writerows((r["ghl_contact_id"], r["last_connected_call_at"]) for r in rows)
            print(f"# DRY RUN window={eca.iso_z(res.window_start)}..{eca.iso_z(res.window_end)} rows={res.rows}",
                  file=sys.stderr)
            return 0
        res = eca.push_contact_activity(session, s)
        session.commit()
        print(
            f"# SENT rows={res.rows} batches={res.batches} matched={res.matched} unmatched={res.unmatched} "
            f"written={res.written} unchanged={res.unchanged} rejected_future={res.rejected_future} "
            f"reason={res.skipped_reason}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
