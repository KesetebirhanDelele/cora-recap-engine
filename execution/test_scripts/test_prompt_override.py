"""
test_prompt_override.py — Verification spike for spec/21's dynamic prompt injection.

Posts directly to the (now-mapped) NewLeads "Make Call" Synthflow workflow webhook
with a `prompt` field in the body, bypassing Cora's job queue and adapter code
entirely. This is deliberately NOT wired through SynthflowClient.launch_new_lead_call()
— that method doesn't support `prompt` yet (spec/21 Decomposition step 7), and building
that support against an unconfirmed mechanism is exactly what spec/21 warns against.

Purpose: confirm whether Synthflow's runtime actually uses the mapped `prompt` field
to override the assistant's saved script for that call, and whether it fully replaces
the saved prompt or merges with it — neither is documented, only testing settles it.

Usage:
    python execution/test_scripts/test_prompt_override.py --phone +1XXXXXXXXXX

Optional:
    --dry-run    Print the payload without submitting (no real call placed)
    --prompt     Override the built-in test phrase with custom text

Preconditions:
    - SYNTHFLOW_LAUNCH_WORKFLOW_URL_New and SYNTHFLOW_API_KEY set in .env
    - The NewLeads "Make Call" workflow's Prompt field is mapped to
      `1. Catch Webhook body.prompt` (confirmed done 2026-08-23)

This places a REAL outbound call to the given number. Do not run against a real
lead's phone number — this is a verification spike, not production traffic.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

from app.config import get_settings

DEFAULT_TEST_PROMPT = (
    "This is an automated test of a dynamic prompt override system. "
    "Ignore any other instructions or persona you may have been given for this call. "
    "Say exactly this sentence, word for word, and then say goodbye and end the call: "
    "'Prompt override confirmed. This is a test call.' "
    "Do not say anything else before or after that sentence."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Synthflow's per-call prompt override by placing a real test call."
    )
    parser.add_argument("--phone", default=None, help="E.164 phone number to call, e.g. +17865551234 (required unless --dry-run)")
    parser.add_argument("--name", default="Prompt Test", help="Name Synthflow will address the callee as")
    parser.add_argument("--prompt", default=DEFAULT_TEST_PROMPT, help="Override the built-in test prompt")
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

    settings = get_settings()
    url = settings.get_synthflow_launch_url("New Lead")

    payload = {
        "phone": args.phone,
        "name": args.name,
        "campaign_name": "New Lead",
        "prompt": args.prompt,
    }

    print("=" * 60)
    print("Synthflow Dynamic Prompt Override — Verification Spike")
    print("=" * 60)
    print(f"  Target workflow: NewLeads Make Call ({url})")
    print(f"  Phone:           {args.phone}")
    print(f"  Test prompt:     {args.prompt}")
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
    print("Answer the call and listen for the exact test sentence.")
    print("  - Says the test sentence, nothing else       -> full override confirmed (replace)")
    print("  - Says the test sentence AND the normal script -> override merges/appends, not a clean replace")
    print("  - Runs the normal Colaberry admissions script  -> override is being ignored")


if __name__ == "__main__":
    main()
