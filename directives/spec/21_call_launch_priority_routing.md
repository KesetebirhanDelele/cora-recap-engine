# spec/21_call_launch_priority_routing.md

**Branch:** `feat/call-launch-priority-routing` (cut from `feat/ghl-call-conversation-sync` 2026-08-22)

## Implementation status

| Area | Status |
|---|---|
| GHL-facing call-launch intake endpoint | **DONE.** `POST /v1/webhooks/leads/{campaign_type}` — `app/api/routes/call_intake.py`, registered in `main.py`. Shared-secret auth (`CORA_INBOUND_WEBHOOK_SECRET`, new setting), find-or-create `LeadState` by phone, delegates everything else to `enter_campaign()` (pause checks, dedup, priority-aware slot-aware scheduling all inherited for free). 9 new tests, all passing. |
| `enter_campaign()` slot-aware scheduling fix | **DONE.** `enter_campaign()` now calls `voicemail_jobs.py::_slot_aware_run_at(session, 0)` instead of `run_at=now`. 2 new regression tests (`test_campaigns.py`) + all 140 tests across `test_campaigns.py`/`test_campaign_switching.py`/`test_nurture_scheduler.py`/`test_live_call_intents.py`/`test_lifecycle*.py` passing, no regressions. |
| New Lead priority / single-bump logic | **DONE.** `_compute_window_run_at()` extended with optional `campaign_name` (keyword-only, defaults to `None` = exact prior behavior); new `_bump_lower_priority_job()` helper, version-checked update. Threaded through `_slot_aware_run_at()` (voicemail retries + `enter_campaign()`) and the window-deferral call site. 7 new tests covering AC4–AC7 plus same-priority/non-priority/version-conflict cases, all passing — 196 tests green across every touched module, no regressions. |
| Per-campaign dynamic prompt injection | **DONE.** `_load_campaign_prompt()` added to `app/adapters/synthflow.py`, wired into `launch_new_lead_call()`'s payload; 5 new unit tests, all passing, no regressions (`python -m pytest tests/unit/test_synthflow_launch.py -q` → 15/15). One Synthflow-dashboard follow-up remains: replicate the Custom Variables mapping on ColdLeads' workflow (see §1) — not code, tracked separately. |
| GHL workflow cutover (New Lead + Cold Lead → Cora) | **DONE — LIVE.** Performed ~2026-08-27 (undocumented at the time), fully verified 2026-09-09. Both GHL "Cora Outbound - New/Cold Leads" webhook actions now POST to `/v1/webhooks/leads/{campaign_type}` with the shared secret in the **Headers** section (an initial misconfiguration put it in Custom Data → 100% 401 for 12 days; see PROGRESS.md 2026-09-08). Old direct-to-Synthflow trigger was removed, not kept as fallback. End-to-end confirmed: GHL 202 → `launch_outbound_call` → Synthflow dial → completion webhook → `call_events` + full downstream pipeline. |

---

## 1. Self-contained problem statement

### Business goal

Cora and Synthflow currently share **one voice agent/assistant** across two campaigns (New Lead,
Cold Lead — the New Lead Synthflow assistant lost its phone number on 2026-07-15 and was repointed
to the Cold Lead assistant; both now resolve to Synthflow `model_id 95fd0659-7446-423c-bc51-764c3060c90f`).
Cold Lead is about to be switched on to run through this same shared agent at meaningful volume
(currently New Lead is <2% of call volume, Cold Lead is >98%).

Two problems block that safely:

1. **GHL triggers the very first call of a lead's lifecycle directly against Synthflow's "Make
   Call" workflow webhook, bypassing Cora entirely.** Cora has no `ScheduledJob` row, no
   visibility, and no way to prevent that call from colliding with a call Cora itself is placing
   at the same moment (voicemail retries, cold-lead nurture batches, callbacks). A specific
   downstream HTTP step inside the Synthflow "Make Call" workflow (post-call logging/notification,
   not the voice agent itself — the voice agent handles 5 concurrent calls fine) cannot tolerate
   concurrent executions. Kes has already validated that a ~75s gap between calls is adequate for
   that HTTP step, once a call is complete.
