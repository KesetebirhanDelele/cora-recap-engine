"""
backfill_call_phones.py — Fill missing phone/name/contact_id on imported call records.

Two phases:
  Phase 1: Extract phone_number_to/from from recording_url already in the DB.
           Pattern: .../{call_id}_%2B{from}_%2B{to}_{timestamp}.wav
           No CSV required — runs against DB only.

  Phase 2: Read Calls_Logging CSV, match by call_id, fill:
           - lead_name        from CSV 'name' column
           - contact_id       from executed_actions → GHL contact → id
           - phone_number_to  from executed_actions → GHL contact → phone
             (fallback when recording_url didn't yield a phone)

Usage:
    python scripts/backfill_call_phones.py --phase 1
    python scripts/backfill_call_phones.py --phase 2 --csv /path/to/Calls_Logging.csv
    python scripts/backfill_call_phones.py --phase all --csv /path/to/Calls_Logging.csv

Dry-run (no writes):
    python scripts/backfill_call_phones.py --phase all --csv ... --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── DB connection ─────────────────────────────────────────────────────────────
def _get_conn():
    import psycopg2
    url = os.environ.get("DATABASE_URL") or os.environ.get("SYNC_DATABASE_URL")
    if url:
        # Strip SQLAlchemy driver prefix so psycopg2 can parse it
        url = url.replace("postgresql+psycopg2://", "postgresql://")
    if not url:
        # Build from parts
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = os.environ.get("POSTGRES_PORT", "5433")
        db   = os.environ.get("POSTGRES_DATABASE", "cora")
        user = os.environ.get("POSTGRES_USERNAME", "postgres")
        pw   = os.environ.get("POSTGRES_PASSWORD", "")
        url  = f"postgresql://{user}:{pw}@{host}:{port}/{db}"
    return psycopg2.connect(url)


# ── Phone extraction helpers ──────────────────────────────────────────────────
_URL_PHONE_RE = re.compile(r'_%2B(\d+)_%2B(\d+)_\d{8}T\d{6}')

def phones_from_url(recording_url: str | None) -> tuple[str | None, str | None]:
    """Return (phone_from, phone_to) extracted from a Synthflow recording URL."""
    if not recording_url:
        return None, None
    m = _URL_PHONE_RE.search(recording_url)
    if not m:
        return None, None
    return f"+{m.group(1)}", f"+{m.group(2)}"


def parse_executed_actions(raw: str | None) -> dict:
    """Return {'phone': ..., 'contact_id': ...} from executed_actions JSON blob."""
    if not raw:
        return {}
    try:
        actions = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(actions, dict):
            return {}
        # Walk all action keys looking for GHL contact data
        for action_data in actions.values():
            rv = action_data.get("return_value") or action_data.get("returnValue")
            if not rv:
                continue
            try:
                rv_obj = json.loads(rv) if isinstance(rv, str) else rv
            except (json.JSONDecodeError, TypeError):
                continue
            # Dig through: results → results.data → contact
            contact = (
                rv_obj.get("results", {})
                       .get("results.data", {})
                       .get("contact", {})
            )
            if contact.get("id") or contact.get("phone"):
                return {
                    "contact_id": contact.get("id"),
                    "phone":      contact.get("phone"),
                }
    except Exception:
        pass
    return {}


# ── Phase 1: DB-only phone extraction from recording_url ─────────────────────
PHASE1_SQL = """
UPDATE call_events
SET raw_payload_json = (
    COALESCE(raw_payload_json::text, '{}')::jsonb
    || jsonb_build_object(
        'phone_number_from', '+' || (regexp_match(
            recording_url,
            '_%2B([0-9]+)_%2B([0-9]+)_[0-9]{8}T[0-9]{6}'
        ))[1],
        'phone_number_to', '+' || (regexp_match(
            recording_url,
            '_%2B([0-9]+)_%2B([0-9]+)_[0-9]{8}T[0-9]{6}'
        ))[2]
    )
)::json
WHERE (raw_payload_json IS NULL
       OR raw_payload_json::jsonb->>'phone_number_to' IS NULL
       OR raw_payload_json::jsonb->>'phone_number_to' = '')
  AND recording_url IS NOT NULL
  AND recording_url ~ '_%2B[0-9]+_%2B[0-9]+_[0-9]{8}T[0-9]{6}'
RETURNING id;
"""

PHASE1_CHECK_SQL = """
SELECT COUNT(*) AS fixable
FROM call_events
WHERE (raw_payload_json IS NULL
       OR raw_payload_json::jsonb->>'phone_number_to' IS NULL
       OR raw_payload_json::jsonb->>'phone_number_to' = '')
  AND recording_url IS NOT NULL
  AND recording_url ~ '_%2B[0-9]+_%2B[0-9]+_[0-9]{8}T[0-9]{6}';
