"""
watch_test_call.py — Monitor a test call from DB or API state.

Polls the database for the call_event and scheduled_job rows associated
with a test call, printing a summary until the call completes or times out.

Usage:
    python execution/test_scripts/watch_test_call.py \\
        --correlation-id <uuid>

    # Or watch the most recent call_event:
    python execution/test_scripts/watch_test_call.py --latest

Requires DATABASE_URL or POSTGRES_* vars set in .env (same as the app).

Output shows:
    - Scheduled job status (pending → running → completed/failed)
    - CallEvent row when created (status, duration, recording_url)
    - Normalized routing outcome
"""
from __future__ import annotations

import argparse
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch a Cora test call until completion."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--correlation-id", help="correlation_id returned by run_test_call.py")
    group.add_argument("--latest", action="store_true", help="Watch the most recent call_event")
    parser.add_argument(
        "--interval", type=int, default=5,
        help="Poll interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--timeout", type=int, default=180,
        help="Stop watching after N seconds (default: 180)"
    )
    return parser.parse_args()


def load_settings():
    """Load app settings (reads .env)."""
    try:
        from app.config import get_settings
        return get_settings()
    except Exception as e:
        print(f"ERROR: Could not load app settings: {e}")
        print("Run this script from the repo root with the app's virtualenv active.")
        sys.exit(1)


def get_db_session():
    try:
        from app.db import get_sync_session
        return get_sync_session
    except Exception as e:
        print(f"ERROR: Could not import DB session: {e}")
        sys.exit(1)


def fetch_job_by_correlation(session, correlation_id: str):
    from sqlalchemy import select, text
    from app.models.scheduled_job import ScheduledJob

    # scheduled jobs store correlation_id in payload_json
    result = session.execute(
        text(
            "SELECT * FROM scheduled_jobs "
            "WHERE payload_json->>'correlation_id' = :cid "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"cid": correlation_id},
    ).mappings().first()
    return dict(result) if result else None


def fetch_latest_call_event(session):
    from sqlalchemy import text

    result = session.execute(
        text("SELECT * FROM call_events ORDER BY created_at DESC LIMIT 1")
    ).mappings().first()
    return dict(result) if result else None


def fetch_call_event_by_correlation(session, correlation_id: str):
    from sqlalchemy import text

    # correlation_id may be in raw_payload_json.metadata.correlation_id
    result = session.execute(
        text(
            "SELECT * FROM call_events "
            "WHERE raw_payload_json->'metadata'->>'correlation_id' = :cid "
            "   OR raw_payload_json->>'correlation_id' = :cid "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"cid": correlation_id},
    ).mappings().first()
    return dict(result) if result else None


def print_job_row(job: dict | None) -> None:
    if not job:
        print("  scheduled_job: not found yet")
        return
    print(f"  scheduled_job  id={job.get('id')} status={job.get('status')} "
          f"type={job.get('job_type')} attempts={job.get('attempts', 0)}")
    if job.get("failure_reason"):
        print(f"  failure_reason: {job['failure_reason']}")


def print_call_event_row(ce: dict | None) -> None:
    if not ce:
        print("  call_event:    not yet received (waiting for Synthflow webhook)")
        return
    print(f"  call_event     id={ce.get('id')}")
    print(f"    call_id:     {ce.get('call_id')}")
    print(f"    status:      {ce.get('status')}")
    print(f"    end_reason:  {ce.get('end_call_reason')}")
    print(f"    duration:    {ce.get('duration_seconds')}s")
    print(f"    recording:   {ce.get('recording_url') or '(none)'}")
    print(f"    lead_name:   {ce.get('lead_name')}")
    print(f"    created_at:  {ce.get('created_at')}")


def main() -> None:
    args = parse_args()
    load_settings()
    get_sync_session = get_db_session()

    print("=" * 60)
    print("Cora Recap Engine — Test Call Watcher")
    print("=" * 60)
    if args.correlation_id:
        print(f"  Watching correlation_id: {args.correlation_id}")
    else:
        print("  Watching: most recent call_event")
    print(f"  Interval: {args.interval}s  Timeout: {args.timeout}s")
    print()

    start = time.time()
    iteration = 0

    while time.time() - start < args.timeout:
        iteration += 1
        elapsed = int(time.time() - start)
        print(f"[{elapsed}s] Poll #{iteration}")

        try:
            with get_sync_session() as session:
                if args.latest:
                    ce = fetch_latest_call_event(session)
                    job = None
                else:
                    job = fetch_job_by_correlation(session, args.correlation_id)
                    ce = fetch_call_event_by_correlation(session, args.correlation_id)

                print_job_row(job)
                print_call_event_row(ce)
                print()

                # Terminal: job done and call_event present
                if ce and job and job.get("status") in ("completed", "failed"):
                    print(f"Terminal state reached: job.status={job.get('status')}")
                    if ce:
                        print(f"Call outcome: {ce.get('status')} / {ce.get('end_call_reason')}")
                    break

                # Latest mode: stop when we see a call_event
                if args.latest and ce:
                    print("call_event found. Done.")
                    break

        except Exception as e:
            print(f"  DB error (will retry): {e}")

        time.sleep(args.interval)
    else:
        print(f"Timeout ({args.timeout}s) reached. Check DB manually:")
        print("  SELECT * FROM call_events ORDER BY created_at DESC LIMIT 5;")
        print("  SELECT * FROM scheduled_jobs ORDER BY created_at DESC LIMIT 5;")


if __name__ == "__main__":
    main()
