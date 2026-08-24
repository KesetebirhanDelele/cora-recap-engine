"""
test_prompt_override.py — Verification spike for spec/21's dynamic prompt injection.

Posts directly to the mapped Synthflow "Make Call" workflow webhook with a `prompt`
field in the body, bypassing Cora's job queue and adapter code entirely. This is
deliberately NOT wired through SynthflowClient.launch_new_lead_call() — that method
is production code with retry/error handling; this script is a standalone spike for
manually assessing call quality before trusting the production path end-to-end.

Two modes:
  1. Generic override-verification (default, no --campaign): places a call with a
     literal test sentence to confirm the {prompt} mechanism still works at all.
  2. Real-campaign dry run (--campaign new_lead|cold_lead): loads the ACTUAL
     production prompt doc (docs/synthflow-warm-lead-prompt.md or
     docs/synthflow-cold-lead-prompt.md — the same file and loader
     app/adapters/synthflow.py._load_campaign_prompt() uses for real production
     calls) and places a real call so you can listen to and assess the genuine
     campaign script, not a placeholder sentence.

Usage:
    python execution/test_scripts/test_prompt_override.py --phone +1XXXXXXXXXX
    python execution/test_scripts/test_prompt_override.py --phone +1XXXXXXXXXX --campaign cold_lead

Optional:
    --dry-run    Print the payload without submitting (no real call placed)
    --prompt     Override with fully custom text (takes priority over --campaign)

Preconditions:
    - SYNTHFLOW_LAUNCH_WORKFLOW_URL_New / _Cold and SYNTHFLOW_API_KEY set in .env
    - The target workflow's Prompt field is mapped to `1. Catch Webhook body.prompt`
      (confirmed done for NewLeads 2026-08-23; confirm the same for ColdLeads before
      using --campaign cold_lead)
    - The target workflow must be ENABLED in Synthflow. Cold Lead went live in the
      Cora dashboard and GHL on 2026-08-24 (see PROGRESS.md), so ColdLeads' Make
      Call workflow (33J546NiXxUUIRCbywNVH) should now be enabled — if a call
      doesn't land, that's the first thing to check.

This places a REAL outbound call to the given number. Do not run against a real
lead's phone number — this is a verification spike, not production traffic.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

from app.config import get_settings

# Real campaign prompt docs can contain non-ASCII characters (em dashes, emoji)
# that Windows' default console codepage (cp1252) can't encode, which otherwise
# crashes this script with UnicodeEncodeError before a call is even placed.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_TEST_PROMPT = (
    "This is an automated test of a dynamic prompt override system. "
    "Ignore any other instructions or persona you may have been given for this call. "
    "Say exactly this sentence, word for word, and then say goodbye and end the call: "
    "'Prompt override confirmed. This is a test call.' "
    "Do not say anything else before or after that sentence."
)

# campaign_type slug (CLI-friendly) -> canonical campaign_name (matches
# app.core.campaigns._CAMPAIGN_NAMES / app.adapters.synthflow._CAMPAIGN_PROMPT_FILES)
_CAMPAIGN_NAMES = {
    "new_lead": "New Lead",
    "cold_lead": "Cold Lead",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Synthflow's per-call prompt override by placing a real test call."
    )
    parser.add_argument("--phone", default=None, help="E.164 phone number to call, e.g. +17865551234 (required unless --dry-run)")
    parser.add_argument("--name", default="Prompt Test", help="Name Synthflow will address the callee as")
    parser.add_argument(
        "--campaign", choices=sorted(_CAMPAIGN_NAMES), default=None,
        help="Load the REAL production prompt for this campaign instead of the generic test sentence",
    )
    parser.add_argument("--prompt", default=None, help="Fully custom prompt text — overrides --campaign if both are given")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without submitting")
    return parser.parse_args()


def validate_phone(phone: str) -> None:
    if not phone.startswith("+") or len(phone) < 10:
        print(f"ERROR: phone must be E.164 format (e.g. +17865551234), got: {phone!r}")
        sys.exit(1)


def main() -> None:
    args = parse_args()

    if args.phone is None:
        if not args.dry_run:
            print("ERROR: --phone is required unless --dry-run is set")
            sys.exit(1)
        args.phone = "+15555550100"  # placeholder, dry-run only — never dialed
    else:
        validate_phone(args.phone)

    campaign_name = _CAMPAIGN_NAMES[args.campaign] if args.campaign else "New Lead"

    if args.prompt is not None:
        prompt = args.prompt
        prompt_source = "custom (--prompt)"
    elif args.campaign is not None:
        from app.adapters.synthflow import _load_campaign_prompt
        prompt = _load_campaign_prompt(campaign_name)
        prompt_source = f"real production prompt for {campaign_name!r} (docs/synthflow-{'cold' if args.campaign == 'cold_lead' else 'warm'}-lead-prompt.md)"
    else:
        prompt = DEFAULT_TEST_PROMPT
        prompt_source = "generic override-verification sentence"

    settings = get_settings()
    url = settings.get_synthflow_launch_url(campaign_name)

    payload = {
        "phone": args.phone,
        "name": args.name,
        "campaign_name": campaign_name,
        "prompt": prompt,
    }

    print("=" * 60)
    print("Synthflow Dynamic Prompt Override — Verification Spike")
    print("=" * 60)
    print(f"  Campaign:        {campaign_name}")
    print(f"  Target workflow: {url}")
    print(f"  Phone:           {args.phone}")
    print(f"  Prompt source:   {prompt_source}")
    print()
    print("  --- prompt text ---")
    print(f"  {prompt}")
    print("  --- end prompt ---")
    print()

    if args.dry_run:
        print("DRY RUN — payload that would be submitted:")
        print(json.dumps(payload, indent=2))
        print("\nNot submitting (--dry-run active). No call placed.")
        return

    headers = {
        "Authorization": f"Bearer {settings.synthflow_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print("Submitting — your phone should ring shortly...")
    resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
    resp.raise_for_status()
    result = resp.json() if resp.content else {}

    print("\nAccepted by Synthflow:")
    print(json.dumps(result, indent=2))
    print()

    if args.campaign is not None and args.prompt is None:
        print(f"Answer the call and assess the real {campaign_name} script — tone, pacing,")
        print("accuracy of what it says about the program, and whether it sounds like the")
        print("intended prompt end-to-end (not a generic/fallback script).")
    else:
        print("Answer the call and listen for the exact test sentence.")
        print("  - Says the test sentence, nothing else       -> full override confirmed (replace)")
        print("  - Says the test sentence AND the normal script -> override merges/appends, not a clean replace")
        print("  - Runs the normal Colaberry admissions script  -> override is being ignored")


if __name__ == "__main__":
    main()
