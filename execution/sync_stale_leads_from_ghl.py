"""
Sync stale leads from GHL.

Fetches the current GHL state for all leads that are "active" in our DB
but have had no pending/running job for more than N days (default 7).

For each stale lead this script will:
  1. Call GHL search_contact_by_phone() to get the live contact record.
  2. Extract: DND flag, tags, AI Campaign, AI Campaign Value, Last Call Status,
     Mark as Lead, AI Campaign Classification.
  3. Derive a recommended_action based on GHL state vs DB tier.
  4. Write one row per contact to a timestamped CSV in /app/tmp/.
  5. (--apply only) Sync factual GHL fields back to lead_state in the DB.

Recommended actions (appear in the CSV):
  finalize__do_not_call     — GHL DND is set; mark as do_not_call in DB
  finalize__converted        — AI Campaign=No AND Mark as Lead=Yes
  finalize__campaign_off     — AI Campaign=No (not converted)
  finalize__tier3_complete   — AI Campaign Value=3 in GHL (should already be closed)
  finalize__tier2_terminal   — DB tier 2, no pending job (VM sequence done, needs finalize)
  reschedule__call_next_tier — AI Campaign=Yes, tiers 0 or 1 (needs next outbound call)
  review__not_found          — Contact not found in GHL (deleted / archived)
  review__no_campaign_flag   — AI Campaign field missing/blank; manual review needed
  review__manual             — Anything else unexpected

--apply writes to DB:
  - lead_state.do_not_call    ← GHL DND
  - lead_state.ai_campaign    ← GHL AI Campaign field value
  - lead_state.ai_campaign_value ← GHL AI Campaign Value field
  - lead_state.last_call_status  ← GHL Last Call Status field
  Status column is NOT changed — use the CSV to decide which action to take next.

Usage
-----
  # Dry-run (report only — safe to run any time):
  docker compose exec worker-default python execution/sync_stale_leads_from_ghl.py

  # Apply DB sync (updates lead_state fields from GHL):
  docker compose exec worker-default python execution/sync_stale_leads_from_ghl.py --apply

  # Custom idle threshold (e.g. 3 days):
  docker compose exec worker-default python execution/sync_stale_leads_from_ghl.py --idle-days 3

Output CSV: /app/tmp/stale_ghl_sync_YYYYMMDD_HHMMSS.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import text  # noqa: E402

from app.adapters.ghl import GHLClient, GHLError  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_sync_session  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("sync_stale_leads")

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_IDLE_DAYS = 7
_GHL_CALL_DELAY_SECONDS = 0.2   # 5 req/s — well inside GHL's ~100 req/s limit
_TMP_DIR = Path("/app/tmp")

# Tags in GHL that indicate the lead should not be called.
# Matched case-insensitively against each tag in the contact's tag list.
_DNC_TAGS = frozenset({
    # Explicit do-not-call variants used in this GHL account
    "do not contact",
    "do not call",
    "do not call again",
    "dnc",
    # Disposition signals
    "not interested",
    "wrong number",
    "opted out",
    "opted_out",
})

# ── SQL: fetch stale active leads ─────────────────────────────────────────────

_STALE_LEADS_SQL = text("""
WITH pending AS (
    SELECT entity_id
    FROM scheduled_jobs
    WHERE status IN ('pending', 'claimed', 'running')
    GROUP BY entity_id
),
last_done AS (
    SELECT DISTINCT ON (entity_id)
        entity_id,
        job_type      AS last_job_type,
        status        AS last_job_status,
        updated_at    AS last_activity_at
    FROM scheduled_jobs
    WHERE status NOT IN ('pending', 'claimed', 'running')
    ORDER BY entity_id, updated_at DESC
)
SELECT
    ls.contact_id,
    ls.campaign_name,
    ls.ai_campaign_value   AS db_vm_tier,
    ls.status              AS db_status,
    ls.do_not_call         AS db_do_not_call,
    ls.ai_campaign         AS db_ai_campaign,
    ls.last_call_status    AS db_last_call_status,
    d.last_job_type,
    d.last_job_status,
    d.last_activity_at,
    EXTRACT(EPOCH FROM (NOW() - d.last_activity_at)) / 86400 AS idle_days
