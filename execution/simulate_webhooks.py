"""
simulate_webhooks.py — Webhook payload builder and sender.

Provides helper functions for building realistic Synthflow completed-call
payloads and sending them to the /v1/webhooks/calls endpoint.

Payload structure mirrors the real Synthflow schema exactly so normalization
in webhooks.py (normalize_synthflow_payload) is exercised.

Usage as a module:
    from execution.simulate_webhooks import simulate_voicemail, send_webhook

Usage as CLI (quick test):
    python execution/simulate_webhooks.py \\
        --url http://localhost:8000 \\
        --contact-id sim-sc1-001 \\
        --phone +15551110001 \\
        --type voicemail

Functions:
    build_voicemail_payload()    — call_status=voicemail (no transcript)
    build_transcript_payload()   — call_status=voicemail + transcript text
    build_completed_payload()    — call_status=completed (routes to AI analysis)
    send_webhook()               — POST to endpoint, return (status_code, response_body)
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _base_payload(
    *,
    call_id: str,
    contact_id: str,
    phone: str,
    campaign_name: str,
    lead_name: str,
    call_status: str,
    duration: int = 0,
    transcript: str | None = None,
    end_call_reason: str | None = None,
) -> dict[str, Any]:
    """
    Build a minimal but realistic Synthflow completed-call payload.

    Uses the same field names that Synthflow sends so normalize_synthflow_payload()
    in webhooks.py is exercised end-to-end.  All optional Synthflow fields that
    the normalizer understands are populated so the stored raw_payload_json is
    representative of production.
    """
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    payload: dict[str, Any] = {
        # Primary routing fields
        "Call_id": call_id,          # Synthflow uses capital C — exercises alias normalisation
        "call_status": call_status,
        "end_call_reason": end_call_reason or call_status,
        "direction": "outbound",
        "duration": duration,
        # Contact resolution
        "contact_id": contact_id,
        "phone_number_to": phone,    # callee phone
        "phone_number_from": "+18001234567",  # agent/system number
        "phones": {
            "callee": phone,
            "caller": "+18001234567",
        },
        # Campaign context
        "campaign_name": campaign_name,
        "lead_name": lead_name,
        # Timing
        "start_time": now_iso,
        "timezone": "America/Chicago",
        # Synthflow agent metadata
        "model_id": "sim-model-001",
        "agent_phone_number": "+18001234567",
        "telephony_duration": float(duration),
        "telephony_start": now_iso,
        "telephony_end": now_iso,
    }
    if transcript is not None:
        payload["transcript"] = transcript
    return payload


def build_voicemail_payload(
    *,
    contact_id: str,
    phone: str,
    call_id: str | None = None,
    campaign_name: str = "New Lead",
    lead_name: str = "Simulation Lead",
) -> dict[str, Any]:
    """
    Build a voicemail-status payload (no transcript).

    Routes to: process_call_event → process_voicemail_tier (tier advancement)
    """
    return _base_payload(
        call_id=call_id or f"sim-{uuid.uuid4().hex[:12]}",
        contact_id=contact_id,
        phone=phone,
        campaign_name=campaign_name,
        lead_name=lead_name,
        call_status="voicemail",
        end_call_reason="voicemail",
        duration=0,
        transcript=None,
    )


def build_transcript_payload(
    *,
    contact_id: str,
    phone: str,
    transcript: str,
    call_id: str | None = None,
    campaign_name: str = "New Lead",
    lead_name: str = "Simulation Lead",
    duration: int = 15,
) -> dict[str, Any]:
    """
    Build a voicemail-status payload WITH a transcript.

    Triggers intent detection in process_voicemail_tier.
    Routes to: process_call_event → process_voicemail_tier → detect_intent → handle_intent

    Use for SC2–SC6, SC8, SC10 scenarios.
    """
    return _base_payload(
        call_id=call_id or f"sim-{uuid.uuid4().hex[:12]}",
        contact_id=contact_id,
        phone=phone,
        campaign_name=campaign_name,
        lead_name=lead_name,
        call_status="voicemail",
        end_call_reason="voicemail",
        duration=duration,
        transcript=transcript,
    )


def build_completed_payload(
    *,
    contact_id: str,
    phone: str,
    call_id: str | None = None,
    campaign_name: str = "New Lead",
    lead_name: str = "Simulation Lead",
    transcript: str | None = None,
    duration: int = 90,
) -> dict[str, Any]:
    """
    Build a completed-call payload (person answered).

    Routes to: process_call_event → classify_call_event (AI analysis path).
    Used for SC8 step that transitions to enrollment after a live conversation.
    """
    return _base_payload(
        call_id=call_id or f"sim-{uuid.uuid4().hex[:12]}",
        contact_id=contact_id,
        phone=phone,
        campaign_name=campaign_name,
        lead_name=lead_name,
        call_status="completed",
        end_call_reason="completed",
        duration=duration,
        transcript=transcript,
    )


# Convenience aliases used by scenario_runner.py
def simulate_voicemail(
    contact_id: str,
    phone: str,
    *,
    call_id: str | None = None,
    campaign_name: str = "New Lead",
    lead_name: str = "Simulation Lead",
) -> dict[str, Any]:
    """Alias for build_voicemail_payload (no transcript)."""
    return build_voicemail_payload(
        contact_id=contact_id,
        phone=phone,
        call_id=call_id,
        campaign_name=campaign_name,
        lead_name=lead_name,
    )


def simulate_transcript(
    contact_id: str,
    phone: str,
    transcript: str,
    *,
    call_id: str | None = None,
    campaign_name: str = "New Lead",
    lead_name: str = "Simulation Lead",
) -> dict[str, Any]:
    """Alias for build_transcript_payload."""
    return build_transcript_payload(
        contact_id=contact_id,
        phone=phone,
        transcript=transcript,
        call_id=call_id,
        campaign_name=campaign_name,
        lead_name=lead_name,
    )


def simulate_callback_event(
    contact_id: str,
    phone: str,
    *,
    call_id: str | None = None,
    campaign_name: str = "New Lead",
    lead_name: str = "Simulation Lead",
) -> dict[str, Any]:
    """Build a completed-call payload representing a callback that was answered."""
    return build_completed_payload(
        contact_id=contact_id,
        phone=phone,
        call_id=call_id,
        campaign_name=campaign_name,
        lead_name=lead_name,
        transcript=None,
        duration=60,
    )


# ---------------------------------------------------------------------------
# HTTP sender
# ---------------------------------------------------------------------------

def send_webhook(
    base_url: str,
    payload: dict[str, Any],
    *,
    shared_secret: str = "changeme",
    timeout: int = 10,
) -> tuple[int, dict]:
    """
    POST payload to /v1/webhooks/calls.

    Returns (http_status_code, response_body_dict).
    Raises RuntimeError on connection failure.
    """
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is required: pip install httpx")

    url = f"{base_url.rstrip('/')}/v1/webhooks/calls"
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Synthflow-Signature": shared_secret,
            },
            timeout=timeout,
        )
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text}
        return resp.status_code, body
    except httpx.ConnectError as exc:
        raise RuntimeError(f"Cannot connect to {url}: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"Timeout connecting to {url}: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI entry point (quick test without running full scenario_runner)
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Send a single simulated Synthflow webhook to a running instance."
    )
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--contact-id", required=True, help="Contact ID")
    parser.add_argument("--phone", default="+15559999999", help="Phone number (E.164)")
    parser.add_argument(
        "--type", default="voicemail",
        choices=["voicemail", "transcript", "completed"],
        help="Payload type (default: voicemail)",
    )
    parser.add_argument("--transcript", default=None, help="Transcript text (for --type transcript)")
    parser.add_argument("--campaign", default="New Lead", help="Campaign name")
    parser.add_argument("--call-id", default=None, help="Call ID (auto-generated if omitted)")
    parser.add_argument("--secret", default="changeme", help="Webhook shared secret")
    parser.add_argument("--dry-run", action="store_true", help="Print payload only, do not send")
    args = parser.parse_args()

    if args.type == "voicemail":
        payload = build_voicemail_payload(
            contact_id=args.contact_id, phone=args.phone,
            call_id=args.call_id, campaign_name=args.campaign,
        )
    elif args.type == "transcript":
        if not args.transcript:
            print("ERROR: --transcript is required for --type transcript")
            sys.exit(1)
        payload = build_transcript_payload(
            contact_id=args.contact_id, phone=args.phone,
            transcript=args.transcript, call_id=args.call_id, campaign_name=args.campaign,
        )
    else:
        payload = build_completed_payload(
            contact_id=args.contact_id, phone=args.phone,
            call_id=args.call_id, campaign_name=args.campaign,
        )

    print(f"Payload:\n{json.dumps(payload, indent=2)}")
    if args.dry_run:
        print("\n[DRY RUN] Not sending.")
        return

    status_code, body = send_webhook(args.url, payload, shared_secret=args.secret)
    print(f"\nHTTP {status_code}")
    print(json.dumps(body, indent=2))
    sys.exit(0 if 200 <= status_code < 300 else 1)


if __name__ == "__main__":
    _cli()
