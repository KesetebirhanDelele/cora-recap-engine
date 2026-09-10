# Spec 29 — Stuck-Call Recovery & Outbound Burst Cap

| Area | Status |
|---|---|
| **Item 1** — recover calls stuck on a non-terminal webhook (`in-progress` / `queue`) | **SPEC — not implemented.** |
| **Item 1a** — `_create_call_event` refreshes a stale non-terminal row instead of returning it untouched | **SPEC — not implemented.** |
| **Item 1b** — auto-recovery sweep for stuck non-terminal `call_events`; cosmetic-only when the lead has moved on | **SPEC — not implemented.** |
| **Item 1c** — auto-resolve the matching `call_pending` exception when a stuck row is repaired | **SPEC — not implemented.** |
| **Item 2** — hard cap of `_CALL_BATCH_SIZE` launch jobs per 5-minute window; serialize bucket allocation | **SPEC — not implemented.** |
| **Item 2a** — nurture scheduler commits per lead so the bucket-allocation lock is not held across a 50-lead batch's GHL lookups | **SPEC — not implemented.** |
| **Item 3** (follow-up, 2026-09-10) — `call_pending` is now **log-only**, no dashboard exception. The recovery sweep repairs these automatically within ~20–40 min, so the alert was transient noise an operator can't action. Same treatment as the `do_not_call` / enrolled-student guards. `_resolve_call_pending` stays (cleans up exceptions raised before this change). | **DONE.** |
| **Item 4** (follow-up, 2026-09-10) — the burst that caused items 1–2 also saturated `worker-default` (single process, `default` queue): `process_call_event` wait times went from 14 s to ~290 s, largely because `staff_call_quality_scan` (~200 s/run) shares that queue. Moved `staff_call_quality_scan` to a dedicated `quality` queue + `worker-quality` service. New `WORKER_ROLE=quality`, `rq_quality_queue` setting. Does not fix the "`worker-default` is unscalable" root (scheduler-loop singleton) — see spec/18 Stage 2 for the advisory-lock guard that unlocks that. | **DONE.** |
| Synthflow-side webhook delivery reliability | **NOT IN THIS REPO** — tracked with Ali. This spec is the Cora-side backstop. |

---

## Self-contained problem statement

### Business goal
Two production defects observed 2026-09-10, almost certainly the same root cause:

1. **Stuck calls.** When Synthflow sends Cora a *start-of-call* webhook
   (`Status: in-progress`, `duration: 0`, empty transcript) and then never
   sends the completion webhook, the call is frozen. `process_call_event`
   cannot route a non-terminal call, so it writes a `call_pending` warning
   exception and stops. Cora's auto-recovery job **explicitly skips** these
   (it only handles calls with *no* webhook at all —
   `webhook_recovery_jobs.py::_get_pending_failures`, `ce.call_id IS NULL`).
   The lead's campaign progression for that attempt is lost; the exception
   sits open forever. On 2026-09-10, 11 outbound New Lead calls got stuck
   between 13:41–14:42 UTC.

2. **Burst over-dialing.** The 75-second call-spacing grid
   (`outbound_jobs.py::_compute_window_run_at`, spec/21) has **no
   concurrency protection**. When a batch of `launch_outbound_call` jobs is
   scheduled concurrently — e.g. a wave of GHL New Lead / Cold Lead webhooks
   from the spec/27 stale-`AI Campaign` re-enrollment loop, each handled in
   its own transaction — every scheduler reads the same "free bucket"
   snapshot and writes to it. Result on 2026-09-10: two 5-minute windows
   (14:00 and 15:00 UTC) held **14 launch jobs each** against a design cap
   of **4**; 66% of the day's 73 outbound calls started <75 s after the
   previous one, 10% within 5 s, one pair in the same second. The 14:00 UTC
   over-dial window is the exact window the 11 stuck calls occurred in —
   flooding Synthflow is the most likely reason it dropped their completion
   webhooks.

### Root cause
- **Item 1:** `_get_pending_failures` filters to `ce.call_id IS NULL`, and
  `_create_call_event` returns an existing row unchanged on a dedupe-key
  hit (`{call_id}:process_call_event`) — so even a forced replay of the
  terminal webhook would not fix the row or fill the transcript.
- **Item 2:** `_compute_window_run_at` selects occupied buckets with a plain
  `SELECT` (no `FOR UPDATE`, no advisory lock, no unique constraint on
  `scheduled_jobs.run_at` for pending launch jobs). Concurrent schedulers
  race and collide on the same bucket. `schedule_job` flushes, so a
  *sequential* batch in one session ladders correctly — the failure is
  cross-transaction concurrency only.

