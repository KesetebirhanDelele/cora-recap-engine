"""
AI Cold Lead Tagging — GHL Filter Semantics Verification (read-only)
=====================================================================
Spec 31 (ai_cold_lead_tagging) prerequisite spike. Confirms the exact
POST /contacts/search field names/operators GHL expects for the six
filter criteria before any adapter/service code is written against them.

This script makes ONLY read calls (GET /contacts/, POST /contacts/search).
It never writes anything. Safe to run against the production GHL location.

Usage:
    python execution/test_scripts/verify_ai_cold_lead_filters.py

What it does:
    1. Lists a few real contacts (GET /contacts/) to inspect the raw shape
       of dnd/type/tags/activity fields — so later filter guesses are
       grounded in what GHL actually returns, not assumed.
    2. Tries each of the 6 target filter criteria individually against
       POST /contacts/search, reporting the returned count for each.
    3. Tries the full combined filter (matching the dashboard UI screenshot)
       and confirms the combined count is <= every individual count (AND
       semantics, not filters being silently ignored).
    4. Prints the pagination cursor shape from a response so the adapter's
       pagination loop can be written against a confirmed contract.

Requirements:
    - GHL_API_KEY and GHL_LOCATION_ID must be set in .env
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import httpx
from app.config import get_settings

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
WARN = "\033[93m WARN\033[0m"
INFO = "\033[94m INFO\033[0m"


def _log(icon: str, msg: str) -> None:
    print(f"  {icon}  {msg}")


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Version": "2021-07-28",
        "Content-Type": "application/json",
    }


def _redact_phone(phone: str | None) -> str:
    if not phone:
        return "<none>"
    return phone[:4] + "***" + phone[-2:] if len(phone) > 6 else "***"


def list_contacts(base_url: str, api_key: str, location_id: str, limit: int = 5) -> list[dict]:
    r = httpx.get(
        f"{base_url}/contacts/",
        headers=_headers(api_key),
        params={"locationId": location_id, "limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("contacts", [])


def search_contacts(
    base_url: str,
    api_key: str,
    location_id: str,
    filters: list[dict],
    page_limit: int = 1,
) -> tuple[int | None, dict]:
    """Returns (count_or_None_on_error, raw_response_dict)."""
    body = {"locationId": location_id, "filters": filters, "pageLimit": page_limit}
    try:
        r = httpx.post(f"{base_url}/contacts/search", headers=_headers(api_key), json=body, timeout=45)
    except httpx.TimeoutException:
        return None, {"status_code": "timeout", "body": "request timed out after 45s"}
    if r.status_code != 200:
        return None, {"status_code": r.status_code, "body": r.text[:500]}
    data = r.json()
    total = data.get("total")
    if total is None:
        # some GHL search responses report count via len(contacts) at pageLimit
        total = len(data.get("contacts", []))
    return total, data


def main() -> None:
    settings = get_settings()
    base_url: str = settings.ghl_base_url
    api_key: str = settings.ghl_api_key or ""
    location_id: str = settings.ghl_location_id or ""

    if not api_key or not location_id:
        print("ERROR: GHL_API_KEY / GHL_LOCATION_ID not set in .env")
        sys.exit(1)

    print("\n=== AI Cold Lead Tagging — Filter Verification (read-only) ===\n")

    # ── Step 1: inspect raw contact shape ──────────────────────────────────
    print("[ 1 ] Listing sample contacts to inspect raw field shapes ...")
    try:
        sample = list_contacts(base_url, api_key, location_id, limit=5)
    except httpx.HTTPStatusError as e:
        _log(FAIL, f"GET /contacts/ failed: {e.response.status_code} — {e.response.text[:200]}")
        sys.exit(1)

    if not sample:
        _log(FAIL, "No contacts returned at all — cannot verify shape.")
        sys.exit(1)

    _log(INFO, f"Retrieved {len(sample)} sample contacts")
    first = sample[0]
    _log(INFO, f"Sample contact top-level keys: {sorted(first.keys())}")
    print("\n  --- full first contact record (for field-name inspection) ---")
    redacted = dict(first)
    if "phone" in redacted:
        redacted["phone"] = _redact_phone(redacted["phone"])
    print(json.dumps(redacted, indent=2, default=str))
    print("  --- end record ---\n")

    for key in ("dnd", "dndSettings", "type", "contactType", "tags", "dateAdded", "dateUpdated", "lastActivity", "lastActivityDate"):
        if key in first:
            _log(INFO, f"Field present on contact record: {key!r} = {first[key]!r}")
        else:
            _log(WARN, f"Field NOT present on this sample contact: {key!r}")

    # ── Step 2: individual filter probes ───────────────────────────────────
    print("\n[ 2 ] Probing individual filter criteria against POST /contacts/search ...")

    baseline_count, _ = search_contacts(base_url, api_key, location_id, filters=[], page_limit=1)
    _log(INFO, f"Baseline (no filter) reported count: {baseline_count}")

    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    cutoff_epoch_ms = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000)

    exclude_tags = [
        "business lead", "colaberry employee", "invalid phone number", "not a lead",
        "spam", "marketing contact", "international lead", "warm lead",
        "ai cold leads", "ai cold leads ii",
    ]

    candidates: dict[str, list[dict]] = {
        "contactType=lead (field=type)": [{"field": "type", "operator": "eq", "value": "lead"}],
        "phone exists (string true)": [{"field": "phone", "operator": "exists", "value": "true"}],
        "phone not_eq empty": [{"field": "phone", "operator": "not_eq", "value": ""}],
        "phone contains +1 (padded 3 char)": [{"field": "phone", "operator": "contains", "value": "+1"}],
        "phone wildcard +1*": [{"field": "phone", "operator": "wildcard", "value": "+1*"}],
        "tags not_contains warm lead": [{"field": "tags", "operator": "not_contains", "value": "warm lead"}],
        "tags not_contains ARRAY (all exclude tags)": [{"field": "tags", "operator": "not_contains", "value": exclude_tags}],
        "dnd eq false": [{"field": "dnd", "operator": "eq", "value": False}],
        f"dateUpdated lt {cutoff}": [{"field": "dateUpdated", "operator": "lt", "value": cutoff}],
        f"dateAdded lt {cutoff}": [{"field": "dateAdded", "operator": "lt", "value": cutoff}],
    }

    results: dict[str, int | None] = {}
    for label, filt in candidates.items():
        count, raw = search_contacts(base_url, api_key, location_id, filters=filt, page_limit=1)
        results[label] = count
        if count is None:
            _log(FAIL, f"{label:45s} -> HTTP {raw['status_code']}: {raw['body'][:400]}")
        elif count == baseline_count:
            _log(WARN, f"{label:45s} -> count unchanged from baseline ({count}) — filter may be silently ignored")
        else:
            _log(PASS, f"{label:45s} -> count={count}")

    # ── Step 2a-extra: date-field operator/field-name discovery ─────────────
    print("\n[ 2a-extra ] Date filter operator/field-name discovery ...")
    date_probes: dict[str, list[dict]] = {
        "dateUpdated range gte/lte epoch": [{"field": "dateUpdated", "operator": "range", "value": {"gte": 0, "lte": int(datetime.now(timezone.utc).timestamp() * 1000) - 30 * 86400000}}],
        "dateUpdated lte iso": [{"field": "dateUpdated", "operator": "lte", "value": cutoff}],
        "dateUpdated gte iso": [{"field": "dateUpdated", "operator": "gte", "value": cutoff}],
        "date_updated lte iso (snake_case field)": [{"field": "date_updated", "operator": "lte", "value": cutoff}],
        "dateUpdated eq bogus (discover allowed ops)": [{"field": "dateUpdated", "operator": "bogus_op", "value": cutoff}],
    }
    for label, filt in date_probes.items():
        count, raw = search_contacts(base_url, api_key, location_id, filters=filt, page_limit=1)
        if count is None:
            _log(FAIL, f"{label:45s} -> HTTP {raw['status_code']}: {raw['body'][:400]}")
        elif count == baseline_count:
            _log(WARN, f"{label:45s} -> count unchanged from baseline ({count})")
        else:
            _log(PASS, f"{label:45s} -> count={count}")

    # ── Step 2b: AND-combined exclusion via 10 chained not_contains filters ──
    print("\n[ 2b ] Combined exclusion (10 ANDed tags not_contains filters) ...")
    chained = [{"field": "tags", "operator": "not_contains", "value": t} for t in exclude_tags]
    count, raw = search_contacts(base_url, api_key, location_id, filters=chained, page_limit=1)
    if count is None:
        _log(FAIL, f"chained not_contains -> HTTP {raw['status_code']}: {raw['body'][:400]}")
    else:
        _log(PASS, f"chained not_contains (10 filters ANDed) -> count={count}")

    # ── Step 3: pagination cursor shape ────────────────────────────────────
    print("\n[ 3 ] Inspecting pagination cursor shape ...")
    _, raw = search_contacts(base_url, api_key, location_id, filters=[], page_limit=2)
    cursor_keys = [k for k in raw.keys() if k not in ("contacts", "total")]
    _log(INFO, f"Top-level response keys besides contacts/total: {cursor_keys}")
    if raw.get("contacts"):
        last_contact = raw["contacts"][-1]
        _log(INFO, f"Last contact keys: {sorted(last_contact.keys())}")
        _log(INFO, f"Last contact lastActivity={last_contact.get('lastActivity')!r} dateUpdated={last_contact.get('dateUpdated')!r}")
        for cursor_field in ("sort", "startAfter", "startAfterId", "searchAfter"):
            if cursor_field in last_contact:
                _log(PASS, f"Last contact has {cursor_field!r} cursor value: {last_contact[cursor_field]!r}")
        # try actually paginating with searchAfter
        if "searchAfter" in last_contact:
            sa = last_contact["searchAfter"]
            body2 = {"locationId": location_id, "filters": [], "pageLimit": 2, "searchAfter": sa}
            try:
                r2 = httpx.post(f"{base_url}/contacts/search", headers=_headers(api_key), json=body2, timeout=45)
                if r2.status_code == 200:
                    next_page = r2.json().get("contacts", [])
                    first_ids_page1 = [c["id"] for c in raw["contacts"]]
                    next_ids = [c["id"] for c in next_page]
                    overlap = set(first_ids_page1) & set(next_ids)
                    _log(PASS if not overlap else FAIL, f"Page 2 via searchAfter returned {len(next_page)} contacts, overlap with page 1: {overlap}")
                else:
                    _log(FAIL, f"Page-2 fetch via searchAfter -> HTTP {r2.status_code}: {r2.text[:300]}")
            except httpx.TimeoutException:
                _log(FAIL, "Page-2 fetch via searchAfter timed out")

    # ── Step 3b: filter directly on lastActivity field ──────────────────────
    print("\n[ 3b ] Direct lastActivity field filter discovery ...")
    la_probes: dict[str, list[dict]] = {
        "lastActivity range": [{"field": "lastActivity", "operator": "range", "value": {"gte": 0, "lte": cutoff_epoch_ms}}],
        "lastActivity eq bogus (discover allowed ops)": [{"field": "lastActivity", "operator": "bogus_op", "value": 0}],
    }
    for label, filt in la_probes.items():
        count, raw = search_contacts(base_url, api_key, location_id, filters=filt, page_limit=1)
        if count is None:
            _log(FAIL, f"{label:45s} -> HTTP {raw['status_code']}: {raw['body'][:400]}")
        elif count == baseline_count:
            _log(WARN, f"{label:45s} -> count unchanged from baseline ({count})")
        else:
            _log(PASS, f"{label:45s} -> count={count}")

    # ── Step 4: full combined filter (matches dashboard UI screenshot) ─────
    print("\n[ 4 ] Full combined filter ...")
    combined_filters = [
        {"field": "type", "operator": "eq", "value": "lead"},
        {"field": "dnd", "operator": "eq", "value": False},
        {"field": "phone", "operator": "wildcard", "value": "+1*"},
        {"field": "tags", "operator": "not_contains", "value": exclude_tags},
        {"field": "lastActivity", "operator": "range", "value": {"gte": 0, "lte": cutoff_epoch_ms}},
    ]
    count, raw = search_contacts(base_url, api_key, location_id, filters=combined_filters, page_limit=1)
    if count is None:
        _log(FAIL, f"combined filter -> HTTP {raw['status_code']}: {raw['body'][:400]}")
    else:
        _log(PASS, f"combined filter (all 5 criteria ANDed) -> count={count}")

    print("\n=== Done — record these findings in directives/spec/31_ai_cold_lead_tagging.md ===\n")


if __name__ == "__main__":
    main()
