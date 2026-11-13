"""Read-only bucket check for the Explorer queue feed (Ali's go-live gate, 2026-11-07).

One live pull of the reach_out view, then for each row the bucket the nightly job would put it in:
  tag                 would be tagged (new or already tagged)
  skip_ai_campaign    GHL contact has AI Campaign = Yes
  skip_no_contact     no usable ghl_url
  skip_wrong_subacct  contact lives in a different GHL location
  unreadable          GHL contact could not be read (reported, never guessed)

Writes NOTHING: no GHL writes, no database access. Prints CSV (rank, enrollment_id, bucket) plus a
count summary. Never prints the service token.

Run on the server:  docker compose exec -T worker-quality python -m execution.explorer_queue_bucket_check
"""
from __future__ import annotations

import csv
import sys
from collections import Counter

from app.adapters.ghl import GHLClient
from app.config import get_settings
from app.services import explorer_queue_logic as L
from app.services.explorer_queue import _read_contact
from app.services.explorer_queue_feed import LiveFeed


def classify(rows, settings, lookup) -> list[tuple[int | None, str | None, str]]:
    out = []
    for row in rows:
        parsed = L.parse_ghl_url(row.ghl_url)
        if parsed is None:
            bucket = "skip_no_contact"
        elif parsed[0] != settings.ghl_location_id:
            bucket = "skip_wrong_subacct"
        else:
            try:
                _tags, ai = _read_contact(lookup(parsed[1]), settings.explorer_queue_ai_campaign_field_id)
                bucket = "skip_ai_campaign" if ai else "tag"
            except Exception:
                bucket = "unreadable"
        out.append((row.rank, row.enrollment_id, bucket))
    return out


def main() -> int:
    s = get_settings()
    feed = LiveFeed(s.explorer_queue_feed_url, s.cora_service_token)
    result = feed.fetch("reach_out", limit=s.explorer_queue_batch_cap)
    client = GHLClient(settings=s)
    try:
        rows = classify(result.rows, s, client.get_contact)
    finally:
        client.close()
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["rank", "enrollment_id", "bucket"])
    w.writerows(rows)
    counts = Counter(b for _, _, b in rows)
    print(f"# pulled={len(result.rows)} {dict(counts)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
