"""
Synthflow webhook simulator — sends a realistic completed-call payload to a
running Cora Recap Engine instance (local or ngrok tunnel).

Usage:
  python execution/simulate_webhook.py --help
  python execution/simulate_webhook.py --url http://localhost:8000

  # Voicemail — New Lead (no transcript)
  python execution/simulate_webhook.py \
    --url http://localhost:8000 \
    --contact-id cid-123 \
    --campaign new_lead \
    --status voicemail

  # Enrollment transcript
  python execution/simulate_webhook.py \
    --url https://abc.ngrok.io \
    --contact-id cid-456 \
    --campaign new_lead \
    --status voicemail \
    --transcript "I want to enroll"

  # Not interested
  python execution/simulate_webhook.py \
    --url http://localhost:8000 \
    --contact-id cid-789 \
    --status voicemail \
    --transcript "no thanks not interested"

  # Completed call (routes to AI analysis, not voicemail tier)
  python execution/simulate_webhook.py \
    --url http://localhost:8000 \
    --contact-id cid-999 \
    --status completed

Output:
  HTTP status code and response body printed to stdout.
  Exit code 0 on 2xx, 1 on error.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone


def _build_payload(
    *,
    call_id: str,
    contact_id: str,
    call_status: str,
    campaign_name: str,
    lead_name: str,
    transcript: str | None,
    phone: str,
) -> dict:
    """
    Build a minimal Synthflow completed-call webhook payload.

    Uses field names that match normalize_synthflow_outcome() in call_processing.py:
      call_status → primary routing field
      contact_id  → contact lookup key
      transcript  → optional, triggers intent detection in process_voicemail_tier
    """
    payload = {
        "call_id": call_id,
        "contact_id": contact_id,
        "call_status": call_status,
        "campaign_name": campaign_name,
        "lead_name": lead_name,
        "phone_number": phone,
        "direction": "outbound",
        "start_time": datetime.now(tz=timezone.utc).isoformat(),
        "duration": 0,
    }
    if transcript is not None:
        payload["transcript"] = transcript
    return payload


def simulate(
    base_url: str,
    contact_id: str,
    call_status: str,
    campaign_name: str,
    lead_name: str,
    transcript: str | None,
    phone: str,
    shared_secret: str,
    dry_run: bool,
) -> int:
    """Send the webhook and return the HTTP status code."""
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx is not installed. Run: pip install httpx", file=sys.stderr)
        return 1

    call_id = f"sim-{uuid.uuid4().hex[:12]}"
    payload = _build_payload(
        call_id=call_id,
        contact_id=contact_id,
        call_status=call_status,
        campaign_name=campaign_name,
        lead_name=lead_name,
        transcript=transcript,
        phone=phone,
    )

    url = base_url.rstrip("/") + "/v1/webhooks/calls"
    headers = {
        "Content-Type": "application/json",
        "X-Synthflow-Signature": shared_secret,
    }

    print(f"Endpoint : {url}")
    print(f"Call ID  : {call_id}")
    print(f"Contact  : {contact_id}")
    print(f"Status   : {call_status}")
    print(f"Campaign : {campaign_name}")
    if transcript:
        print(f"Transcript: {transcript!r}")
    print(f"Payload  :\n{json.dumps(payload, indent=2)}")

    if dry_run:
        print("\n[DRY RUN] Payload built. Not sending.")
        return 0

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=30)
        print(f"\nHTTP {resp.status_code}")
        print(resp.text[:2000])
        return 0 if resp.is_success else 1
    except httpx.ConnectError as exc:
        print(f"\nERROR: Cannot connect to {url}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate a Synthflow completed-call webhook against a running instance."
    )
    parser.add_argument(
        "--url", default="http://localhost:8000",
        help="Base URL of the running Cora Recap Engine (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--contact-id", default=None,
        help="GHL contact_id. Auto-generated if omitted.",
    )
    parser.add_argument(
        "--status", default="voicemail",
        choices=["voicemail", "hangup_on_voicemail", "completed", "left_voicemail",
                 "voicemail_detected", "machine_detected"],
        help="Synthflow call_status. 'voicemail' → voicemail path; 'completed' → AI path.",
    )
    parser.add_argument(
        "--campaign", default="new_lead",
        choices=["new_lead", "cold_lead"],
        help="Campaign name to include in the payload (default: new_lead).",
    )
    parser.add_argument(
        "--lead-name", default="Test Lead",
        help="Lead name to include in the payload.",
    )
    parser.add_argument(
        "--transcript", default=None,
        help="Optional transcript text (triggers intent detection).",
    )
    parser.add_argument(
        "--phone", default="+15550009999",
        help="Phone number to include in the payload.",
    )
    parser.add_argument(
        "--secret", default="changeme",
        help="Webhook shared secret header value (matches WEBHOOK_SHARED_SECRET).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build the payload and print it but do not send.",
    )
    args = parser.parse_args()

    # Normalise campaign name to internal format
    campaign_map = {"new_lead": "New Lead", "cold_lead": "Cold Lead"}
    campaign_name = campaign_map.get(args.campaign, args.campaign)

    contact_id = args.contact_id or f"sim-contact-{uuid.uuid4().hex[:8]}"

    exit_code = simulate(
        base_url=args.url,
        contact_id=contact_id,
        call_status=args.status,
        campaign_name=campaign_name,
        lead_name=args.lead_name,
        transcript=args.transcript,
        phone=args.phone,
        shared_secret=args.secret,
        dry_run=args.dry_run,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
