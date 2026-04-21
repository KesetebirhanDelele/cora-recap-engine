"""
migrate_sheets_calls.py — one-off import of Synthflow call data from a
Google Sheets CSV export into the call_events Postgres table.

Usage
-----
    python execution/migrate_sheets_calls.py path/to/calls.csv
    python execution/migrate_sheets_calls.py path/to/calls.csv --dry-run
    python execution/migrate_sheets_calls.py path/to/calls.csv --limit 50

Prerequisites
-------------
- Run from the project root so that `app/` is on the Python path.
- DATABASE_URL must be set in .env or the shell environment.
  Example (local):  DATABASE_URL=postgresql+psycopg2://postgres:...@localhost:5433/cora
  Example (server): set DATABASE_URL in .env on the Hetzner host, then run via
                    docker compose exec api python execution/migrate_sheets_calls.py /tmp/calls.csv

CSV format
----------
Export from Google Sheets: File → Download → Comma-separated values (.csv).
Required columns (order doesn't matter):
    Agent, type_of_call, call_id, module_id, duration, end_call_reason,
    executed_actions, prompt_variables, recording_url, transcript,
    start_time, name, timezone, phone_number_to, phone_number_from,
    status, campaign_type, Call Time

Idempotency
-----------
Each row is keyed by  dedupe_key = "{call_id}:sheets_import".
Rows already present in call_events are silently skipped.
Safe to re-run if interrupted — no duplicates are ever written.
"""

from __future__ import annotations

import argparse
import csv
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env before importing app modules so DATABASE_URL is set
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on shell env

# Add project root to sys.path so `app` is importable when run directly.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.db import get_sync_session  # noqa: E402  (must come after path fix)
from app.models.call_event import CallEvent  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 500  # rows per DB commit

# Maps substrings found in the Agent column to internal voice_agent labels.
# Add entries here if your sheet has other agent names.
_AGENT_KEYWORD_MAP: list[tuple[str, str]] = [
    ("newlead", "NewLead"),
    ("new lead", "NewLead"),
    ("coldlead", "ColdLead"),
    ("cold lead", "ColdLead"),
    ("inbound", "Inbound"),
]

_CAMPAIGN_NAME_MAP: dict[str, str] = {
    "NewLead": "New Lead",
    "ColdLead": "Cold Lead",
    "Inbound": "Inbound",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_phone(value: str | None) -> str | None:
    """
    Convert a phone number that Google Sheets may have mangled into scientific
    notation (e.g. "1.83E+10") back to an E.164-like string ("+18301234567").

    Returns None if the value is empty or unparseable.
    """
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        digits = str(int(float(raw)))
    except (ValueError, OverflowError):
        # Already a string like "+18005551234" — return as-is
        return raw if raw else None

    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) >= 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def _parse_timestamp(value: str | None) -> datetime | None:
    """
    Convert a millisecond epoch (possibly in scientific notation: "1.75E+12")
    to a timezone-aware UTC datetime.

    Returns None if the value is empty or unparseable.
    """
    if not value or not value.strip():
        return None
    try:
        ms = int(float(value.strip()))
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _infer_voice_agent(agent_str: str) -> tuple[str | None, str | None]:
    """
    Return (voice_agent, campaign_name) inferred from the Agent column value.
    Both are None if no keyword matches.
    """
    lower = agent_str.lower()
    for keyword, label in _AGENT_KEYWORD_MAP:
        if keyword in lower:
            return label, _CAMPAIGN_NAME_MAP.get(label)
    return None, None


