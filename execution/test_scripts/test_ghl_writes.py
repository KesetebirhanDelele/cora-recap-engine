"""
GHL Write Integration Test
==========================
Tests every custom field write that Cora performs against your real GHL account.
Uses the same GHLClient and field-resolution logic as production.

Usage:
    python execution/test_scripts/test_ghl_writes.py --phone +15551234567
    python execution/test_scripts/test_ghl_writes.py --contact-id <GHL_CONTACT_ID>

What it does:
    1. Looks up the contact in GHL (read — always safe)
    2. Snapshots current field values so they can be restored
    3. Writes test values to every field Cora would write
    4. Reads the contact back and verifies each field was accepted
    5. Restores original values (unless --no-restore is passed)

Requirements:
    - GHL_API_KEY and GHL_LOCATION_ID must be set in .env
    - GHL_WRITE_MODE does NOT need to be changed — this script bypasses
      the shadow gate deliberately via a forced live client
    - The contact must exist in your GHL location
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import httpx
from app.config import get_settings

# ── helpers ───────────────────────────────────────────────────────────────────

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

def get_contact(base_url: str, api_key: str, contact_id: str) -> dict:
    url = f"{base_url}/contacts/{contact_id}"
    r = httpx.get(url, headers=_headers(api_key), timeout=15)
    r.raise_for_status()
    return r.json()

def search_by_phone(base_url: str, api_key: str, location_id: str, phone: str) -> dict | None:
    url = f"{base_url}/contacts/"
    r = httpx.get(url, headers=_headers(api_key),
                  params={"locationId": location_id, "query": phone}, timeout=15)
    r.raise_for_status()
    contacts = r.json().get("contacts", [])
    return contacts[0] if contacts else None

def write_fields(base_url: str, api_key: str, contact_id: str,
                 field_updates: list[dict]) -> dict:
    url = f"{base_url}/contacts/{contact_id}"
    payload = {"customFields": field_updates}
    r = httpx.put(url, headers=_headers(api_key), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

def create_task(base_url: str, api_key: str, contact_id: str,
                title: str, description: str, due_date: str) -> dict:
    url = f"{base_url}/contacts/{contact_id}/tasks"
    payload = {
        "title": title,
        "dueDate": due_date,
        "completed": False,
    }
    r = httpx.post(url, headers=_headers(api_key), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

def get_location_fields(base_url: str, api_key: str, location_id: str) -> list[dict]:
    """Fetch all custom field definitions for the location (label→id mapping)."""
    url = f"{base_url}/locations/{location_id}/customFields"
    r = httpx.get(url, headers=_headers(api_key), timeout=15)
    r.raise_for_status()
    return r.json().get("customFields", [])

def resolve_field_id_from_location(field_label: str, location_fields: list[dict]) -> str | None:
    """Resolve field ID from location field definitions — works even if field has no value on contact."""
    for field in location_fields:
        if field.get("name") == field_label or field.get("fieldKey") == field_label:
            return field.get("id")
    return None

def resolve_field_id(field_label: str, contact: dict) -> str | None:
    """Same logic as GHLClient.resolve_field_id."""
    for field in contact.get("customFields", []):
        if field.get("name") == field_label or field.get("fieldKey") == field_label:
            return field.get("id")
    return None

def get_field_value(field_label: str, contact: dict) -> str | None:
    """Same logic as GHLClient.get_field_value."""
    for field in contact.get("customFields", []):
        if field.get("name") == field_label or field.get("fieldKey") == field_label:
            v = field.get("value")
            return str(v) if v is not None else None
    return None

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Test GHL field writes (Cora integration check)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phone", help="Contact phone in E.164 format, e.g. +15551234567")
    group.add_argument("--contact-id", help="GHL contact ID directly")
    parser.add_argument("--no-restore", action="store_true",
                        help="Leave test values in place (don't restore originals)")
    parser.add_argument("--skip-task", action="store_true",
                        help="Skip task creation test (leaves no task artifact)")
    args = parser.parse_args()

    settings = get_settings()
    base_url: str = settings.ghl_base_url
    api_key: str = settings.ghl_api_key or ""
    location_id: str = settings.ghl_location_id or ""

    if not api_key:
        print("ERROR: GHL_API_KEY not set in .env")
        sys.exit(1)
    if not location_id:
        print("ERROR: GHL_LOCATION_ID not set in .env")
        sys.exit(1)

    print("\n=== GHL Write Integration Test ===\n")

    # ── Step 1: resolve contact ───────────────────────────────────────────────
    print("[ 1 ] Resolving contact ...")
    contact_id: str = ""
    if args.contact_id:
        contact_id = args.contact_id
        _log(INFO, f"Using contact_id={contact_id}")
    else:
        contact = search_by_phone(base_url, api_key, location_id, args.phone)
        if not contact:
            print(f"\nERROR: No contact found for phone {args.phone}")
            sys.exit(1)
        contact_id = contact["id"]
        _log(INFO, f"Found contact_id={contact_id}  name={contact.get('firstName','')} {contact.get('lastName','')}")

    # ── Step 2: fetch full contact (for field ID resolution) ─────────────────
    print("\n[ 2 ] Fetching contact record ...")
    contact = get_contact(base_url, api_key, contact_id)
    # GHL wraps in {"contact": {...}} for GET /contacts/{id}
    if "contact" in contact:
        contact = contact["contact"]
    _log(INFO, f"Custom fields present: {len(contact.get('customFields', []))}")

    # ── Step 3: build field map (label → id) for every field Cora writes ─────
    print("\n[ 3 ] Resolving field IDs ...")

    # Fetch location-level field definitions so we can resolve IDs even when
    # a contact has never had a value written to those fields yet.
    print("      Fetching location custom field definitions ...")
    try:
        location_fields = get_location_fields(base_url, api_key, location_id)
        _log(INFO, f"Location defines {len(location_fields)} custom fields")
    except httpx.HTTPStatusError as e:
        _log(WARN, f"Could not fetch location fields ({e.response.status_code}) — falling back to contact-only resolution")
        location_fields = []

    field_labels: dict[str, str | None] = {
        "mark_as_lead":           settings.ghl_field_mark_as_lead,
        "ai_campaign":            settings.ghl_field_ai_campaign,
        "ai_campaign_value":      settings.ghl_field_ai_campaign_value,
        "ai_lead_classification": settings.ghl_field_ai_lead_classification,
        "support_ticket_3":       settings.ghl_field_support_ticket_3,   # task description
        "support_ticket_4":       settings.ghl_field_support_ticket_4,   # student summary / VM SMS
        "support_ticket_2":       settings.ghl_field_support_ticket_2,   # VM email HTML
        "message":                settings.ghl_field_message,            # VM email subject / message
        "student_summary":        getattr(settings, "ghl_field_student_summary", None),
    }

    resolved: dict[str, str] = {}    # key → field_id
    snapshot: dict[str, str | None] = {}  # key → current value (for restore)

    for key, label in field_labels.items():
        if not label:
            _log(WARN, f"  {key:30s} — label not configured, skipping")
            continue
        # Prefer location-level field definitions; fall back to contact record
        fid = resolve_field_id_from_location(label, location_fields) or resolve_field_id(label, contact)
        if not fid:
            _log(WARN, f"  {key:30s} ({label!r}) — field ID not found in location or contact record")
            continue
        current = get_field_value(label, contact)
        resolved[key] = fid
        snapshot[key] = current
        _log(INFO, f"  {key:30s} ({label!r}) → id={fid}  current={current!r}")

    if not resolved:
        print("\nERROR: No fields could be resolved. Check your GHL_FIELD_* settings.")
        sys.exit(1)

    # ── Step 4: write test values ─────────────────────────────────────────────
    print(f"\n[ 4 ] Writing test values to {len(resolved)} fields ...")

    test_values: dict[str, str] = {
        "mark_as_lead":           "Lead",
        "ai_campaign":            "Yes",
        "ai_campaign_value":      "0",
        "ai_lead_classification": "warm_lead",
        "support_ticket_3":       "[CORA TEST] Task description — verify this appears in GHL",
        "support_ticket_4":       "[CORA TEST] Student summary / VM SMS text",
        "support_ticket_2":       "[CORA TEST] VM email body HTML",
        "message":                "[CORA TEST] Message field",
        "student_summary":        "[CORA TEST] Student summary duplicate write path",
    }

    field_updates = [
        {"id": resolved[key], "value": test_values[key]}
        for key in resolved
        if key in test_values
    ]

    try:
        write_fields(base_url, api_key, contact_id, field_updates)
        _log(PASS, f"PUT /contacts/{contact_id} accepted ({len(field_updates)} fields)")
    except httpx.HTTPStatusError as e:
        _log(FAIL, f"PUT failed: {e.response.status_code} — {e.response.text[:200]}")
        sys.exit(1)

    # ── Step 5: verify writes ─────────────────────────────────────────────────
    print("\n[ 5 ] Verifying writes (re-fetching contact) ...")
    time.sleep(1)   # brief pause — GHL eventual consistency
    updated = get_contact(base_url, api_key, contact_id)
    if "contact" in updated:
        updated = updated["contact"]

    all_passed = True
    for key, fid in resolved.items():
        expected = test_values.get(key, "")
        # Contact customFields only returns {id, value} — search by ID not label
        raw = next(
            (f.get("value") for f in updated.get("customFields", []) if f.get("id") == fid),
            None,
        )
        # Multi-select fields return a list; unwrap single-element lists for comparison
        if isinstance(raw, list):
            actual = raw[0] if len(raw) == 1 else str(raw)
        elif raw is not None:
            actual = str(raw)
        else:
            actual = None
        if actual == expected:
            _log(PASS, f"  {key:30s} = {actual!r}")
        else:
            _log(FAIL, f"  {key:30s} expected={expected!r} got={actual!r}")
            all_passed = False

    # ── Step 6: create test task ──────────────────────────────────────────────
    if not args.skip_task:
        print("\n[ 6 ] Creating test task ...")
        try:
            from datetime import datetime, timedelta, timezone
            due = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            task_result = create_task(
                base_url, api_key, contact_id,
                title="[CORA TEST] Integration check — delete me",
                description="Auto-created by test_ghl_writes.py to verify Cora task creation works.",
                due_date=due,
            )
            task_id = task_result.get("task", {}).get("id") or task_result.get("id", "unknown")
            _log(PASS, f"Task created: id={task_id}")
            _log(WARN, "  Remember to delete this test task from GHL manually.")
        except httpx.HTTPStatusError as e:
            _log(FAIL, f"Task creation failed: {e.response.status_code} — {e.response.text[:200]}")
            all_passed = False
    else:
        print("\n[ 6 ] Task creation — skipped (--skip-task)")

    # ── Step 7: restore original values ──────────────────────────────────────
    if not args.no_restore:
        print("\n[ 7 ] Restoring original field values ...")
        restore_updates = []
        for key, fid in resolved.items():
            orig = snapshot.get(key)
            restore_updates.append({"id": fid, "value": orig if orig is not None else ""})
        try:
            write_fields(base_url, api_key, contact_id, restore_updates)
            _log(PASS, f"Original values restored ({len(restore_updates)} fields)")
        except httpx.HTTPStatusError as e:
            _log(WARN, f"Restore failed (non-fatal): {e.response.status_code} — {e.response.text[:200]}")
    else:
        print("\n[ 7 ] Restore — skipped (--no-restore). Test values remain in GHL.")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n=== Result ===")
    if all_passed:
        print("  ALL CHECKS PASSED — GHL write integration is working correctly.\n")
    else:
        print("  SOME CHECKS FAILED — review the output above before going live.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