### Relevant systems and files
- `app/worker/jobs/call_processing.py`
  - `process_call_event()` — `_PENDING_STATUSES = {"queue","in-progress"}`
    branch (line ~422) writes `call_pending` and falls through to
    `complete_job`.
  - `_create_call_event()` (line ~250) — dedupe-key hit returns existing row
    untouched (line ~263–271).
  - `normalize_synthflow_outcome()` — already handles `Status` (capital);
    a recovered `sf.get_call()` payload normalizes correctly.
- `app/worker/jobs/webhook_recovery_jobs.py`
  - `_run_recovery_cycle()` — per-cycle loop, cap `_PER_CYCLE_CAP = 10`,
    reschedules every 5 min.
  - `_get_pending_failures()` — `ce.call_id IS NULL` filter is the gap.
  - `_NON_TERMINAL_STATUSES`, `_TERMINAL_STATUSES`.
- `app/services/stale_recovery.py`
  - `recover_missed_webhook(session, contact_id, synthflow_call_id,
    operator_id, settings)` — fetches `sf.get_call()`, normalizes, schedules
    `process_call_event`; raises `StaleLeadConflict` if an active
    `process_call_event` job already exists for the call_id.
  - `advance_stale_lead()`, `_write_audit()`.
- `app/worker/jobs/outbound_jobs.py`
  - `_CALL_BATCH_SIZE = 4`, `_CALL_SLOT_SECONDS = 300`,
    `_CALL_WITHIN_SLOT_SPACING = 75`, `_EPOCH = 2020-01-01Z`,
    `_MAX_BUCKET_SEARCH = 192`.
  - `_compute_window_run_at(session, window_start, *, campaign_name)` —
    bucket allocator; needs serialization.
  - `_bump_lower_priority_job()` — priority displacement (New Lead over Cold
    Lead); must keep working under the new lock.
- `app/worker/jobs/voicemail_jobs.py::_slot_aware_run_at()` — rounds `now +
  delay` down to the 5-min slot then calls `_compute_window_run_at`.
- `app/models/exception.py` / `app/worker/exceptions.py` —
  `resolve_exception()` (status `open` → `resolved`).
- `app/worker/scheduler.py::schedule_job()` — flushes after insert.

### Input data
- `call_events` rows with `status IN ('in-progress','queue')`,
  `direction = 'outbound'`.
- Synthflow `GET /v2/calls/{call_id}` — authoritative current status.
- `scheduled_jobs` where `job_type='launch_outbound_call'` and
  `status IN ('pending','claimed')` — the bucket occupancy set.

### Expected outputs
- **Item 1:** every stuck non-terminal `call_events` row older than
  `_MIN_AGE_MINUTES` is, within one recovery cycle, either (a) fully
  recovered — row refreshed to the real terminal status + transcript, the
  campaign routed forward, exception resolved — when the lead has **not**
  moved on, or (b) cosmetically recovered — row refreshed, exception
  resolved, **no** re-routing — when the lead has newer terminal
  `call_events` or a pending `launch_outbound_call`.
- **Item 2:** no 5-minute window (epoch-aligned) ever contains more than
  `_CALL_BATCH_SIZE` pending/claimed `launch_outbound_call` jobs; concurrent
  schedulers serialize on bucket allocation; the priority-bump path still
  works.

### Known edge cases
- **Stuck call, lead already moved on** (the 2026-09-10 eleven — now
  `Cold Lead`, `pending_calls = 0`): cosmetic recovery only. Never replay an
  old voicemail/answered outcome into `process_voicemail_tier` /
  call-through — that would double-advance the tier.
- **Stuck call, Synthflow still says non-terminal**: skip this cycle, retry
  next (mirrors existing `_NON_TERMINAL_STATUSES` handling).
- **Stuck call, Synthflow returns 404 / no record**: after
  `_MAX_AGE_HOURS`, mark the row `failed` (end_call_reason
  `recovery_no_record`), resolve the exception, no routing.
- **Stuck call was actually answered** (real transcript in Synthflow) and
  the lead has **not** moved on: full recovery — route to call-through so
  the transcript is analysed.
- **Item 2 on SQLite** (unit tests): `pg_advisory_xact_lock` is
  Postgres-only. Guard on `session.bind.dialect.name == "postgresql"`; on
  SQLite the allocator runs unlocked (tests are single-threaded, so the
  ladder assertions still hold).
- **Advisory lock + priority bump**: the lock must wrap the whole
  read-decide-write in `_compute_window_run_at` including
  `_bump_lower_priority_job`, so a bumped job's new bucket is not
  simultaneously taken by another scheduler.