"""


def run_phase1(dry_run: bool) -> int:
    conn = _get_conn()
    cur  = conn.cursor()
    cur.execute(PHASE1_CHECK_SQL)
    fixable = cur.fetchone()[0]
    log.info("Phase 1: %d records fixable from recording_url", fixable)
    if dry_run:
        log.info("Phase 1: dry-run — no writes")
        cur.close(); conn.close()
        return fixable
    cur.execute(PHASE1_SQL)
    updated = cur.rowcount
    conn.commit()
    log.info("Phase 1: updated %d records", updated)
    cur.close(); conn.close()
    return updated


# ── Phase 2: CSV-based backfill ───────────────────────────────────────────────
def run_phase2(csv_path: str, dry_run: bool) -> int:
    if not os.path.exists(csv_path):
        log.error("CSV not found: %s", csv_path)
        sys.exit(1)

    conn = _get_conn()
    cur  = conn.cursor()

    # Load call_ids that still need phone OR name OR contact_id
    cur.execute("""
        SELECT call_id,
               raw_payload_json::jsonb->>'phone_number_to' AS phone_to,
               lead_name,
               contact_id
        FROM call_events
        WHERE raw_payload_json IS NULL
           OR raw_payload_json::jsonb->>'phone_number_to' IS NULL
           OR raw_payload_json::jsonb->>'phone_number_to' = ''
           OR lead_name IS NULL
           OR contact_id IS NULL
    """)
    needs_fix = {row[0]: row for row in cur.fetchall()}
    log.info("Phase 2: %d call_events need at least one field filled", len(needs_fix))

    if not needs_fix:
        log.info("Phase 2: nothing to do")
        cur.close(); conn.close()
        return 0

    updated = 0
    batch: list[tuple] = []

    try:
        f = open(csv_path, newline="", encoding="latin-1")
    except Exception as e:
        log.error("Cannot open CSV: %s", e)
        sys.exit(1)

    with f:
        reader = csv.DictReader(f)
        headers = {k.strip().lower() for k in (reader.fieldnames or [])}
        log.info("CSV columns: %s", sorted(headers))

        for row in reader:
            row = {k.strip().lower(): v.strip() for k, v in row.items()}
            call_id = row.get("call_id", "").strip()
            if call_id not in needs_fix:
                continue

            # Phone directly from CSV columns (most reliable)
            # Normalize: ensure + prefix so format matches recording_url extraction
            def _norm(p: str) -> str | None:
                p = p.strip()
                if not p:
                    return None
                return p if p.startswith("+") else f"+{p}"

            phone_to   = _norm(row.get("phone_number_to", ""))
            phone_from = _norm(row.get("phone_number_from", ""))

            # Fallback: extract from recording_url
            if not phone_to or not phone_from:
                url_from, url_to = phones_from_url(row.get("recording_url"))
                phone_to   = phone_to   or url_to
                phone_from = phone_from or url_from

            # Fallback: extract from executed_actions (GHL contact)
            ea_data    = parse_executed_actions(row.get("executed_actions"))
            contact_id = ea_data.get("contact_id")
            phone_to   = phone_to or ea_data.get("phone")

            name = row.get("name") or row.get("lead_name")

            # Skip row if nothing useful
            if not any([phone_to, phone_from, contact_id, name]):
                continue

            batch.append((phone_to, phone_from, contact_id, name, call_id))

    log.info("Phase 2: %d CSV rows matched call_events", len(batch))
    if dry_run:
        log.info("Phase 2: dry-run — showing first 5 rows:")
        for row in batch[:5]:
            log.info("  call_id=%s phone_to=%s phone_from=%s contact_id=%s name=%s",
                     row[4], row[0], row[1], row[2], row[3])
        cur.close(); conn.close()
        return len(batch)

    for phone_to, phone_from, contact_id, name, call_id in batch:
        # Build partial update — only overwrite NULL / empty fields
        sets = []
        params: list = []

        phones_patch: dict = {}
        if phone_to:
            phones_patch["phone_number_to"] = phone_to
        if phone_from:
            phones_patch["phone_number_from"] = phone_from

        if phones_patch:
            patch_expr = " || ".join(
                f"jsonb_build_object('{k}', %s)" for k in phones_patch
            )
            sets.append(f"""
                raw_payload_json = (
                    COALESCE(raw_payload_json::text, '{{}}')::jsonb
                    || {patch_expr}
                )::json
            """)
            params.extend(phones_patch.values())

        if contact_id:
            sets.append("contact_id = COALESCE(contact_id, %s)")
            params.append(contact_id)

        if name:
            sets.append("lead_name = COALESCE(NULLIF(lead_name,''), %s)")
            params.append(name)

        if not sets:
            continue

        params.append(call_id)
        sql = f"UPDATE call_events SET {', '.join(sets)} WHERE call_id = %s"
        cur.execute(sql, params)
        updated += cur.rowcount

    conn.commit()
    log.info("Phase 2: updated %d records", updated)
    cur.close(); conn.close()
    return updated


# ── Verification ──────────────────────────────────────────────────────────────
def run_verify():
    conn = _get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT
          date_trunc('week', call_started_at)::date AS week,
          COUNT(*)                                   AS total_calls,
          COUNT(raw_payload_json::jsonb->>'phone_number_to')
            FILTER (WHERE raw_payload_json::jsonb->>'phone_number_to' IS NOT NULL
                      AND raw_payload_json::jsonb->>'phone_number_to' != '')
                                                     AS has_phone,
          COUNT(DISTINCT raw_payload_json::jsonb->>'phone_number_to')
                                                     AS unique_contacts
        FROM call_events
        WHERE call_started_at IS NOT NULL
          AND call_started_at >= '2026-02-01'
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 12
    """)
    print(f"\n{'Week':<14} {'Total':>10} {'HasPhone':>10} {'UniqContacts':>14}")
    print("-" * 52)
    for row in cur.fetchall():
        print(f"{str(row[0]):<14} {row[1]:>10} {row[2]:>10} {row[3]:>14}")
    cur.close(); conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["1", "2", "all"], default="all")
    parser.add_argument("--csv",   default=None, help="Path to Calls_Logging CSV")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.phase in ("1", "all"):
        run_phase1(dry_run=args.dry_run)

    if args.phase in ("2", "all"):
        if not args.csv:
            log.error("--csv required for phase 2")
            sys.exit(1)
        run_phase2(csv_path=args.csv, dry_run=args.dry_run)

    run_verify()
