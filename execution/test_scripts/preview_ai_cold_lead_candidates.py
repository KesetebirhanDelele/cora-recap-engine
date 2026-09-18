"""
AI Cold Lead Tagging — sample candidate preview (read-only)
=============================================================
Prints N real contacts currently matching the confirmed spec/31 filter, with
enough identifying info to look each one up in GHL's UI. Used for manual
before/after verification ahead of a canary live run (batch_cap temporarily
set low) — this script itself makes NO writes.

Usage:
    python execution/test_scripts/preview_ai_cold_lead_candidates.py [--limit 5]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import httpx
from app.config import get_settings


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview N candidate contacts for AI cold lead tagging")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    settings = get_settings()
    base_url = settings.ghl_base_url
    api_key = settings.ghl_api_key or ""
    location_id = settings.ghl_location_id or ""

    if not api_key or not location_id:
        print("ERROR: GHL_API_KEY / GHL_LOCATION_ID not set in .env")
        sys.exit(1)

    from app.services.ai_cold_lead_tagging import build_search_filters
    filters = build_search_filters(settings)

    body = {"locationId": location_id, "filters": filters, "pageLimit": args.limit}
    r = httpx.post(f"{base_url}/contacts/search", headers=_headers(api_key), json=body, timeout=45)
    r.raise_for_status()
    data = r.json()
    contacts = data.get("contacts", [])
    total = data.get("total")

    print(f"\n=== {len(contacts)} of {total} total matching contacts (spec/31 filter) ===\n")
    for i, c in enumerate(contacts, 1):
        name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or "<no name>"
        phone = _redact_phone(c.get("phone"))
        tags = c.get("tags") or []
        last_activity = c.get("lastActivity")
        contact_id = c.get("id")
        ghl_url = f"https://app.gohighlevel.com/v2/location/{location_id}/contacts/detail/{contact_id}"
        print(f"{i}. {name}")
        print(f"   contact_id:    {contact_id}")
        print(f"   phone:         {phone}")
        print(f"   tags:          {tags}")
        print(f"   last_activity: {last_activity}")
        print(f"   GHL link:      {ghl_url}  (verify this opens the right contact — pattern not independently confirmed)")
        print()


if __name__ == "__main__":
    main()