FROM lead_state ls
LEFT JOIN pending    p ON p.entity_id  = ls.contact_id
LEFT JOIN last_done  d ON d.entity_id  = ls.contact_id
WHERE
    -- active by dashboard definition
    (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal'))
    AND ls.do_not_call IS NOT TRUE
    AND (ls.ai_campaign_value IS NULL OR ls.ai_campaign_value != '3')
    -- no job in flight
    AND p.entity_id IS NULL
    -- idle longer than threshold
    AND (
        d.last_activity_at IS NULL
        OR d.last_activity_at < NOW() - (:idle_days * INTERVAL '1 day')
    )
ORDER BY d.last_activity_at NULLS FIRST
""")

_UPDATE_LEAD_SQL = text("""
UPDATE lead_state
SET
    do_not_call        = :do_not_call,
    ai_campaign        = :ai_campaign,
    ai_campaign_value  = :ai_campaign_value,
    last_call_status   = :last_call_status,
    version            = version + 1,
    updated_at         = NOW()
WHERE contact_id = :contact_id
""")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_field_id_map(location_fields: list[dict]) -> dict[str, str]:
    """Build {field_id → field_name} from GHL location fields list."""
    return {f["id"]: f.get("name", f.get("fieldKey", f["id"])) for f in location_fields}


def _extract_custom_fields(contact: dict, id_to_name: dict[str, str]) -> dict[str, Any]:
    """
    Convert GHL's [{id, value}] customFields array into {field_name: value}.
    Unknown field IDs are kept under their raw ID.
    """
    result: dict[str, Any] = {}
    for cf in contact.get("customFields", []):
        fid = cf.get("id", "")
        name = id_to_name.get(fid, fid)
        value = cf.get("value")
        # Multi-select fields return lists; unwrap single-item lists to a string
        if isinstance(value, list):
            value = value[0] if len(value) == 1 else ", ".join(str(v) for v in value)
        result[name] = value
    return result


def _is_dnc(contact: dict, tags: list[str]) -> bool:
    """True if GHL marks this contact as do-not-call."""
    if contact.get("dnd"):
        return True
    tag_set = {t.lower() for t in tags}
    return bool(tag_set & {t.lower() for t in _DNC_TAGS})


def _recommend_action(
    ghl_found: bool,
    dnc: bool,
    ghl_ai_campaign: str | None,
    ghl_ai_campaign_value: str | None,
    ghl_mark_as_lead: str | None,
    db_vm_tier: str | None,
) -> str:
    if not ghl_found:
        return "review__not_found"
    if dnc:
        return "finalize__do_not_call"

    campaign_on = (ghl_ai_campaign or "").strip().lower() in {"yes", "true", "1"}
    campaign_off = (ghl_ai_campaign or "").strip().lower() in {"no", "false", "0"}
    mark_as_lead = (ghl_mark_as_lead or "").strip().lower() in {"yes", "true", "1"}
    ghl_tier = (ghl_ai_campaign_value or "").strip()

    if ghl_tier == "3":
        return "finalize__tier3_complete"
    if campaign_off and mark_as_lead:
        return "finalize__converted"
    if campaign_off:
        return "finalize__campaign_off"
    if db_vm_tier == "2":
        return "finalize__tier2_terminal"
    if campaign_on or not ghl_ai_campaign:
        if db_vm_tier in ("0", "1", None):
            return "reschedule__call_next_tier"
    if not ghl_ai_campaign:
        return "review__no_campaign_flag"
    return "review__manual"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync stale leads from GHL")
    parser.add_argument("--apply", action="store_true",
                        help="Write GHL field values back to lead_state (default: dry-run)")
    parser.add_argument("--idle-days", type=int, default=_DEFAULT_IDLE_DAYS,
                        help=f"Leads idle longer than this many days (default {_DEFAULT_IDLE_DAYS})")
    args = parser.parse_args()

    settings = get_settings()
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = _TMP_DIR / f"stale_ghl_sync_{ts}.csv"

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("=== sync_stale_leads_from_ghl | mode=%s idle_days=%d ===", mode, args.idle_days)

    # ── Step 1: load stale leads from DB ─────────────────────────────────────
    with get_sync_session() as session:
        rows = session.execute(_STALE_LEADS_SQL, {"idle_days": args.idle_days}).fetchall()

    if not rows:
        logger.info("No stale leads found — nothing to do.")
        return

    logger.info("Found %d stale leads (idle > %d days)", len(rows), args.idle_days)

    # ── Step 2: fetch GHL location fields once ────────────────────────────────
    ghl = GHLClient(settings=settings)
    try:
        location_fields = ghl.get_location_fields()
    except GHLError as exc:
        logger.error("Failed to fetch GHL location fields: %s — aborting", exc)
        sys.exit(1)

    id_to_name = _build_field_id_map(location_fields)
    logger.info("GHL location field map loaded: %d fields", len(id_to_name))

    # Build name→id map for the fields we care about, for reverse lookups
    name_to_id = {v: k for k, v in id_to_name.items()}

    # Resolve field names from settings so we can look them up in custom fields
    field_ai_campaign       = settings.ghl_field_ai_campaign
    field_ai_campaign_value = settings.ghl_field_ai_campaign_value
    field_last_call_status  = settings.ghl_field_last_call_status
    field_mark_as_lead      = settings.ghl_field_mark_as_lead
    field_classification    = settings.ghl_field_ai_lead_classification

    # ── Step 3: fetch each contact from GHL ──────────────────────────────────
    _CSV_COLS = [
        "contact_id", "ghl_contact_id", "ghl_name",
        "db_campaign", "db_vm_tier", "db_status",
        "db_last_job_type", "db_last_job_status", "idle_days",
        "ghl_dnd", "ghl_tags",
        "ghl_ai_campaign", "ghl_ai_campaign_value",
        "ghl_last_call_status", "ghl_mark_as_lead", "ghl_classification",
        "recommended_action",
    ]

    result_rows: list[dict] = []

    # Counters for summary
    counts: dict[str, int] = {}

    for i, row in enumerate(rows, 1):
        contact_id = row.contact_id
        db_vm_tier = row.db_vm_tier
        idle = round(float(row.idle_days or 0), 1) if row.idle_days else None
        idle_str = f"{idle:.1f}" if idle is not None else "never"

        logger.info(
            "[%d/%d] contact_id=%s campaign=%s tier=%s idle=%s days",
            i, len(rows), contact_id, row.campaign_name, db_vm_tier, idle_str,
        )

        # GHL lookup
        ghl_contact: dict | None = None
        ghl_error: str = ""
        try:
            ghl_contact = ghl.search_contact_by_phone(contact_id)
            time.sleep(_GHL_CALL_DELAY_SECONDS)
        except GHLError as exc:
            ghl_error = str(exc)
            logger.warning("GHL error for %s: %s", contact_id, exc)
            time.sleep(_GHL_CALL_DELAY_SECONDS)

        if ghl_contact is None and not ghl_error:
            logger.warning("Contact not found in GHL: %s", contact_id)

        ghl_found = ghl_contact is not None

        # Extract fields
        ghl_contact_id = ghl_contact.get("id", "") if ghl_found else ""
        first = ghl_contact.get("firstName", "") if ghl_found else ""
        last = ghl_contact.get("lastName", "") if ghl_found else ""
        ghl_name = f"{first} {last}".strip() if ghl_found else ""
        tags: list[str] = ghl_contact.get("tags", []) if ghl_found else []
        dnc = _is_dnc(ghl_contact, tags) if ghl_found else False

        custom: dict[str, Any] = {}
        if ghl_found:
            custom = _extract_custom_fields(ghl_contact, id_to_name)

        ghl_ai_campaign       = custom.get(field_ai_campaign)        if field_ai_campaign       else None
        ghl_ai_campaign_value = custom.get(field_ai_campaign_value)  if field_ai_campaign_value else None
        ghl_last_call_status  = custom.get(field_last_call_status)   if field_last_call_status  else None
        ghl_mark_as_lead      = custom.get(field_mark_as_lead)       if field_mark_as_lead      else None
        ghl_classification    = custom.get(field_classification)     if field_classification    else None

        action = _recommend_action(
            ghl_found=ghl_found,
            dnc=dnc,
            ghl_ai_campaign=ghl_ai_campaign,
            ghl_ai_campaign_value=ghl_ai_campaign_value,
            ghl_mark_as_lead=ghl_mark_as_lead,
            db_vm_tier=db_vm_tier,
        )
        counts[action] = counts.get(action, 0) + 1

        result_rows.append({
            "contact_id":           contact_id,
            "ghl_contact_id":       ghl_contact_id,
            "ghl_name":             ghl_name,
            "db_campaign":          row.campaign_name or "",
            "db_vm_tier":           db_vm_tier or "",
            "db_status":            row.db_status or "",
            "db_last_job_type":     row.last_job_type or "",
            "db_last_job_status":   row.last_job_status or "",
            "idle_days":            idle_str,
            "ghl_dnd":              str(dnc),
            "ghl_tags":             "; ".join(tags),
            "ghl_ai_campaign":      ghl_ai_campaign or "",
            "ghl_ai_campaign_value": ghl_ai_campaign_value or "",
            "ghl_last_call_status": ghl_last_call_status or "",
            "ghl_mark_as_lead":     ghl_mark_as_lead or "",
            "ghl_classification":   ghl_classification or "",
            "recommended_action":   action,
        })

        # Apply DB sync if requested
        if args.apply and ghl_found:
            new_do_not_call = dnc
            with get_sync_session() as session:
                session.execute(_UPDATE_LEAD_SQL, {
                    "contact_id":        contact_id,
                    "do_not_call":       new_do_not_call,
                    "ai_campaign":       ghl_ai_campaign,
                    "ai_campaign_value": ghl_ai_campaign_value,
                    "last_call_status":  ghl_last_call_status,
                })
                session.commit()
            logger.info("DB updated for %s (do_not_call=%s ai_campaign=%s tier=%s)",
                        contact_id, new_do_not_call, ghl_ai_campaign, ghl_ai_campaign_value)

    # ── Step 4: write CSV ─────────────────────────────────────────────────────
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLS)
        writer.writeheader()
        writer.writerows(result_rows)

    logger.info("CSV written → %s (%d rows)", csv_path, len(result_rows))

    # ── Step 5: summary ───────────────────────────────────────────────────────
    logger.info("=== Summary (mode=%s) ===", mode)
    for action, count in sorted(counts.items()):
        logger.info("  %-40s %d", action, count)
    logger.info("Total processed: %d", len(result_rows))
    logger.info("CSV: %s", csv_path)

    if not args.apply:
        logger.info("Dry-run complete — no DB changes made. Re-run with --apply to sync.")


if __name__ == "__main__":
    main()
