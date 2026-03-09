"""
Worker job functions — called by RQ workers.

Each job function:
  1. Receives a scheduled_job_id (str)
  2. Claims the job atomically via app.worker.claim.claim_job()
  3. Marks as running
  4. Executes business logic (calls services and adapters)
  5. On success: marks as completed
  6. On failure: creates an ExceptionRecord, marks as failed

Job routing (queue → job type → function):
  default queue   → process_call_event, process_voicemail_tier
  ai queue        → run_call_analysis
  callbacks queue → schedule_synthflow_callback (Phase 7)
  retries queue   → retry_failed_job
  sheet_mirror    → sync_sheet_rows (Phase 9)
"""

JOB_REGISTRY: dict[str, object] = {}
"""Maps job_type strings to callable job functions."""