2. **Even for calls Cora does schedule, spacing is inconsistent.** Voicemail-tier retries
   (`voicemail_jobs.py::_slot_aware_run_at`) are already placed on a shared 75-second bucket grid
   (`outbound_jobs.py::_compute_window_run_at`) that has no campaign filter — but a lead's very
   first call, scheduled via `campaigns.py::enter_campaign()`, is scheduled with `run_at=now` and
   never touches that grid at all (`campaigns.py:158`). Multiple leads entering a campaign close
   together (in particular, `nurture_scheduler.py`'s batch of up to 50 leads every 5 minutes) can
   produce several `launch_outbound_call` jobs with near-identical `run_at`, with nothing to space
   them.

The goal of this work: **route every call-launch trigger — from GHL and from Cora's own internal
scheduling — through a single scheduling system**, so the existing collision-safe pacing that
already protects voicemail retries protects everything, with New Lead (rare, time-sensitive)
getting priority over Cold Lead (common, not time-sensitive) when both want the same slot.

A second, independent goal bundled into this branch at Kes's request: give the shared voice agent
a way to vary its spoken script by campaign, without a second Synthflow assistant/number — using a
per-call prompt override, following the pattern already proven working in the sibling `Cory_dev`
repo (same provider, same "one shared assistant" shape).

### Relevant systems and files

- `app/core/campaigns.py::enter_campaign()` — single public entry point for placing a lead into
  New Lead or Cold Lead. Schedules the first `launch_outbound_call` job with `run_at=now`
  (`campaigns.py:153-166`) — this is the gap to close for goal 2.
- `app/worker/jobs/outbound_jobs.py` — owns the shared bucket grid:
  `_compute_window_run_at()` (line 68) finds the next free 75s bucket (`_CALL_WITHIN_SLOT_SPACING
  = 75`, from `_CALL_SLOT_SECONDS=300 / _CALL_BATCH_SIZE=4`) for `job_type="launch_outbound_call"`
  jobs in `pending`/`claimed` status, with **no campaign filter** — this is the mechanism to
  extend with priority/bump semantics. Currently only invoked from the "outside active calling
  window" deferral path (line 231), not from real-time scheduling.
- `app/worker/jobs/voicemail_jobs.py::_slot_aware_run_at()` (line 351) — the existing pattern for
  routing a `run_at` through the shared grid before scheduling; the model to follow for fixing
  `enter_campaign()` and for building the new intake endpoint's scheduling call.
- `app/worker/claim.py` — atomic claim/lease/version primitives (`claim_job`, `release_job_to_pending`,
  optimistic `version` column). Any code that rewrites another job's `run_at` (the bump) **must**
  go through a version-checked update, reusing this module's pattern — not a raw `UPDATE`.
- `app/adapters/synthflow.py::launch_new_lead_call()` (line 219) — builds the Synthflow launch
  payload (`phone`, `name`, `campaign_name`, `metadata` only, today). Extension point for the
  per-campaign prompt override.
- `app/api/routes/webhooks.py` — existing pattern for a Cora-facing webhook route (`POST
  /v1/webhooks/calls`, Synthflow's call-completion callback). **Has no auth/signature check
  today** (confirmed via grep — this endpoint is low-risk, it only logs data). The new intake
  endpoint is higher-risk (it can trigger a real, billable outbound call) and must not copy this
  endpoint's lack of auth — see Constraints.
- `app/core/mode_flags.py` — `outbound_campaigns_paused`, `cold_lead_campaign_paused` flags,
  already checked in both `campaigns.py::enter_campaign()` and `outbound_jobs.py::launch_outbound_call_job()`.
  The new intake endpoint must respect these too (skip/no-op, not error) — do not create a second
  code path that can fire calls while the system is paused.
- `directives/spec/16_ghl_integration.md` — existing GHL write-path contract (Private Integration
  API), unaffected by this work but the natural place future GHL-auth conventions should stay
  consistent with.

### Input data

- GHL workflow HTTP-request trigger payload (New Lead and Cold Lead workflows): contact phone,
  lead name, GHL contact ID, campaign identifier. **Exact current payload shape is unconfirmed** —
  needs to be captured from GHL's existing "Send HTTP request" step config (the one currently
  pointed at Synthflow) before the intake endpoint's request schema is finalized.
- `Cory_dev`'s confirmed Synthflow call-payload shape (for the prompt-injection piece):
  `{"model_id", "phone", "name", "prompt": <full composed script text>, "custom_variables": [...]}`
  — `prompt` is a full override string composed application-side per call, not a `{{template}}`
  filled by Synthflow. **Whether Cora's specific Synthflow account/model accepts the same `prompt`
  field is unconfirmed** — Cory_dev may be on a different Synthflow plan or model configuration.
- **Prompt source text — provided 2026-08-23, source of truth for step 7**:
  `docs/synthflow-warm-lead-prompt.md` (New Lead / `campaign_name="New Lead"` — warm, energetic,
  action-oriented tone) and `docs/synthflow-cold-lead-prompt.md` (Cold Lead /
  `campaign_name="Cold Lead"` — empathetic, trust-rebuilding tone). Both are full Synthflow system
  prompts (background, goals, company overview, call-flow sections, rules, staff availability) —
  the composition function in step 7 sends the appropriate one's full text as the `prompt` override
  per call; this is not lead-specific dynamic interpolation of a shared template, it's a full
  per-campaign script swap.
- **Prompt-override mechanism confirmed 2026-08-23 by direct inspection of the live Synthflow
  workflow builder** (not a docs spike — Kes screenshotted the actual "Make Phone Call and Get
  Call Data" step config). This is a Synthflow-native Workflow (built in Synthflow's own visual
  builder, reached via `fine-tuner.ai/portal`) — **not** Make.com; Make.com is a separate,
  unrelated third-party integration and does not apply to Cora's setup. The step's trigger is
  `1. Catch Webhook` (the URL `launch_new_lead_call()` POSTs to); step 2, `Make Phone Call and Get
  Call Data` (a "Voice AI Agent" action), exposes individually-mapped input fields sourced from
  `1. Catch Webhook body.<field>`: `Phone Number` ← `body.phone`, `Recipient Name` ← `body.first_name`,
  `Custom Variables` (key/value list, `+ Add Item` to add more, described in-UI as "JSON string of
  key-value pairs of custom variables for dynamic injection"), `Lead Email` ← `body.email`,
  `Lead Timezone` ← `body.timezone`, and — the field that matters here — **`Prompt`** ("Pass the
  prompt for the Assistant"), currently **empty/unmapped** in this workflow.
  **This confirms the mechanism (an explicit per-call prompt-override slot exists) but also
  confirms it requires explicit field mapping, not automatic pass-through** — same pattern as
  Make.com's module config, just inside Synthflow's own builder instead. Two consequences:
  1. Cora's payload must add a `"prompt"` key (it currently sends `phone`/`name`/`campaign_name`/
     `metadata` only — `synthflow.py:240-246` — note `"name"`, not `"first_name"`, which doesn't
     match the `Recipient Name` field's current mapping; worth checking separately whether that's
     a pre-existing bug, out of scope for this spec).
  2. **The `Prompt` field must be manually mapped to `body.prompt` inside the Synthflow workflow
     builder — once per workflow object.**

     **Settled 2026-08-23, via direct inspection of Synthflow's workflow list** (not inference —
     Kes screenshotted the actual inventory with enabled/disabled status). Two separate "Make Call"
     workflow objects genuinely exist, exactly as the original two-URL design assumed:

     | Workflow | Webhook ID | Status | Env var |
     |---|---|---|---|
     | Cora Outbound NewLeads - Make Call | `p6ihFj7HmplXM2WiuVsaC` | **Enabled** (live, placing real calls today) | `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New` |
     | Cora Outbound ColdLeads - Make Call | `33J546NiXxUUIRCbywNVH` | **Disabled** (Cold Lead hasn't launched yet) | `SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold` |

     This resolves the earlier back-and-forth about "only one workflow" cleanly: both objects are
     real and both matter, they're just in different lifecycle states — ColdLeads' being disabled
     *is* the reason Kes is doing this work ("kick starting" Cold Lead means safely enabling it).
     Per the 2026-07-15 repointing (see [[synthflow-voice-agent-routing]] memory), both workflows'
     "Make Phone Call" step were configured to invoke the **same** underlying assistant/number
     (the working one, `model_id 95fd0659-...`) — this is the fact that makes the shared-agent
     concurrency problem and the dynamic-prompt need real, and should be re-eyeballed (compare the
     `Assistant` dropdown value) while doing the mapping below, not just assumed from memory.

     **DONE (2026-08-23): `Prompt` ← `body.prompt` mapped and published on both workflow
     objects** — ColdLeads' (currently inert, since that workflow is disabled/not yet launched)
     and NewLeads' (the one actually live today). Also confirmed while mapping NewLeads': its
     `Assistant` field is set to the identical `Cora - Outbound Admissions Agent - ColdL`,
     matching ColdLeads' workflow — the shared-agent premise this whole spec is built on is
     directly confirmed, not just inferred from the July memory.

     Separately, each campaign also has its own distinct **"Completed Call"** workflow object
     (post-call processing): NewLeads' Completed Call (`yoBhMQSneCLTWM81xebZP`, enabled) and
     ColdLeads' Completed Call (`XXQ91isnW6vg60bX1qwA6`, disabled). **Confirmed by Kes
     (2026-08-23): the HTTP step inside these "Completed Call" workflows is the actual rate
     limiter** (not the voice agent, not the Make Call workflow) — the existing 75-second window
     already addresses it and is to be kept as-is, not changed. This closes what had been an open
     question about whether the constraint was per-workflow-instance or shared/downstream — no
     further investigation needed, just keep all `launch_outbound_call` scheduling on the existing
     75s grid as planned.

### Expected outputs

- Every `launch_outbound_call` job, regardless of origin (GHL New Lead trigger, GHL Cold Lead
  trigger, voicemail retry, nurture-scheduler batch, callback), has a `ScheduledJob` row and a
  slot-aware `run_at` on the shared 75s grid.
- When a New Lead job needs a slot currently held by a `pending` Cold Lead job, the Cold Lead job
  is moved to the next free bucket (single bump — see Constraints for the precise definition) and
  the New Lead job takes the vacated slot.
- Outbound Synthflow call payloads carry a campaign-appropriate prompt override once the
  injection piece ships.
- GHL's workflows are **not yet repointed** at the new endpoint by this spec — that cutover is a
  separate, explicitly gated step (see Out of scope).

### Known edge cases

1. A brand-new contact GHL has never sent to Cora before — no `lead_state` row exists yet. The
   intake endpoint must create one (find-or-create by phone), not assume `enter_campaign()`'s
   existing `lead: Any` argument is always pre-populated.
2. Duplicate GHL trigger (retry, double-fire) for a contact that already has a pending outbound
   job — must be idempotent, reusing the existing `_has_pending_outbound()` dedup check.
3. The bucket a New Lead job wants is occupied by a `claimed` or `running` Cold Lead job (a worker
   already grabbed it) — must **never** be touched or rescheduled; New Lead searches forward for
   the next actually-free bucket instead, same as today's non-priority search.
4. The bucket is occupied by another **New Lead** job — no bump needed/possible; search forward
   normally (priority only applies against lower-priority campaigns).
5. System-paused / outbound-campaigns-paused / cold-lead-paused flags are set when the GHL trigger
   arrives — endpoint must skip cleanly (same behavior as `enter_campaign()`'s existing pause
   checks), not silently drop the lead or error.
6. `nurture_scheduler.py`'s batch (up to 50 Cold Lead entries per 5-minute tick) lands at the same
   moment a New Lead trigger arrives — bump logic must handle a New Lead job displacing one job out
   of a dense, mostly-full stretch of the grid without erroring if the search has to look several
   buckets ahead for a free slot.
7. Synthflow rejects or silently ignores the `prompt` override field for Cora's model — must be
   confirmed before this is relied on (see Escalation Triggers), not assumed to work by analogy
   with Cory_dev.

### Out of scope

- **GHL workflow cutover itself** (repointing the "Send HTTP request" step in both New Lead and
  Cold Lead workflows from Synthflow's webhook to Cora's new endpoint). This is a live production
  config change on GHL's side, done manually by Kes only after this spec's acceptance criteria
  pass in isolation, with the current direct-to-Synthflow trigger kept as a fallback until the new
  path is confirmed. Building the endpoint and logic is in scope; flipping the switch is not.
- A second Synthflow assistant/number for Cold Lead — ruled out earlier in this project's
  discussion (not currently provisionable); this spec's entire premise is making one shared agent
  safe for both campaigns.
- Full `{{template}}`-style prompt interpolation inside Synthflow's own dashboard config — the
  chosen pattern (per Cory_dev) composes the full prompt text application-side and sends it as a
  per-call override, not provider-side templating.
- Cascading/multi-job bump chains — explicitly rejected by Kes given Cold Lead's overwhelming
  volume share makes starvation a non-issue in practice; only a single displaced job is ever moved.
- Any change to the existing `/v1/webhooks/calls` (call-completion) route or `call_processing.py`.

---

## 2. Acceptance criteria

1. Given a GHL New Lead trigger payload for a contact with no existing `lead_state` row, when the
   intake endpoint receives it, then a `lead_state` row is created and a `launch_outbound_call`
   job is scheduled with `campaign_name="New Lead"` and a slot-aware `run_at`.
2. Given a GHL Cold Lead trigger for a contact that already has a pending outbound job, when the
   intake endpoint receives it, then no second job is created (idempotent, mirrors
   `_has_pending_outbound()`).
3. Given `outbound_campaigns_paused=true` (or the campaign-specific pause flag), when a GHL trigger
   of either campaign arrives, then the endpoint accepts the request (does not error to GHL) but
   schedules no job — consistent with `enter_campaign()`'s existing pause behavior.
4. Given a New Lead job is being assigned a slot and the next bucket is free, when scheduled, then
   it takes that bucket directly — no behavior change from today for the uncontended case.
5. Given a New Lead job is being assigned a slot and the next bucket holds a `pending` Cold Lead
   job, when scheduled, then: the Cold Lead job's `run_at` is moved to the next free bucket found
   by the standard forward search (not an unconditional `+1 bucket`, to avoid creating a new
   collision), its `version` is incremented via the same optimistic-concurrency pattern
   `claim.py` uses elsewhere, and the New Lead job takes the vacated bucket.
6. Given a New Lead job is being assigned a slot and the next bucket holds a `claimed` or
   `running` job (any campaign), when scheduled, then that job is never modified and the New Lead
   job searches forward for the next free bucket instead.
7. Given the bump in criterion 5 occurs, when the displaced Cold Lead job's new `run_at` arrives,
   then it fires normally — the bump does not orphan, cancel, or corrupt the job.
8. Given `enter_campaign()` is called for any campaign (New Lead or Cold Lead) with a `run_at`
   that would previously have been `now`, when scheduled, then the resulting `run_at` is
   slot-aware (lands on the shared grid), matching the existing voicemail-retry behavior.
9. Given two leads enter a campaign within the same second (e.g. a `nurture_scheduler` batch tick),
   when both are scheduled, then they never land in the same bucket.
10. Given the Synthflow prompt-override spike (Constraint/Escalation below) confirms the `prompt`
    field is honored by Cora's model, then `launch_new_lead_call()` sends a campaign-appropriate
    prompt on every call; given the spike shows it is not honored, then this criterion is
    explicitly marked not-met in this doc rather than shipped as if it worked.
11. All new/changed code paths have unit tests with Synthflow/GHL HTTP calls mocked, following
    existing patterns in `tests/unit/test_synthflow_launch.py`, `tests/unit/test_campaign_switching.py`.
12. Full existing test suite (`python -m pytest tests/unit/ -q`) passes unmodified after this work,
    with no regressions to voicemail-retry spacing, campaign-switch rules, or shadow-mode behavior.

---

## 3. Constraint architecture

### Musts

- Must create a `lead_state` row (find-or-create by phone) when the intake endpoint receives a
  contact Cora has never seen — do not assume the row already exists.
- Must respect `system_paused`, `outbound_campaigns_paused`, and `cold_lead_campaign_paused` in
  the new endpoint, matching `enter_campaign()`'s existing checks exactly (skip, don't error).
- Must require authentication on the new intake endpoint (shared-secret header, checked against a
  new env var — e.g. `CORA_INBOUND_WEBHOOK_SECRET`) — this endpoint can trigger a real, billable
  outbound call, unlike the unauthenticated `/v1/webhooks/calls` completion callback; do not reuse
  that endpoint's no-auth posture.
- Must never log or expose the shared-secret value (Secret Safety Rule) — check presence/match
  only.
- Must only bump `pending` jobs — `claimed`/`running` jobs are never modified by priority logic.
- Must perform the bump via the same version-checked `UPDATE ... WHERE version = expected_version`
  pattern `claim.py` uses, not a raw update, to stay safe under concurrent workers.
- Must only bump jobs belonging to a lower-priority campaign than the incoming job (Cold Lead
  displaced by New Lead; New Lead never displaces New Lead).
- Must route `enter_campaign()`'s initial `run_at` through the same slot-aware helper voicemail
  retries already use (`_compute_window_run_at`/`_slot_aware_run_at`), not `run_at=now`.
- ~~Must confirm the `Prompt` field is mapped in both Make Call workflow objects~~ — **done
  2026-08-23**, both ColdLeads' and NewLeads' workflows mapped and published.
- ~~Must confirm whether `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New`/`_Cold` collapse to one URL~~ —
  **resolved 2026-08-23**: they are correctly two distinct, both-real workflow objects (see §1
  table); no code collapse needed, the existing per-campaign branch in `get_synthflow_launch_url()`
  is correct as-is.

### Must-nots

- Must not build cascading bump chains — a bump moves exactly one displaced job, full stop, per
  Kes's explicit decision (Cold Lead's volume share makes starvation risk from this negligible).