- **Lock contention / throughput**: outbound volume is ~50–75 calls/day;
  serializing sub-second bucket allocation is a non-issue. Revisit under
  spec/18 only if volume × 10.

### Out of scope
- Synthflow-side webhook delivery reliability (Ali).
- The spec/27 GHL re-enrollment loop that generates the bursts (Ali) — this
  spec caps the blast radius, it does not stop the trigger.
- Reworking the 75s grid arithmetic or `_MAX_BUCKET_SEARCH`.
- A global token-bucket rate limiter on the dialer (heavier; the per-window
  cap is sufficient at current volume).
- spec/28 (answering-service handling) — separate branch.

---

## Acceptance criteria

1. **Stuck-row refresh (1a).** Given a `call_events` row with
   `status='in-progress'` and `dedupe_key='<cid>:process_call_event'`, when
   `process_call_event` runs again with a payload whose normalized status is
   terminal (e.g. `hangup_on_voicemail`), then `_create_call_event` **updates**
   the existing row (`status`, `transcript`, `duration_seconds`,
   `end_call_reason`, `recording_url`, `timeline`, `telephony_*`,
   `start_time_utc`, `raw_payload_json`) and returns it. Given the existing
   row is already terminal, then it is returned unchanged (true replay
   dedupe — no regression).
2. **Sweep — full recovery (1b).** Given a stuck outbound `in-progress` row
   older than `_MIN_AGE_MINUTES`, Synthflow reports it terminal, and the
   contact has **no** newer terminal `call_events` and **no** pending
   `launch_outbound_call`, when the recovery cycle runs, then
   `recover_missed_webhook` is invoked, the row is refreshed, the campaign
   is routed forward (voicemail tier or call-through), an audit
   `manual_webhook_recovery` (operator `auto_webhook_recovery`) is written,
   and the `call_pending` exception is resolved.
3. **Sweep — cosmetic recovery (1b).** Same as (2) but the contact **has**
   a newer terminal `call_event` or a pending `launch_outbound_call`, when
   the cycle runs, then the row is refreshed to the real terminal status,
   the exception is resolved, an audit line records `cosmetic_recovery`, and
   **no** `process_voicemail_tier` / `run_call_analysis` /
   `launch_outbound_call` job is scheduled.
4. **Sweep — Synthflow still non-terminal.** Given Synthflow reports
   `in_progress`/`ringing`, when the cycle runs, then the row is left as-is
   and retried next cycle (no exception churn).
5. **Sweep — no Synthflow record past max age.** Given Synthflow returns no
   call and the row is older than `_MAX_AGE_HOURS`, when the cycle runs,
   then the row is set `status='failed'`,
   `end_call_reason='recovery_no_record'`, the exception is resolved, and no
   routing occurs.
6. **Exception auto-resolve (1c).** Whenever a `call_pending` row is
   repaired by (2), (3), or (5), the corresponding open exception
   (`type='call_pending'`, `entity_id=call_id`) transitions to `resolved`
   with `resolution_reason` naming the recovery path.
7. **Burst cap (2).** Given `_CALL_BATCH_SIZE + N` `enter_campaign()` calls
   for distinct contacts targeting the same 5-minute window, when they are
   scheduled, then the first `_CALL_BATCH_SIZE` land on that window's
   buckets (offsets 0/75/150/225) and the remaining `N` spill to the next
   window(s) — never more than `_CALL_BATCH_SIZE` pending launch jobs per
   epoch-aligned 5-minute window.
8. **Burst cap — concurrency.** Given two schedulers allocate a bucket
   concurrently (simulated: two sessions, second starts before first
   commits), when both complete, then they hold **different** buckets.
   (Postgres-backed test; skipped on SQLite.)
9. **No priority regression.** The New-Lead-bumps-Cold-Lead path
   (`_bump_lower_priority_job`) still works with the serialization in place;
   existing `test_outbound_jobs.py` priority tests pass unchanged.

---

## Constraint architecture

### Musts
- Stuck-row refresh must be **status-gated**: only `non-terminal → terminal`
  triggers an update; `terminal → anything` stays a no-op (replay safety).
- Cosmetic vs full recovery must be decided by a **lead-moved-on check**
  (newer terminal `call_events` for the contact, or a pending
  `launch_outbound_call`) — never re-route a lead that has progressed.
- Item 2 must serialize bucket allocation across transactions on Postgres
  (`pg_advisory_xact_lock`), covering the read, the priority-bump decision,
  and the insert.
- The recovery sweep must respect `_PER_CYCLE_CAP` and the existing
  claim/reschedule lifecycle — no unbounded loop.
- Every repair must leave an audit trail (`audit_log`) and resolve the
  `call_pending` exception.

