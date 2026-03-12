"""
run_test_call.py — End-to-end outbound test call launcher.

Submits a single test call request through the Cora Recap Engine API,
causing the Synthflow agent to call the specified phone number.

Usage:
    python execution/test_scripts/run_test_call.py \\
        --phone +17865551234 \\
        --lead-name "Test User" \\
        --campaign-name New_Lead

Optional flags:
    --dry-run       Build and print the request payload without submitting
    --notes TEXT    Optional metadata attached to the test record
    --api-url URL   Override the default API base URL (default: http://localhost:8000)
    --wait          Poll for job completion (prints status every 5 seconds, timeout 120s)

Preconditions (must all be true before running):
    - API service is running (uvicorn app.main:app)
    - Worker service is running (rq worker)
    - Redis is connected
    - Postgres is available and migrations are applied (alembic upgrade head)
    - SYNTHFLOW_LAUNCH_WORKFLOW_URL is set in .env
    - APP_ENV != production (test route is disabled in production)
    - GHL_WRITE_MODE=shadow or read-only (recommended for testing)

After the call ends, Synthflow will POST the completed-call payload to:
    POST <API_BASE_URL>/v1/webhooks/calls
(Configure this URL in Synthflow's "Call Completed" workflow.)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a real Synthflow outbound test call through the Cora API."
    )
    parser.add_argument("--phone", required=True, help="E.164 phone number, e.g. +17865551234")
    parser.add_argument("--lead-name", required=True, help="Lead/contact name")
    parser.add_argument("--campaign-name", default="New_Lead", help="Campaign name (default: New_Lead)")
    parser.add_argument("--notes", default="", help="Optional notes attached to the test record")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without submitting")
    parser.add_argument(
        "--api-url", default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--wait", action="store_true",
        help="Poll for job completion after submitting (timeout: 120s)"
    )
    return parser.parse_args()


def validate_phone(phone: str) -> None:
    if not phone.startswith("+") or len(phone) < 10:
        print(f"ERROR: phone must be E.164 format (e.g. +17865551234), got: {phone!r}")
        sys.exit(1)


def submit_request(api_url: str, payload: dict) -> dict:
    url = f"{api_url.rstrip('/')}/v1/test/calls/outbound"
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {e.code} from API\n{body_text}")
        sys.exit(1)
    except URLError as e:
        print(f"ERROR: Could not reach API at {url}\n{e.reason}")
        print("Is the API running? Try: uvicorn app.main:app --reload")
        sys.exit(1)


def poll_job(api_url: str, job_id: str, timeout_seconds: int = 120) -> None:
    """Poll the health endpoint and print status. (Full job-status API is a future addition.)"""
    print(f"\nPolling for completion (job_id={job_id}, timeout={timeout_seconds}s)...")
    print("Watch worker logs for detailed job status.")
    print("The Synthflow agent will call your phone. Answer or let it go to voicemail.")

    start = time.time()
    dots = 0
    while time.time() - start < timeout_seconds:
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] Waiting for Synthflow completed-call webhook...", end="\r")
        time.sleep(5)
        dots += 1

    print(f"\nTimeout reached ({timeout_seconds}s). Check logs and DB for final status:")
    print(f"  SELECT * FROM call_events ORDER BY created_at DESC LIMIT 5;")
    print(f"  SELECT * FROM scheduled_jobs WHERE id = '{job_id}';")


def main() -> None:
    args = parse_args()
    validate_phone(args.phone)

    payload = {
        "phone_number": args.phone,
        "lead_name": args.lead_name,
        "campaign_name": args.campaign_name,
        "notes": args.notes,
        "source": "e2e_test_harness",
    }

    print("=" * 60)
    print("Cora Recap Engine — End-to-End Test Call")
    print("=" * 60)
    print(f"  Phone:    {args.phone}")
    print(f"  Lead:     {args.lead_name}")
    print(f"  Campaign: {args.campaign_name}")
    if args.notes:
        print(f"  Notes:    {args.notes}")
    print()

    if args.dry_run:
        print("DRY RUN — payload that would be submitted:")
        print(json.dumps(payload, indent=2))
        print("\nNot submitting (--dry-run active).")
        return

    print(f"Submitting to {args.api_url}/v1/test/calls/outbound ...")
    result = submit_request(args.api_url, payload)

    print("\nAccepted:")
    print(f"  job_id:         {result.get('job_id')}")
    print(f"  correlation_id: {result.get('correlation_id')}")
    print(f"  campaign:       {result.get('campaign_name')}")
    print()
    print("Next steps:")
    print("  1. Watch worker logs for 'Synthflow call launched'")
    print("  2. Answer or ignore the incoming call")
    print("  3. After the call, watch for 'Received call event' in API logs")
    print("  4. Check DB: SELECT * FROM call_events ORDER BY created_at DESC LIMIT 5;")
    print()
    print("Observe completed-call result:")
    print("  python execution/test_scripts/watch_test_call.py "
          f"--correlation-id {result.get('correlation_id')}")

    if args.wait:
        poll_job(args.api_url, result.get("job_id", ""), timeout_seconds=120)


if __name__ == "__main__":
    main()