- Must not change `/v1/webhooks/calls` or `call_processing.py`.
- Must not repoint any GHL workflow as part of this spec's implementation — that is a separate,
  explicitly gated cutover step done by Kes after testing.
- Must not assume GHL's "Send HTTP request" step supports custom headers without confirming — if
  it doesn't, the auth mechanism needs to fall back to a token-in-URL or payload-field scheme
  instead of a header.
- Must not mutate the Synthflow assistant's stored/dashboard-configured prompt at call time (the
  risky pattern explicitly ruled out by the Cory_dev research — full override per call only).

### Preferences

- Prefer a new route file (e.g. `app/api/routes/call_intake.py`) over extending `webhooks.py`,
  mirroring spec/20's guidance to keep mechanically-different concerns in separate adapters/routes
  rather than conflating a low-risk data-logging endpoint with a high-risk call-triggering one.
- Prefer extending `outbound_jobs.py::_compute_window_run_at()` with an optional
  `campaign_name`/priority parameter over writing a parallel scheduling function, to keep exactly
  one bucket-occupancy code path in the codebase.
- Prefer composing the campaign-specific prompt text in a small, isolated function (analogous to
  Cory_dev's `_build_prompt()`) rather than inline in `launch_new_lead_call()`, so prompt content
  can be iterated on without touching the HTTP/retry logic.

### Escalation triggers

- If GHL's workflow "Send HTTP request" step cannot attach a custom auth header, stop and confirm
  an alternative auth mechanism with Kes before building the endpoint's auth around an assumption.
- ~~If a real test call with `prompt` populated does not actually change the assistant's spoken
  script...~~ — **this escalation trigger fired once (the standalone `Prompt` field, attempt 1 in
  §1), was resolved by switching mechanism (Custom Variables + `{prompt}` + blank Greeting
  Message, attempt 3), and is now closed** — confirmed working via real call, 2026-08-23.
- If GHL's actual New Lead/Cold Lead trigger payload shape (once captured) is missing a field this
  spec assumes is present (e.g. no stable GHL contact ID, only a phone number), stop and revise
  the endpoint's request schema before building against a guessed shape.

---

## 4. Decomposition / break pattern

1. **Capture GHL's current trigger payload** — pull the exact JSON body GHL's existing "Send HTTP
   request" step sends to Synthflow's webhook (from GHL's workflow config or a logged sample), so
   the new endpoint's request schema is built against reality, not assumption. No code; blocks
   step 2's schema.
2. **New intake endpoint** (`call_intake.py` or similar) — auth check, find-or-create `lead_state`,
   pause-flag checks, `_has_pending_outbound()` dedup, schedules `launch_outbound_call` with a
   slot-aware `run_at`. Verifiable in isolation: unit tests + a manual curl against local/staging
   with a test payload, no GHL involved yet.
3. **`enter_campaign()` slot-aware fix** — replace `run_at=now` with the same helper. Verifiable:
   existing `test_campaign_switching.py` still passes; new test asserts two same-second entries
   land in different buckets.
4. **Priority/bump logic in `_compute_window_run_at()`** — add the campaign-aware bump path
   (criteria 4-7). Verifiable: new unit tests for all four branches (free / pending-bumpable /
   claimed-untouched / same-priority-no-bump), run against the existing bucket-grid tests as a
   regression check.
5. **DONE — folded into step 2, not a separate wiring task.** The intake endpoint calls
   `enter_campaign()` directly rather than duplicating its scheduling logic, and `enter_campaign()`
   itself now threads `campaign_name` through to `_slot_aware_run_at()`/`_compute_window_run_at()`
   (step 3/4's work) — so New Lead vs Cold Lead priority is automatically correct for every GHL
   trigger with no additional wiring needed. Covered by `test_call_intake.py`'s happy-path tests
   plus the dedicated bump-logic tests in `test_outbound_jobs.py`.
6. **DONE, mechanism confirmed working (2026-08-23)** — see §1's full recipe. **One follow-up
   item carried into step 7's PR**: add the same Custom Variables `prompt` → `body.prompt` mapping
   to ColdLeads' Make Call workflow (currently only has the non-working standalone `Prompt` field
   from the abandoned first attempt) — small, mechanical, no ambiguity left now that the recipe is
   proven.
7. **Prompt composition + payload wiring** (only if step 6 confirms feasibility) — small
   campaign→prompt-text function that loads `docs/synthflow-warm-lead-prompt.md` (New Lead) or
   `docs/synthflow-cold-lead-prompt.md` (Cold Lead) by `campaign_name` and sends its full text as
   the `prompt` override, wired into `launch_new_lead_call()`'s payload. Verifiable: unit tests
   asserting New Lead vs Cold Lead calls carry the correct distinct `prompt` text.
8. **(Explicitly deferred, not part of this spec's build)** GHL workflow cutover for both
   campaigns, done by Kes after 2-7 pass and are demoed.

Steps 1 and 6 can happen in parallel with 2-3 since neither blocks the other. Each chunk is a
separate commit, tested before moving to the next, per this repo's standing conventions.

---

## 5. Evaluation design

### Unit tests (required before any chunk is considered done)

- Intake endpoint: valid New Lead payload → job created; valid Cold Lead payload → job created;
  missing/invalid auth → 401, no job created; duplicate trigger for a contact with a pending job →
  no second job; each pause flag set → accepted, no job scheduled; unknown/malformed payload → 4xx,
  no job, no crash.
- `enter_campaign()` spacing: two calls in the same tick → distinct `run_at` buckets (regression
  guard for the exact gap this spec closes).
- Bump logic, one test per branch: free bucket (no bump); pending Cold Lead in the way (bumped,
  version incremented, new `run_at` verified free); claimed/running Cold Lead in the way (untouched,
  New Lead searches forward instead); another New Lead job in the way (no bump, forward search).
- Bumped-job survival: after a bump, the displaced job's job_type/payload/entity are unchanged —
  only `run_at` and `version` differ — and it completes normally when its new time arrives.
- Prompt payload (once step 7 ships): New Lead vs Cold Lead campaign_name → distinct `prompt`
  values in the built payload; existing calls with no campaign-specific prompt configured still
  build a valid payload (no `None`/crash).

### Manual/integration verification (not automatable, human-confirmed)

- Step 6's Synthflow prompt spike: one real outbound test call, transcript/call behavior reviewed
  by Kes to confirm the override actually changed what the agent said.
- Step 5's wiring: a staged scenario (test contact, test flags) confirming a New Lead trigger
  visibly reschedules a pending Cold Lead test job's `run_at` in the database.

### Regression checks

- Full `python -m pytest tests/unit/ -q` suite passes unmodified.
- `ruff check app/ tests/` clean.
- No diff to `app/api/routes/webhooks.py`, `app/worker/jobs/call_processing.py` (confirms the
  completion-webhook path is untouched).
- Existing voicemail-retry spacing tests still pass with `_compute_window_run_at()`'s signature
  change (new optional parameter must default to today's exact behavior when omitted).

---

## 6. Open questions

- ~~Exact current GHL → Synthflow trigger payload shape~~ — **CONFIRMED 2026-08-23, and closed.**
  GHL's "Cora Outbound - New Leads" workflow action (Webhook, `POST` to
  `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New`) sends Custom Data: `phone` (via a GHL Number Formatter step
  first), `first_name` (`Contact.First Name`), `email` (`Contact.Email`). Kes confirmed these
  Custom Data fields are the **only** items GHL actually passes to Synthflow — "standard data" in
  the UI's helper text is not a separate hidden payload of concern; nothing further to chase here.
  No `campaign_name` field is sent explicitly — the intake endpoint will infer campaign identity
  from which GHL action/URL called it (New Lead vs Cold Lead are already distinct GHL actions), not
  from a payload field. **Not independently verified whether Cold Lead's GHL action has the
  identical shape — by Kes's explicit direction, not pre-verifying further; monitor during real
  testing and resolve any mismatch found then, rather than blocking on it now.**
- ~~Whether GHL's workflow HTTP step supports custom auth headers~~ — **CONFIRMED 2026-08-23.**
  The GHL webhook action has a `Headers` section (already carrying `Content-Type:
  application/json`) with an "Add another item" control — a shared-secret header (e.g.
  `X-Cora-Webhook-Secret`) is straightforward to add here. Unblocks the auth design in §3 Musts.
- ~~Whether `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New` collapses to `_Cold`'s URL~~ — **resolved
  2026-08-23**: no, and it shouldn't — they are two distinct, both-real, both-intentional workflow
  objects (NewLeads' enabled/live, ColdLeads' disabled/not-yet-launched). No code change needed.
- ~~Whether Synthflow's runtime actually honors a `prompt` override at call time~~ — **CONFIRMED
  WORKING, 2026-08-23, after 3 iterations.** Full timeline, since the working recipe is
  non-obvious and worth preserving exactly:
  1. **First attempt (failed)**: mapped the workflow step's standalone `Prompt` field (labeled
     "Pass the prompt for the Assistant") directly to `body.prompt`. Real test call: completely
     ignored — assistant used its static Greeting Message and generic fallback conversation the
     entire call. This field's actual behavior remains undocumented/unclear; abandoned in favor of
     the mechanism below.
  2. **Second attempt (partial)**: added `prompt` → `body.prompt` as a **Custom Variables** entry
     instead (same key/value list already used for `first_name`), and set the assistant's saved
     `Prompt` field to literally `{prompt}` (confirmed via the Agent builder's `{} Variables` →
     Pre-Call Variables panel, which listed `prompt` once exercised by a real call). Result: the
     injected text **did** reach the model — proven by the agent reciting the test sentence
     verbatim — but only when the customer explicitly asked about it mid-call; the call otherwise
     opened with the same static Greeting Message and drifted into unrelated small talk. The
     assistant's separate `Greeting Message` field (a hardcoded string, unrelated to campaign,
     still referencing the discontinued "Data Analytics bootcamp") was overriding the opening
     turn and apparently biasing the whole conversation's tone even after that.
  3. **Third attempt (full pass)**: cleared the `Greeting Message` field's text entirely (its own
     UI hints "leave blank to auto-generate a greeting" from the active prompt instead of using a
     fixed string). Real test call: opened **immediately** with the exact injected test sentence,
     no small talk, no prompting needed. Confirmed clean.

  **Working recipe (must be replicated per workflow, since Custom Variables mapping is
  per-workflow-object; the assistant-level Prompt/Greeting fix is shared automatically since both
  workflows call the same assistant):**
  - Workflow's "Make Phone Call and Get Call Data" step → Custom Variables → add `prompt` →
    `1. Catch Webhook body.prompt`. **Done on NewLeads' workflow. Still needed on ColdLeads'
    workflow** (it only has the old, non-working standalone `Prompt` field mapped from attempt 1).
  - Assistant's ("Cora - Outbound Admissions Agent - ColdL") saved `Prompt` field = exactly
    `{prompt}`, nothing else. **Done, shared by both workflows** (same assistant).
  - Assistant's `Greeting Message` → Configure → `Message` field = blank (auto-generate). **Done,
    shared by both workflows.**
  - Assistant deployed after each change (not just saved as a draft).

  Cora's payload (`prompt` key, top-level JSON, sent to whichever campaign's workflow URL) is
  exactly what step 7 needs to send — confirmed against the real mechanism now, not an assumption.
- ~~Whether the "Completed Call" workflows' HTTP step concurrency is per-instance or shared~~ —
  **resolved 2026-08-23**: Kes confirmed this HTTP step is the actual rate limiter, and the
  existing 75-second window already addresses it — no further investigation needed, keep using it
  as the shared spacing constant for all `launch_outbound_call` scheduling.
- Whether `Recipient Name`'s existing mapping to `body.first_name` (vs. Cora's payload sending
  `"name"`) is a pre-existing bug causing empty recipient names on live calls today — deferred to
  testing per Kes's direction, not pre-investigated further.