### Must-nots
- Must not replay an old call outcome into tier/analysis when the lead has
  newer activity.
- Must not change `check_urgent_unresolved`, the 75s grid math, or
  `_MAX_BUCKET_SEARCH`.
- Must not require a schema migration (advisory lock needs none;
  `call_events` and `exceptions` columns already exist).
- Must not block the recovery cycle on a single failing call — catch,
  log, `skipped += 1`, continue (mirrors existing loop).
- Must not hold the advisory lock across a Synthflow HTTP call.

### Preferences
- Prefer extending `_run_recovery_cycle` with a second pass
  (`_recover_stuck_calls`) over a new top-level job — reuses the schedule,
  cap, and claim lifecycle.
- Prefer reusing `recover_missed_webhook` for the full-recovery path; add a
  narrow `route: bool` (or a pre-flight skip) rather than duplicating its
  Synthflow-fetch + normalize logic.
- Prefer a single well-named advisory-lock constant
  (`_BUCKET_ALLOC_LOCK_KEY`) over a hashed string.

### Escalation triggers
- If cosmetic-recovery rate stays high (say >5/day for a week), the burst
  cap isn't holding or Synthflow delivery has degraded further — escalate
  to Ali with the `call_pending` counts.
- If the advisory lock shows measurable contention in worker logs (waits
  >1 s), outbound volume has outgrown single-lane allocation — escalate to
  the spec/18 scaling track (batch allocator, or a reservation table).

---

## Decomposition / break pattern

| Chunk | Purpose | Artifact | Depends on |
|---|---|---|---|
| **1** | `_create_call_event` status-gated refresh of a stale non-terminal row | `test_call_processing.py`: in-progress→terminal updates; terminal→terminal no-op | — |
| **2** | `_recover_stuck_calls` sweep + cosmetic/full branch + exception auto-resolve | `test_webhook_recovery_jobs.py`: full recovery; cosmetic; still-non-terminal; no-record-past-max-age; exception resolved | Chunk 1 |
| **3** | `recover_missed_webhook` gains `route: bool` (or pre-flight) for the cosmetic path | covered by Chunk 2 tests + a `test_stale_recovery` case | Chunk 1 |
| **4** | Advisory lock in `_compute_window_run_at` (PG-guarded), covering bump | `test_outbound_jobs.py`: per-window cap ≤ 4 with overflow; PG concurrency test (skipped on SQLite); priority-bump regression | — (independent) |
| **Integration** | full suite + ruff; deploy; run one recovery cycle; confirm the 11 repaired | `pytest tests/unit -q` (7 known pre-existing failures unchanged); post-deploy log check | 1–4 |

Chunk 4 is independent and lowest-risk — could ship first. Chunks 1→2→3 are
the recovery fix and land together.

## Eval design

- **`test_call_processing.py`**: stale `in-progress` row + terminal replay →
  fields updated; terminal row + replay → untouched; `queue` status treated
  same as `in-progress`.
- **`test_webhook_recovery_jobs.py`**: seed a stuck `in-progress`
  `call_events` row; mock `SynthflowClient.get_call` →
  (a) `hangup_on_voicemail` + no newer activity → full recovery, tier job
  scheduled, exception resolved; (b) same + newer `call_event` present →
  cosmetic, no tier job, exception resolved; (c) `in_progress` → untouched;
  (d) 404 + row older than `_MAX_AGE_HOURS` → `failed` /
  `recovery_no_record`, exception resolved.
- **`test_outbound_jobs.py`**: schedule `_CALL_BATCH_SIZE + 3` jobs into one
  window → assert ≤ 4 per window and the rest in later windows; Postgres
  fixture concurrency test for distinct buckets (SQLite-skip);
  existing priority-bump tests unchanged.
- **Regression**: full `pytest tests/unit -q`; `ruff check app/ tests/
  migrations/`.
- **Post-deploy (Hetzner)**: trigger one `auto_webhook_recovery` cycle;
  confirm all 11 stuck rows now terminal, all 11 `call_pending` exceptions
  resolved, no new `process_voicemail_tier` jobs for the 11 (cosmetic path);
  re-run the per-window count query for the next active hour → ≤ 4.

## Production context — the 11 stuck calls (2026-09-10)

All 11 will hit the **cosmetic** path: they are now `campaign_name='Cold
Lead'` with `pending_calls=0` and each contact has newer terminal
`call_events` from later attempts. Recovery will set the true status
(Synthflow shows `hangup_on_voicemail` for the one checked, `+14342622221`
/ `116c79f8…`), fill the transcript, resolve the exception, and **not**
re-dial. Phone list and stuck call_ids: see the 2026-09-10 session notes /
PROGRESS.md.