def _safe_int(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(float(value.strip()))
    except (ValueError, OverflowError):
        return None


def _trunc(value: str | None, max_len: int) -> str | None:
    """Truncate a string to max_len characters. Silently drops overflow."""
    if value is None:
        return None
    return value[:max_len] if len(value) > max_len else value


def _row_to_call_event(row: dict) -> CallEvent | None:
    """
    Convert one CSV row dict to a CallEvent model instance.
    Returns None if call_id is missing (row cannot be keyed).
    """
    call_id = (row.get("call_id") or "").strip()
    if not call_id:
        return None

    dedupe_key = f"{call_id}:sheets_import"
    voice_agent, campaign_name = _infer_voice_agent(row.get("Agent") or "")

    # Store the full row as raw_payload_json for auditability / replay.
    raw_payload = {k: v for k, v in row.items() if v is not None and str(v).strip()}

    def _s(key: str) -> str | None:
        v = (row.get(key) or "").strip()
        return v or None

    start_time = _parse_timestamp(row.get("start_time"))
    # Use the original call timestamp for created_at so dashboard time-range
    # queries show real historical data instead of the import date.
    created_at = start_time or datetime.now(tz=timezone.utc)

    return CallEvent(
        id=str(uuid.uuid4()),
        call_id=_trunc(call_id, 255),
        contact_id=None,  # not available in sheet; may be enriched later
        direction=_trunc(_s("type_of_call"), 20),
        status=_trunc(_s("status"), 50),
        end_call_reason=_trunc(_s("end_call_reason"), 100),
        transcript=_s("transcript"),  # Text — no length limit
        duration_seconds=_safe_int(row.get("duration")),
        recording_url=_s("recording_url"),  # Text — no length limit
        start_time_utc=start_time,
        model_id=_trunc(_s("module_id"), 255),
        lead_name=_trunc(_s("name"), 255),
        # phone_number_from = the Synthflow outbound number (agent side)
        agent_phone_number=_trunc(_parse_phone(row.get("phone_number_from")), 50),
        voice_agent=_trunc(voice_agent, 50),
        campaign_name=_trunc(campaign_name, 100),
        dedupe_key=_trunc(dedupe_key, 512),
        detected_intent=None,  # not available in sheet
        raw_payload_json=raw_payload,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Synthflow call data from a Google Sheets CSV into call_events."
    )
    parser.add_argument("csv_path", help="Path to the CSV file exported from Google Sheets")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate without writing anything to the database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only process the first N rows (useful for a quick sanity check)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    # ---- Read CSV --------------------------------------------------------
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        all_rows = list(reader)

    total_rows = len(all_rows)
    if args.limit:
        all_rows = all_rows[: args.limit]

    print(f"CSV rows total: {total_rows}  |  processing: {len(all_rows)}")

    # ---- Parse -----------------------------------------------------------
    events: list[CallEvent] = []
    skipped_no_id = 0

    for row in all_rows:
        evt = _row_to_call_event(row)
        if evt is None:
            skipped_no_id += 1
        else:
            events.append(evt)

    print(
        f"Parsed: {len(events)} valid rows  |  "
        f"skipped (no call_id): {skipped_no_id}"
    )

    if args.dry_run:
        print("\n--dry-run active — no database writes.\n")
        print("Sample (first 5 rows):")
        for e in events[:5]:
            print(
                f"  call_id={e.call_id!r:<42} "
                f"status={e.status!r:<28} "
                f"lead={e.lead_name!r:<20} "
                f"start={e.start_time_utc}"
            )
        return

    # ---- Insert (batched, one commit per batch) --------------------------
    # Each batch runs in its own session so a bad row only loses that batch.
    # On re-run, already-inserted rows are skipped via dedupe_key check.
    import sqlalchemy  # noqa: PLC0415

    inserted = 0
    skipped_dup = 0
    batch_num = 0

    # Load existing keys once up front.
    with get_sync_session() as session:
        existing_keys: set[str] = {
            r[0]
            for r in session.execute(
                sqlalchemy.text(
                    "SELECT dedupe_key FROM call_events "
                    "WHERE dedupe_key LIKE :pattern"
                ),
                {"pattern": "%:sheets_import"},
            ).fetchall()
        }
    print(f"Existing sheets_import rows in DB: {len(existing_keys)}")

    # Split events into batches, skipping duplicates.
    batches: list[list[CallEvent]] = []
    current_batch: list[CallEvent] = []
    for evt in events:
        if evt.dedupe_key in existing_keys:
            skipped_dup += 1
            continue
        existing_keys.add(evt.dedupe_key)  # prevent intra-run dupes
        current_batch.append(evt)
        if len(current_batch) >= BATCH_SIZE:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)

    print(f"Batches to insert: {len(batches)}  |  Skipped (duplicates): {skipped_dup}")

    for batch in batches:
        batch_num += 1
        try:
            with get_sync_session() as session:
                for e in batch:
                    session.add(e)
            inserted += len(batch)
            print(f"  Batch {batch_num}/{len(batches)}: committed {inserted} rows so far…")
        except Exception as exc:  # noqa: BLE001
            print(
                f"  Batch {batch_num} FAILED ({exc.__class__.__name__}: {exc}). "
                f"Skipping {len(batch)} rows — re-run to retry.",
                file=sys.stderr,
            )

    print(
        f"\nDone.\n"
        f"  Inserted:              {inserted}\n"
        f"  Skipped (duplicates):  {skipped_dup}\n"
        f"  Skipped (no call_id):  {skipped_no_id}\n"
        f"  Total rows processed:  {len(events) + skipped_no_id}"
    )


if __name__ == "__main__":
    main()
