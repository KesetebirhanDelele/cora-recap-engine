# PROGRESS.md

Running log of work across sessions on `feat/ghl-call-conversation-sync`, to allow any
session (human or agent) to pick up where the last one left off. Update this file at the
end of a substantial work session — append, don't rewrite history.

Read this alongside `CLAUDE.md` at session start.

---

## Standing rule: `feat/ghl-call-conversation-sync` is the production-tracking branch, not `main`

**This is a durable operating rule, not a session note — follow it every session until this
section says otherwise.**

`main` on GitHub is stale by design in this repo. The 2026-07-09/07-11 session (see below)
discovered production was running a mix of Docker images built from different divergent
branches, root-caused it, and fully merged `feat/production-deployment-hardening` (111 commits)
and `feat/support-staff-shift-routing` (3 commits) into `feat/ghl-call-conversation-sync`. At
that point all three known branches — `main` and both of the above — had **zero commits missing**
from `feat/ghl-call-conversation-sync`, and production was rebuilt from that branch's tip.
`feat/ghl-call-conversation-sync` is therefore a strict superset of `main`, not a fork that needs
reconciling with it.

**How to apply:**
- Cut new feature branches from `feat/ghl-call-conversation-sync`, never from `main`:
  `git checkout feat/ghl-call-conversation-sync && git pull && git checkout -b <new-branch>`.
- Do not merge feature work into `main`, and do not attempt to "sync" `main` up to date as a
  side effect of unrelated work — that is a deliberate, separate decision requiring Kes's
  explicit approval, not a default action.
- `main` being the GitHub-default / PR-target branch label is a repo-settings artifact, not a
  signal about where production code lives. Don't infer branch choice from that label alone.

**How to verify this is still true (run before trusting the above, especially if it's been a
while since the last session):**
```
git fetch origin
git log feat/ghl-call-conversation-sync..main --oneline
git log feat/ghl-call-conversation-sync..feat/production-deployment-hardening --oneline
git log feat/ghl-call-conversation-sync..feat/support-staff-shift-routing --oneline
```
All three must return **empty**. A non-empty result means one of those branches has commits
`feat/ghl-call-conversation-sync` doesn't have — the invariant has broken (someone committed
directly to `main` or another branch, bypassing this one) and needs investigating before
building anything new. If it breaks, update this section with what happened and the new state —
don't just silently re-fix it and move on.

Confirm production actually matches this branch's tip before relying on any of the above as
current fact: `docker compose images` on the Hetzner box should show all services built from the
same recent timestamp/commit. Git branch topology alone doesn't prove the running containers
match — Docker images can lag behind the branch.

---

## Session: 2026-07-17 — Cold Lead 404 alert triage, Cold-Lead-only pause control, branch-drift check

**Branch**: `feat/cold-lead-campaign-pause` (cut from `feat/ghl-call-conversation-sync`)

### What happened

Kes brought five `Synthflow launch HTTP error: 404` alerts (contacts +19592022210 ×2,
+15714782790, +16127301379, +18084292459; 2026-07-15 22:00 UTC through 2026-07-17 15:46 UTC).
Queried `scheduled_jobs` for the five `job_id`s — all five are **Cold Lead** campaign, all
`status='failed'`.

1. **Built a `cold_lead_campaign_paused` mode flag**, scoped narrower than the existing
   `outbound_campaigns_paused` (which holds New Lead + Cold Lead together). Mirrors the existing
   flag's pattern at every check site: `app/core/mode_flags.py`, `app/core/campaigns.py`
   (`enter_campaign`), `app/worker/jobs/{outbound,voicemail,crm,ai,channel}_jobs.py`,
   `app/worker/jobs/nurture_scheduler.py`, new dashboard endpoints
   (`/mode/pause-cold-lead-campaign`, `/mode/resume-cold-lead-campaign`), and a new "Cold Lead
   Campaign Pause" section in `SystemControlsClient.tsx`. New tests in `test_campaigns.py` and
   `test_outbound_jobs.py` cover Cold-Lead-skipped / New-Lead-unaffected in both the entry point
   and the job-execution guard. Full unit suite run (`--ignore=test_e2e_scenarios.py`): no new
   failures, confirmed via `git stash` diff against the pre-change baseline (all 46 pre-existing
   failures are environment gaps — missing `openai` package, no local Redis/tables — unrelated to
   this branch). **As of this entry: implemented and tested locally, not yet committed.**

2. **Mid-session branch-drift scare, resolved**: initially cut this branch from `main`, then
   discovered `main` was missing `outbound_campaigns_paused` and the whole pause mechanism
   entirely — 138 commits behind. Re-cut from `feat/ghl-call-conversation-sync` instead. Root
   cause and the durable fix (cut from the sync branch, always) is now written up as the standing
   rule above so this doesn't recur.

3. **Confirmed root cause of the timing, via `app_config`/`audit_log` on Hetzner:** the 2026-07-11
   session recorded production as `shadow_mode_enabled=true` and `outbound_campaigns_paused=true`
   at that time, and explicitly listed "the Synthflow Cold Lead webhook is stale/broken and needs
   checking before live calling resumes" as a known, unresolved blocker. Confirmed:
   `shadow_mode_enabled` flipped `true → false` at **2026-07-15 21:44:41 UTC**
   (`app_config.updated_by = 'dashboard'` — generic fallback operator ID, see note below). The
   first two Cold Lead 404s in today's alert set fired at **22:00:50 UTC the same day — 16 minutes
   later**. So: someone took the system live without the webhook fix happening first, and it broke
   almost immediately. It ran live and broken for Cold Lead for **2 days** before these alerts were
   brought to this session. `outbound_campaigns_paused` itself was never re-enabled after 07-11
   (confirmed unrelated to this flip); `shadow_mode_enabled` was the only lever that changed.
   **Audit trail gap**: `updated_by='dashboard'` is `SystemControlsClient.tsx`'s generic fallback
   when no operator has set an `operator_id` in their browser's `localStorage` — there is currently
   no way to tell *who* flipped this from the DB alone. Not fixed this session; flagging as a real
   gap if attribution ever matters (e.g. after an incident like this one).
   **As of this entry, `shadow_mode_enabled` remains `false` (live)** — New Lead and any SMS/email
   campaigns have been making real contact with real leads for 2 days with no reported issues.
   This has not been independently reviewed as an intended state versus an unannounced side effect
   of whatever prompted the 07-15 flip — worth a deliberate decision, not just inertia.

### Root cause vs. the fix built this session — do not conflate these

- **Actual root cause of the 404s — found, not yet fixed:** the Cold Lead "Make Call" workflow was
  turned **off** in Synthflow's dashboard. A disabled workflow returning 404 on its webhook is
  expected behavior — simpler than the 07-11 session's "stale/broken" framing suggested, not a
  misconfiguration, just a toggle left off (likely during the New Lead repoint work or earlier
  testing, never turned back on). Explains why only Cold Lead 404s and New Lead doesn't — New
  Lead's workflow is still active. **Fix is in Synthflow's UI, not this repo**: turn the workflow
  back on, then do one controlled test call before un-pausing real traffic — don't repeat the
  07-15 pattern of flipping live and assuming it's fine.
- **What this session built**: a Cold-Lead-only pause toggle. This is an operational safety net
  (hold Cold Lead without also holding New Lead) — it does **not** fix the webhook. Calls will
  404 again the moment Cold Lead is unpaused until the Synthflow-side fix happens.

### Design investigation — New Lead call priority in the shared pacer (not built, findings only)

Explored whether New Lead calls should be prioritized over Cold Lead in the shared call-pacer
(`_compute_window_run_at` in `outbound_jobs.py`, 4 calls/5-min window, shared by both campaigns).
Findings, in case this comes up again:

- **A brand-new lead's first call is not scheduled by this repo at all.** `enter_campaign(...,
  "new_lead", ...)` is never called anywhere in the codebase — confirmed by grep, matches the
  07-15 session's finding. The first call is triggered externally, almost certainly a GHL
  workflow hitting Synthflow directly; Cora only takes over once the completion webhook arrives.
  So "prioritize New Lead's first call" targets something that never touches our scheduler —
  there's nothing to prioritize there. Cold Lead entry, by contrast, does go through
  `enter_campaign(..., "cold_lead", ...)` and does hit our job queue.
- **Two different things both get called "callback" in this codebase** — don't conflate them:
  (1) automatic voicemail-tier retries (`voicemail_jobs.py`, routine system re-dial after
  no-answer, goes through the pacer every time), and (2) lead-requested explicit callbacks
  (`intent_actions.py`'s `_handle_callback_request`/`_handle_callback_with_time`/
  `_handle_call_later_no_time` — lead said "call me back" / "call me at 3pm" mid-conversation).
- **Explicit callback requests bypass the pacer's protection entirely, not just its delay.**
  `_schedule_outbound_call` sets `run_at` directly to the requested time, with no coordination
  against the pacer's slot math. Within the same lead this is safe (`handle_intent` cancels all
  other pending jobs for that contact first). **Across different leads it is not** — nothing
  reschedules other leads' paced calls around a fixed-time callback, and nothing stops a fixed
  callback from landing in the same ~75s window as calls the pacer already placed there. The
  pacer's count-based math implicitly assumes every job in the pool was placed by the same
  evenly-spaced grid logic; an arbitrary-timestamp callback breaks that assumption without being
  detected. The "+2h fallback" path (used whenever a time can't be extracted — likely the common
  case) makes this a real collision risk, not just theoretical, since many unrelated leads'
  callbacks could land near the same timestamp with zero coordination.
- **This is a pre-existing gap, independent of Cold Lead's pause/webhook issue.** Fixing it does
  **not** unblock Cold Lead — that's gated purely on the Synthflow workflow toggle above. If
  tackled, the right fix is not a tweak to the counting formula but a different algorithm:
  bucket every 75s off a fixed epoch (the pattern `_slot_aware_run_at` already uses), track actual
  occupancy (including exact-time callbacks) rather than just a count, and have reschedulable
  jobs (voicemail retries, deferred first-calls) walk forward to the next genuinely free bucket
  instead of assuming a clean grid. Bigger than it first looks — it replaces core placement logic
  for every reschedulable call in the system, both campaigns, so it needs real test coverage
  before landing. **Not started. No code changed for this.**
- Still open, unrelated to the above: whether New Lead's automatic voicemail-tier retries (as
  opposed to its first call, which isn't ours to prioritize) should out-rank Cold Lead's in the
  shared pacer. Not decided.

### Update — same session, pacer/callback collision fix built and shipped

Kes decided to fix the collision gap above before touching the New Lead priority question (which
remains undecided, unrelated, not blocking anything). Built exactly the design described above:
`_compute_window_run_at` in `outbound_jobs.py` replaced with a real bucket-occupancy search
(fixed shared `_EPOCH`, `_bucket_index()`/`_bucket_start()` helpers, walks forward to the next
genuinely free 75s bucket instead of approximating via a count). `voicemail_jobs.py`'s
`_slot_aware_run_at` now imports the shared `_EPOCH` instead of a local duplicate, so every
scheduling path — first calls, voicemail retries, and lead-requested callbacks — lands on the
same grid and can't silently collide.

Caught one real bug along the way, independent of the design: SQLite returns naive datetimes for
timezone-aware columns, which crashed the new bucket math on first contact with a stored
`run_at`. Fixed by normalizing naive datetimes to UTC in `_bucket_index()` — this would have hit
local/shadow-mode dev too, not just tests.

Rewrote the `_compute_window_run_at` and `_slot_aware_run_at` burst tests for the new occupancy
semantics (the old tests stacked jobs at one identical timestamp, which doesn't map to
bucket-based logic) and added a test for the actual collision scenario: an off-grid timestamp
(simulating an exact-time callback) reserves its bucket and a subsequent call routes around it.
Full suite: 41 failed / 945 passed — failures **dropped** from the pre-existing 46 baseline (this
fix incidentally repaired 5 tests that were already broken under the old algorithm's arithmetic,
unrelated pre-existing issue). Zero new failures anywhere, confirmed via targeted runs of
`test_intent_actions.py`, `test_real_world_callback_intents.py`, `test_channel_jobs.py`,
`test_crm_jobs.py`, `test_campaigns.py`. Committed and pushed directly to
`feat/ghl-call-conversation-sync` (`d4ef713`) — Kes approved via debrief, no separate branch/PR
for this one.

**Still true, restated so it isn't missed later:** this fix does not touch, and is not required
for, resuming Cold Lead — that remains gated purely on the Synthflow workflow toggle.

### Update — same session, after Kes tested on Hetzner

Kes deployed `feat/cold-lead-campaign-pause` to Hetzner (`git pull` + `docker compose up -d
--build`), toggled "Pause Cold Lead Campaign" in the dashboard, and confirmed it works — shown as
"Cold Lead Campaign Paused" with a working "Resume" button. Merged into
`feat/ghl-call-conversation-sync` (fast-forward, `d9d94aa..f09ce52`, no PR needed — see the new
Branching Model section in `CLAUDE.md`) and pushed. **Hetzner is still running the
`feat/cold-lead-campaign-pause` checkout, not the merged `feat/ghl-call-conversation-sync` tip —
functionally identical (fast-forward, same commit), but the next session should `git checkout
feat/ghl-call-conversation-sync` there to keep the branch label consistent with what's documented
as the production-tracking branch.**

Also added a permanent "Branching Model" section to `CLAUDE.md` (cutting/merging procedure,
verification commands) so this doesn't need re-explaining every session — the standing rule below
now has a companion procedure doc.

### Update — same session, shadow-mode flip explained + New Lead verified healthy

Kes confirmed the 07-15 21:44 UTC `shadow_mode_enabled` flip was **intentional** — done so New
Lead campaigns could start working live. At the time, no granular per-campaign control existed
(`cold_lead_campaign_paused` didn't exist until this session), and `outbound_campaigns_paused`
holds New Lead + Cold Lead together — so there was no way to bring New Lead live without also
exposing Cold Lead's already-known-broken webhook as an unavoidable side effect. The real gap
was that the 07-11 session's explicit note — "webhook needs checking before live calling
resumes" — wasn't checked first. Not a tooling failure at the time; today's `cold_lead_campaign_paused`
flag exists specifically so this choice doesn't have to be all-or-nothing again.

Verified New Lead is genuinely healthy, not just quiet: `call_events` for the last 2 days shows 6
completed, 4 hangup_on_voicemail, 1 no-answer, 1 failed (12 total) — a normal outbound-calling
outcome mix. Unlike Cold Lead's 404s (which fail *before* Synthflow ever creates a call, so no
`call_events` row exists for those attempts), New Lead calls are reaching Synthflow and being
answered/not-answered normally. The single "failed" row is a call-level outcome, not a launch
failure — not concerning at this volume.

**Decision:** leave `shadow_mode_enabled=false` (live) as-is — it was intentional and New Lead is
confirmed working. `cold_lead_campaign_paused=true` stays on until the webhook is fixed.

### Next steps, in order

1. **Kes to turn the Cold Lead "Make Call" workflow back on in Synthflow's dashboard** — found
   this session to be simply toggled off, explaining every 404. Do one controlled test call to
   confirm it actually completes before un-pausing `cold_lead_campaign_paused` for real traffic.
2. Pacer/callback collision fix is **done** (see update further below, commit `d4ef713`). Still
   open: whether New Lead's automatic voicemail-tier retries should out-rank Cold Lead's in the
   shared pacer — findings only, nothing built, no urgency tied to unblocking Cold Lead.
3. Optional cleanup: switch Hetzner's git checkout from `feat/cold-lead-campaign-pause` to
   `feat/ghl-call-conversation-sync` (same commit, just a label mismatch).
4. Consider whether `operator_id` should be required (not defaulting to `'dashboard'`) for
   mode-flag changes, given the audit-trail gap surfaced this session.
5. Everything carried over from the 2026-07-15 session below is still open and untouched by this
   session.

---

## Session: 2026-07-15 — Synthflow voice-agent routing swap, DNC gap analysis, new-lead trigger source identified

**Branch**: `feat/ghl-call-conversation-sync`

### What changed

1. **New Lead campaign's Synthflow assistant repointed to Cold Lead's, live in production.**
   The New Lead agent's own phone number was lost. Kes fixed it directly in Synthflow's
   dashboard (not in this repo) by changing the New Lead "Make Call" workflow
   (`p6ihFj7HmplXM2WiuVsaC`)'s Assistant field from its own agent to "Cora - Outbound
   Admissions Agent - ColdL" (model_id `95fd0659-7446-423c-bc51-764c3060c90f`). Confirmed live
   via a `call_events` cross-tab query: New Lead calls flipped from the old model_id
   (`2608601d-...`, last seen 16:06 UTC) to the Cold Lead model_id (first seen 22:00 UTC) on
   2026-07-15. Updated the stale mapping comment in `app/adapters/synthflow.py` to match.
   Important: `CallEvent.voice_agent` labeling (`ColdLead`/`NewLead`/`Inbound`) comes from the
   Synthflow workflow's own `Agent` text field, not the model_id — so New Lead calls still
   correctly label as `NewLead` in the dashboard despite sharing an assistant with Cold Lead.
   The old New Lead assistant now has no phone number and isn't wired to any workflow —
   **not yet decided** whether to retire it or re-provision a number.

### Findings — not yet acted on, need a decision

2. **`launch_outbound_call_job` never checks `lead_state.do_not_call` before dialing.**
   (`app/worker/jobs/outbound_jobs.py`) It checks `system_paused`, `outbound_campaigns_paused`,
   the static `blocked_dial_numbers` list, and the campaign active window — but not the DNC
   flag itself. In normal operation this is masked because `handle_intent` cancels pending jobs
   when `do_not_call` fires, but there's no belt-and-suspenders check at the actual dial point,
   so a stale/re-scheduled job could still call a DNC-flagged contact. Small, low-risk fix,
   proposed but not built.

3. **Cora never ingests GHL's own DNC-style tags.** `lead_state.do_not_call` is only ever set
   from Cora's own live intent detection — there's no sync pulling in pre-existing GHL tags
   like `do not contact` / `do not call again` / `spam likely`. Surfaced by a real inbound call
   (`call_id c9620c5f...`, contact `+19729921028`) where GHL already had this contact tagged
   do-not-contact from a stale 2025 interaction (an unrelated caller from "Pulte Mortgage"), but
   Cora's Inbound agent still delivered a full personalized re-engagement pitch. Needs a spec
   (which GHL tags count as DNC-equivalent, live check vs. periodic sync) before building.

4. **Inbound agent doesn't act on GHL tags it already fetches.** The Synthflow Inbound workflow
   calls a custom function (`get_the_user_preferences_from_gohighlevel`) that returns the GHL
   contact including tags, but the assistant's prompt doesn't branch on do-not-contact tags —
   this is a Synthflow-side prompt/workflow fix, outside this repo's control.

5. **Likely test/QA artifact found in production call data**: an inbound call's
   `phone_number_from` exactly matched a New Lead outbound call's target number placed 32
   seconds earlier (`0d5f0ce1` → `0b453e92`, both 2026-07-15 ~22:41 UTC) — consistent with an
   automated harness that answers Cora's outbound call and immediately rings back into Cora's
   Inbound line. Not confirmed with whoever runs QA; flagged for awareness only.

6. **Signed recording URLs in `call_events.recording_url` have ~100-year expiry**
   (`Expires=4937753238`, i.e. year 2126) — effectively permanent, unauthenticated access to the
   audio for anyone who obtains the URL. Noted as a hygiene concern, not yet addressed.

### Architecture fact confirmed this session (previously undocumented)

7. **The first call to a brand-new "New Lead" is not triggered by any code path in this repo.**
   Checked thoroughly: the only inbound API route is `POST /v1/webhooks/calls` (Synthflow
   *completed*-call intake, not a new-lead notification), there is no polling job that discovers
   new leads, and `enter_campaign(session, lead, "new_lead", ...)` is never actually called
   anywhere in the worker/job code (only `"cold_lead"` call sites exist, from
   `intent_actions.py` and `nurture_scheduler.py`). The first dial for a new lead is therefore
   almost certainly triggered externally — most likely a GHL workflow hitting Synthflow's
   "Make Call" webhook directly — and Cora only takes over once that first call's completion
   webhook arrives. Not confirmed against the actual GHL workflow config (inferred from absence
   of any other trigger path in this codebase) — worth verifying with whoever owns that
   automation. If it ever breaks, new leads would silently never enter Cora's pipeline.

### Next steps, in order

1. Decide whether to fix item 2 (outbound `do_not_call` dial-point guard) — small and low-risk,
   ready to build on approval.
2. Spec item 3 (GHL DNC-tag ingestion) before building — needs a decision on tag matching rules
   and sync mechanism.
3. Raise item 4 (Inbound agent prompt gap) with whoever owns the Synthflow Inbound workflow —
   not fixable from this repo.
4. Confirm item 7 (external New Lead trigger source) with whoever owns the GHL workflow that
   presumably fires the first call — currently undocumented outside this session's inference.
5. Decide fate of the old, numberless New Lead Synthflow assistant (retire vs. re-provision).
6. Carried over from the previous session, still open: permanent domain + TLS reverse proxy
   (blocked on Ali's DNS record), real production Colaberry GHL OAuth install, staff portal
   build, app-wide logging gap, pre-existing test failure triage.

---

## Session: 2026-07-09 to 2026-07-11 — GHL Conversations write-back, branch consolidation, staff portal planning

**Branch**: `feat/ghl-call-conversation-sync`
**Latest commit at end of session**: `e3a32e1` — "Merge remote-tracking branch 'origin/feat/support-staff-shift-routing' into feat/ghl-call-conversation-sync"

### What shipped

1. **GHL Marketplace Conversations OAuth write-back** (spec/19, spec/20) — fully working end
   to end against the sandbox, verified with a real API write and a human visual check in the
   GHL UI (Acceptance Criterion 12 passed). Two real bugs found and fixed along the way:
   - `get_location_token()` was missing the required `Version` header on
     `POST /oauth/locationToken` — every real install failed with a 401.
   - GHL Marketplace test installs can land on a different company/agency each session; the
     target-location config has to match whichever company actually authorizes.
   - Full reusable verification procedure documented in spec/20 §9.

2. **Branch consolidation** — discovered production was running a mix of Docker images built
   from different branches/times (different services literally running different code).
   Root-caused and fully merged the two other divergent feature branches into this one:
   - `feat/production-deployment-hardening` (111 commits — webhook recovery, DB Explorer,
     worker activity dashboards, voice performance v2, PgBouncer, stale-lead recovery, slot
     rebalancer, outbound-campaign-pause control, and more).
   - `feat/support-staff-shift-routing` (3 commits — shift-based customer support call-summary
     routing, `app/core/staff_roster.py`).
   - All three known remote branches (`main`, both of the above) now have zero commits missing
     from this branch.
   - Fixed a real gap found during this work: `outbound_campaigns_paused` was checked in six
     worker job files but not in `app/core/campaigns.py`'s `enter_campaign()` or its two direct
     callers in `intent_actions.py` — centralized the check in `enter_campaign()` itself so
     every current and future caller is protected uniformly.
   - Fixed a pre-existing test gap (`test_outbound_jobs.py`'s flag mock missing the new field).
   - Fixed a Synthflow call-status bug: `"user-canceled"` wasn't in `_FAILED_STATUSES`, causing
     it to be misrouted through full AI processing on empty-data calls.

3. **Caught and fixed a live production data leak**: `GHL_OAUTH_TARGET_LOCATION_ID` was left
   pointed at the sandbox location after AC12 testing, causing every real production call's
   `write_conversation_log` job to attempt a write using the sandbox's token against a contact
   that doesn't exist under that authorization — real GHL 401s for about an hour before caught.
   Fixed (commented out in the server's `.env`) and documented in spec/20 §9.4 as a required
   cleanup step after every sandbox verification run.

### Current live production state (Hetzner box, `/opt/cora-recap-engine`)

- All 8 app services rebuilt consistently from this branch's latest commit (confirm with
  `docker compose images` — all should share the same build timestamp).
- `shadow_mode_enabled` = **true** (Shadow) — no real outbound Synthflow calls go out.
- `outbound_campaigns_paused` = **true** — New Lead / Cold Lead campaign activity held.
- `GHL_WRITE_CONVERSATION_LOG` = true, but `GHL_OAUTH_TARGET_LOCATION_ID` is commented out, so
  it correctly falls back to the real production `GHL_LOCATION_ID` (`ttBtJmxoLwjf18lIvvVD`),
  which has no stored OAuth token — writes cleanly no-op (Acceptance Criterion 9), which is the
  safe/correct state until the real production install happens.
- OAuth is installed only for the sandbox location (`eWe9cRDf0UmSSIBxBMAO`) — **not** the real
  Colaberry account yet.
- ngrok is still the OAuth redirect URI (`4654-...ngrok-free.app`) — ephemeral, will need
  re-syncing again if it restarts.

### Explicitly NOT done yet (do not assume otherwise)

- Real OAuth install against the production Colaberry GHL location — blocked on (a) confirming
  billing/plan implications with GHL, (b) a permanent domain (see below).
- GHL Conversations recording attachment and transcript delivery — deferred, spec/20 §7 has the
  full investigation trail (GHL's own upload endpoint's output fails its own validation).
- Fixing the Synthflow Cold Lead webhook 404 (the original alert that kicked off this session's
  investigation) — no longer urgent since shadow mode covers it, but the webhook URL itself is
  still stale/broken in Synthflow's dashboard and needs checking before live calling resumes.
- App-wide `logger.info()` being silently swallowed — `settings.log_level` is only ever wired
  into `uvicorn.run()`'s own logger, never into Python's root logger via `logging.basicConfig()`.
  Non-blocking, but affects observability everywhere, not just this feature.
- Staff portal for admissions/support assistants (see below) — planning only, no code yet.

### Next steps, in order

1. **Waiting on Ali**: DNS A record `cora.colaberry.com → 204.168.245.238`
   (BC ticket [10084773637](https://app.basecamp.com/3945211/buckets/15139308/todos/10084773637)).
2. Once DNS is live: reverse proxy (Caddy recommended — automatic Let's Encrypt) in front of
   `frontend`/`dashboard-api`/`api`, bind those services to `127.0.0.1` only.
3. Update the GHL Marketplace app's Redirect URI to the permanent domain, retire ngrok.
4. Confirm billing/plan implications, then do the real production Colaberry GHL install
   (spec/20 §9 has the exact repeatable procedure).
5. Build the staff portal — decided approach: separate portal (not role-gating the existing
   dashboard, which has no role concept anywhere today), individual accounts (`staff_users`
   table, bcrypt password hashes), Redis-backed sessions with httpOnly secure cookies. Needs
   the domain/TLS from steps 1-2 first — do not build the login flow over plain HTTP.
6. Separate, non-blocking cleanup: the app-wide logging gap (item above), and two clusters of
   pre-existing test failures worth triage — 5 tests in `test_enrolled_intent.py` /
   `test_ghl_adapter.py` / `test_inbound_call_processing.py` (predate this session), and 5
   slot-rebalancer timing tests in `test_outbound_jobs.py` (confirmed pre-existing on
   `feat/production-deployment-hardening` itself via a clean-checkout reproduction, not
   introduced by the branch merge).

---

## Session: 2026-08-23 — GHL→Cora call routing, New Lead priority, per-campaign dynamic prompts

**Branch**: `feat/call-launch-priority-routing` (cut from `feat/ghl-call-conversation-sync`) —
**merged and pushed to `feat/ghl-call-conversation-sync` this session** (commit `dcadd2e`,
fast-forward, no conflicts). Full design/decision trail in `directives/spec/21_call_launch_priority_routing.md`.

### What happened

1. **Problem**: Cold Lead is about to go live sharing the same Synthflow voice agent as New
   Lead. GHL currently triggers Synthflow's "Make Call" workflow *directly* for first-touch
   calls, completely bypassing Cora — no `ScheduledJob`, no visibility, no way to prevent a
   GHL-triggered call from colliding with anything Cora itself is scheduling at the same moment.
   Separately, `enter_campaign()`'s first-touch call used `run_at=now` with no spacing at all,
   unlike voicemail retries which were already on a shared 75s bucket grid.

2. **Built** (all on the merged branch, 835 unit tests passing, zero regressions vs. base):
   - `POST /v1/webhooks/leads/{campaign_type}` (`app/api/routes/call_intake.py`) — GHL will call
     this instead of Synthflow directly. Shared-secret auth (`CORA_INBOUND_WEBHOOK_SECRET`, new
     setting), find-or-create `lead_state` by phone, delegates everything else to
     `enter_campaign()`.
   - `enter_campaign()` now schedules via the same slot-aware helper voicemail retries use,
     closing the no-spacing gap.
   - `_compute_window_run_at()` gained priority/bump logic: a New Lead job can displace a
     *pending* Cold Lead job from a contested slot (single bump, version-checked optimistic
     concurrency, never touches claimed/running jobs) — New Lead is <2% of volume but
     time-sensitive, Cold Lead is the overwhelming majority and isn't.
   - Per-campaign dynamic prompt injection: `launch_new_lead_call()` now sends a full
     campaign-specific system prompt (`docs/synthflow-warm-lead-prompt.md` /
     `docs/synthflow-cold-lead-prompt.md`) as the outbound payload's `prompt` field.

3. **Dynamic-prompt mechanism required real trial and error against Synthflow's actual
   platform** (not just docs) — worth recording since it's non-obvious and easy to get wrong
   silently:
   - The standalone workflow-step `Prompt` field ("Pass the prompt for the Assistant") does
     **not** work as a full override — tested with a real call, completely ignored.
   - The working mechanism: a `Custom Variables` entry (`prompt` → `body.prompt`) on the
     workflow's "Make Phone Call" step, **and** the assistant's own saved prompt set to literally
     `{prompt}` (nothing else), **and** the assistant's Greeting Message field cleared (it
     otherwise always speaks first with a stale, hardcoded line referencing the discontinued
     Data Analytics bootcamp, which also biased the rest of the conversation even after the
     `{prompt}` fix). All three together, confirmed via a real call opening immediately with the
     injected test sentence.
   - Both Make Call workflow objects — `NewLeads` (`p6ihFj7HmplXM2WiuVsaC`, **enabled**, the one
     placing real calls today) and `ColdLeads` (`33J546NiXxUUIRCbywNVH`, **disabled** — Cold
     Lead hasn't launched yet, which is the entire reason this work exists) — got the Custom
     Variables mapping. The assistant-level fix (`{prompt}` + blank greeting) is shared
     automatically since both workflows call the same underlying assistant
     (`model_id 95fd0659-...`, "Cora - Outbound Admissions Agent - ColdL").
   - `execution/test_scripts/test_prompt_override.py` — standalone verification spike, POSTs
     directly to Synthflow bypassing Cora's job queue entirely. Kept in the repo as the
     reference for retesting this mechanism if Synthflow's behavior ever changes.

4. **GHL's actual current trigger payload confirmed** (captured from the live "Cora Outbound -
   New Leads" action, not guessed): Custom Data = `phone` (via a GHL Number Formatter step),
   `first_name`, `email`. No `campaign_name` field — the new intake endpoint infers campaign
   identity from the URL path instead. GHL's webhook action does support custom headers
   (confirmed via its `Headers` section), which is what makes the shared-secret auth viable.

### Explicitly NOT done yet (do not assume otherwise)

- **Hetzner has not been redeployed.** The merge/push above only updated the remote branch —
  production is still running whatever was deployed before this session. Needs, on the server
  (`/opt/cora-recap-engine`): `git pull && docker compose up -d --build` (a real rebuild, not
  just a restart — actual code changed, not only an env value).
- `CORA_INBOUND_WEBHOOK_SECRET` needs adding to the server's `.env` (separately from local —
  confirm with Kes whether this happened yet) before the new endpoint will accept anything.
- **GHL's cutover itself is deliberately deferred** — repointing GHL's New Lead and Cold Lead
  actions from Synthflow's URL to Cora's new endpoint is a separate, explicitly gated step for
  Kes to do once he's satisfied with testing; the direct-to-Synthflow trigger stays live as a
  fallback until then. See spec/21's Out of Scope section.
- **Unknown whether `shadow_mode_enabled` / `outbound_campaigns_paused` are still `true` on
  production** — both were `true` as of the 2026-07 session logged just above this one. If
  they're still set that way, the new endpoint will accept requests and schedule jobs correctly,
  but `launch_outbound_call_job` will no-op in shadow mode rather than placing real calls (this
  differs from this session's own prompt-override test calls, which bypassed the job queue
  entirely via the standalone script and were never subject to these flags). Check current state
  before assuming a live GHL cutover would actually dial anyone.
- Bare-IP HTTPS (`https://204.168.245.238`, no domain yet) may present a self-signed/non-CA
  certificate that GHL's outbound HTTP client could reject — untested. The DNS/reverse-proxy
  plan from the session above (`cora.colaberry.com`, blocked on Ali/DNS as of that session) would
  resolve this properly; worth checking whether that's landed since, before relying on the bare
  IP for the real GHL cutover.

### Next steps, in order

1. Confirm `CORA_INBOUND_WEBHOOK_SECRET` is set on the Hetzner server's `.env`, then
   `git pull && docker compose up -d --build`.
2. Check current `shadow_mode_enabled` / `outbound_campaigns_paused` state on production —
   decide deliberately whether the GHL cutover should happen before or after turning these off.
3. `curl` the new endpoint directly (command in this session's chat log) to confirm it responds
   correctly before touching any GHL config.
4. Check whether the bare-IP TLS cert issue actually blocks GHL (or whether DNS/reverse-proxy
   has landed since the 2026-07 session, making this moot).
5. Only then: Kes repoints GHL's New Lead and Cold Lead actions to the new Cora endpoint.

---

## Session: 2026-08-24 — Hetzner deploy, TLS via Caddy, GHL intake endpoint verified live

**Branch**: `feat/ghl-call-conversation-sync` (direct commits — infra-only, no feature branch needed
for this kind of change per this repo's convention).

### What happened

1. **Deployed yesterday's merged code to production**: `git pull && docker compose up -d --build`
   at `/opt/cora-recap-engine`. Confirmed `cora_inbound_webhook_secret` loaded correctly
   post-rebuild (it previously threw `AttributeError` — the code implementing it hadn't reached
   the server yet; a plain `.env` edit alone can never fix a missing-field error like that, only
   a real code deploy can).

2. **Discovered no TLS existed at all.** `docker compose ps` showed `api` exposed only on plain
   HTTP (`0.0.0.0:8000`), nothing listening on 443 — `curl https://204.168.245.238` returned
   connection refused. The `GHL_OAUTH_REDIRECT_URI=https://204.168.245.238/oauth/callback` value
   already sitting in `.env` was stale/aspirational, never actually reachable.

3. **Added Caddy as a reverse proxy** (`docker-compose.yml` + new `Caddyfile`, committed) for
   automatic HTTPS via Let's Encrypt, fronting a free `sslip.io` hostname
   (`204-168-245-238.sslip.io` — encodes the server's IP, needs no domain registration/account).
   Deliberately additive only: `api`'s existing plain-HTTP `8000:8000` binding was left untouched
   so nothing already depending on it (whatever Synthflow's completion webhook currently targets)
   could break. Locking that port down to `127.0.0.1` is a separate, later decision.

4. **Extended secret-sync debugging** — local `.env` and Hetzner's `.env` drifted on
   `CORA_INBOUND_WEBHOOK_SECRET` across several rounds: a plain `--no-deps` restart didn't
   actually reload the new value (Compose didn't detect the `.env` *content* change as requiring
   a container recreate — only `--force-recreate` guarantees a fresh environment load), and two
   rounds of manual copy/paste into `nano` still didn't produce a matching value even after a
   real recreate. Root-caused definitively via **SHA-256 hash comparison** — proves whether two
   secrets are identical without either side ever being exposed or printed, which settled it
   after a "manually confirmed, looks the same" visual check had already been wrong twice (easy
   to miss a single differing character in a 43-char random string by eye). Fixed by generating
   one fresh canonical value and setting it via `sed -i` in place on both sides, avoiding the
   manual retyping in `nano` that was the likely actual source of drift.

5. **Confirmed full end-to-end success**: `POST https://204-168-245-238.sslip.io/v1/webhooks/leads/new_lead`
   returns `202 Accepted` over real HTTPS (valid Let's Encrypt cert, verified via Caddy's
   `Via: 1.1 Caddy` response header) with matched-and-hash-verified secret auth. The GHL intake
   endpoint built in the 2026-08-23 session is now deployed and confirmed working in production
   — not just unit-tested against an in-memory DB.

### Recommendation flagged to Ali: get the real domain (`cora.colaberry.com`) live

The current TLS setup depends on `sslip.io`, a free third-party service that turns the server's
raw IP into a hostname Let's Encrypt will issue a certificate for. It correctly unblocked testing
today with zero registration or waiting, but it is **not** something to leave as the permanent
setup once real GHL lead traffic depends on it continuously. Two concrete risks, explained to Kes
in CEO-facing terms this session:
1. **Not owned or controlled by Colaberry** — an external free service; if it goes down or
   changes its policy, the integration breaks with no warning and no recourse.
2. **Tied to this specific server's IP address** — if the server ever moves (migration, scaling,
   disaster recovery), the address changes and every integration pointing at it (this one, and
   anything built on it later) breaks and needs manual reconfiguration everywhere it's
   referenced. A domain Colaberry owns can instead be repointed to a new IP in one place.

The DNS A record `cora.colaberry.com → 204.168.245.238` has been pending on Ali since the 2026-07
session (BC ticket
[10084773637](https://app.basecamp.com/3945211/buckets/15139308/todos/10084773637)) — worth
re-flagging now that there's a live, working dependency on the interim solution, not just a
hypothetical future need.

### Explicitly NOT done yet

- **GHL has not been repointed.** Both New Lead and Cold Lead workflow actions in GHL still
  trigger Synthflow directly — the new Cora endpoint is deployed and verified but nothing is
  actually using it yet. Deliberate, separate decision for Kes.
- `shadow_mode_enabled` / `outbound_campaigns_paused` current state was never rechecked this
  session — still unknown whether they're on or off right now. Check before assuming a GHL
  cutover would place real calls immediately versus silently no-op in shadow mode.
- The permanent domain is still not live — `sslip.io` is a functioning bridge, not the answer.
- Test `lead_state`/`scheduled_job` rows for the placeholder number `+15550001234` (created
  during this session's verification calls) are still sitting in production — harmless, not
  required to remove, but worth knowing they exist if anyone notices them while browsing the DB.

### Next steps, in order

1. Get Ali's DNS record live, then repoint the `Caddyfile` at the real domain (Caddy re-issues
   the certificate automatically on the next request once the hostname changes).
2. Check `shadow_mode_enabled`/`outbound_campaigns_paused` state before any GHL cutover.
3. Kes repoints GHL's New Lead and Cold Lead actions to the new Cora endpoint (exact URLs/header
   already given to him this session).
4. Optional cleanup: remove the `+15550001234` test rows from `lead_state`/`scheduled_jobs`.

---

## Session: 2026-08-24 (cont'd) — fixed voicemail-tier crash for contacts who also called inbound

**Branch**: `fix/voicemail-tier-campaign-name-drift` (cut from `feat/ghl-call-conversation-sync`,
merged and pushed same session, commit `85a6e32`, fast-forward, no conflicts).

### What happened

1. **Diagnosed a critical dashboard alert**: `process_voicemail_tier` failing with
   `Unknown campaign type: 'Inbound'` for contact `+16822812224`, job 12 days old. Traced the
   full path through the code and confirmed against real production data via SSH (`call_events`
   and `lead_state` queried directly on Hetzner):
   - The failing call itself was a completely ordinary **outbound New Lead** call (from Cora's
     `+19729921028` caller ID, to `+16822812224`, `hangup_on_voicemail`, 2026-08-11) — not a
     self-call, not an inbound misroute.
   - The same real contact separately called **into** Cora's own inbound line on other occasions,
     which set their `lead_state.campaign_name` to `"Inbound"`.
   - `process_voicemail_tier` (`voicemail_jobs.py`) was reading `lead.campaign_name` off the row
     in preference to the job's own payload, so by the time the outbound retry job ran, it read
     the now-drifted `"Inbound"` value instead of `"New Lead"`, and `tier_policy.get_tier_policy`
     correctly rejects `"Inbound"` (it's not a tier-retry campaign) — crashing the job every time
     it fired.
2. **Root cause**: a single `lead_state.campaign_name` field can't hold two independent facts at
   once (last outbound campaign vs. "this contact also called our inbound line") — whichever
   channel touches the row last silently overwrites the other's context.
3. **Fix**: `voicemail_jobs.py` now resolves `campaign_name` by trusting the job payload's own
   explicit value first (what the call was actually placed under), falling back to the row only
   when the payload didn't carry one — unchanged behavior for that case (GHL-sync /
   auto-created-row convenience the original fallback existed for).
4. **Tests**: 2 new regression tests in `test_voicemail_jobs.py` — one reproduces the exact bug
   (drifted row + explicit payload campaign → must not crash, must use the payload's campaign),
   one locks in the preserved fallback behavior when the payload omits campaign_name entirely.
   `test_voicemail_jobs.py`: 20/20 passing. Full suite: 1008 passed, 9 failed — confirmed via
   `git stash` that all 9 failures reproduce identically on the base branch (unrelated
   pre-existing flakiness in `channel_jobs`/`ghl_adapter`/`messaging`/`shadow_mode`/
   `enrolled_intent`/`inbound_call_processing`, none touching `voicemail_jobs.py`) — zero
   regressions from this change.
5. **Deployed to Hetzner**: merged to `feat/ghl-call-conversation-sync`, pushed, then
   `git pull && docker compose up -d --build` on the server. All 12 containers came up healthy;
   `worker-retries`/`worker-default` logs confirmed clean startup with jobs processing normally.

### Next steps

- The original failed job for `+16822812224` is still sitting in the dashboard as an unresolved
  critical alert from 12 days ago — safe to manually **Resolve** now that the underlying bug is
  fixed; it will not self-clear.
- Worth a quick scan for any *other* contacts with the same `lead_state.campaign_name = "Inbound"`
  + a pending/stuck outbound voicemail-tier job, in case this bug produced more than one silent
  failure historically.

---

## Session: 2026-08-24 (cont'd 2) — Cold Lead went live: 6 production bugs found and fixed same-day

Branch: all commits directly to `feat/ghl-call-conversation-sync` (this repo's convention — no PR
required for merges into it, see top-of-file branching rule). Every fix below was verified against
real production data via SSH before and after deploy, not just local tests.

### What happened, in order

1. **Cold Lead calls were routing to a dead workflow** (`e1520c8`). `get_synthflow_launch_url()`
   branched on `"cold"` in the campaign name to pick `SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold` — but
   that workflow (`33J546NiXxUUIRCbywNVH`) has no phone number attached and cannot place real
   calls. Only `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New` (`p6ihFj7HmplXM2WiuVsaC`, phone
   `+19729921028`) is live. Fixed to always return the shared New workflow URL; campaign identity
   is now carried entirely through the dynamic `prompt` payload field, not URL routing. Confirmed
   via a real test call to Kes's own phone using the actual Cold Lead prompt.
2. **Prompt gap found from that real test call** (`81d035e`): the agent asked an open-ended "what's
   on your mind?" after the greeting instead of proceeding into the pitch, making outbound calls
   feel like the lead had called in. Added an explicit transition instruction after the Section 1
   greeting in both `docs/synthflow-cold-lead-prompt.md` and `docs/synthflow-warm-lead-prompt.md`.
3. **Critical: `docs/` was never baked into the Docker image** (`5823b6c`). Discovered while
   verifying the prompt fix — `_load_campaign_prompt()` raised `FileNotFoundError` inside the
   running container. `Dockerfile` never had `COPY docs/`, and `.dockerignore`'s blanket `*.md`
   rule excluded it even if it had. Confirmed severity by querying `scheduled_jobs`: **zero**
   `launch_outbound_call` completions since 3 days before this deploy — every real production
   call had been silently failing the entire time Cold Lead was supposedly live. Fixed both files;
   verified by loading both prompts from inside the rebuilt container.
4. **Dashboard hiding real scheduled calls** (`16e5ebb`). Kes reported Cold Lead's scheduled calls
   missing from the "Scheduled Actions" view. Traced to `LEAST(scheduled_job.run_at,
   lead_state.next_action_at)` in the dashboard query picking a stale, days-old `next_action_at`
   left over from a prior nurture-wait state over the genuinely fresh scheduled call. Fixed at the
   source: `enter_campaign()` now clears `next_action_at` on every campaign entry. Confirmed 5/7
   real Cold Lead contacts were hidden before the fix.
5. **All outbound calls misattributed to "New Lead" regardless of actual campaign**. Root cause:
   Synthflow's completion webhook self-reports a static `campaign_name` baked into the now-shared
   workflow's config — it does not echo what Cora actually sent at launch, and can't, now that
   both campaigns share one workflow (see fix #1). Fixed (`7bf2055`) by resolving
   `call_events.campaign_name`/`voice_agent` from Cora's own most recent *completed*
   `launch_outbound_call` job for the contact instead of trusting Synthflow's self-report; inbound
   calls untouched; falls back to the payload's own value when no launch job is found. Backfilled
   that day's misattributed rows directly in production, excluding one contact
   (`+15714782790`) whose most recent launch job had a blank campaign value rather than guessing.
6. **Cora's outbound dialer was repeatedly calling its own Inbound line**. `+16822812224`
   (confirmed via Synthflow's Agents dashboard: "Cora - Inbound Admissions Agent") had been dialed
   as an outbound "lead" 48 times since April — because 33 separate `lead_state` rows created from
   real inbound callers have `normalized_phone` incorrectly set to Cora's own inbound number
   instead of the caller's own number (a GHL-side data mapping bug, not yet root-caused or fixed —
   flagged as separate follow-up work). Stopgapped by setting `BLOCKED_DIAL_NUMBERS=+16822812224`
   on Hetzner's `.env` (the guard already existed in `outbound_jobs.py`, just unconfigured in
   prod) — confirmed loaded, no pending job was at risk at deploy time. Env-only change, no code
   commit; containers force-recreated to pick it up.
7. **Uncapped low-confidence-audio retry loop, found from a different angle of the same
   self-call investigation**: reviewing today's Call Logs turned up `+18666932332` ("Mazda
   Financial Services", an automated IVR) being called every ~15 minutes non-stop since May.
   Traced the mechanism: every call to an IVR produces a too-short/noisy transcript →
   `low_confidence_audio` intent → `_handle_low_confidence_audio()` called `enter_campaign(...,
   "cold_lead", ...)` **unconditionally, on every occurrence** — and `enter_campaign()` always
   resets the voicemail tier to 0 and schedules an immediate call. No cap, no backoff, no terminal
   state — unlike the structurally identical `_handle_partial_engagement()`, which retries twice
   with a 2h gap then escalates once. Fixed by removing the auto-retry/auto-escalate entirely:
   `_handle_low_confidence_audio` now only transitions the lead to `status="cold"` and schedules
   nothing further — a number that produces low-confidence audio once will keep doing so forever
   (IVR/wrong number/disconnected line), so any retry cadence just delays the same infinite loop.
   Confirmed via grep that `handle_intent()`'s dispatch table has no campaign gating, so this
   applied identically to New Lead, Cold Lead, and Inbound-originated contacts alike. 2 tests
   updated in `test_live_call_intents.py`. Full suite: 1040 passed, 5 failed — same 5
   pre-existing flaky failures as before (`MagicMock` vs `int` in `app/worker/claim.py:270`,
   unrelated), confirmed zero regressions from this change.

### Explicitly NOT done yet

- **GHL-side root cause of the `+16822812224` phone mismapping (item 6) is unresolved.** The
  `BLOCKED_DIAL_NUMBERS` fix is a stopgap on Cora's side only; the 33 `lead_state` rows still have
  the wrong `normalized_phone` value, and whatever GHL workflow is writing Cora's own inbound
  number into new inbound-caller contacts' phone field is still doing so. Needs a GHL-side
  investigation, not a Cora-side one.
- **`+15714782790` was excluded from the campaign-attribution backfill** (item 5) — its most
  recent launch job had a blank `campaign_name`. Never manually resolved; still shows whatever
  attribution it had before the fix.
- **Same auto-retry design gap identified in `partial_engagement`, `interested_not_now`,
  `uncertain`, `failed_booking`, and unconfirmed `human_transfer_request` intents — but for
  Inbound-originated calls specifically, not fixed yet.** All five schedule a future outbound
  call (either directly, or via the `nurture` → `nurture_scheduler` → Cold Lead pipeline) with no
  campaign-awareness, meaning someone who merely calls Cora's Inbound line and has an ambiguous
  conversation — never asking for a callback, never booking — can still end up auto-enrolled into
  an outbound calling campaign days later. Flagged to Kes; scope of the fix (skip auto-retry
  specifically for `campaign_name == "Inbound"`, or something narrower) not yet agreed.

### Next steps

1. Investigate the GHL workflow responsible for writing `normalized_phone` on new inbound-caller
   contacts — find why it's writing Cora's own inbound number instead of the caller's.
2. Decide and implement the Inbound-campaign auto-retry scope boundary (item above).
3. Manually resolve `+15714782790`'s campaign attribution if/when the real value is known.

---

## Session: 2026-08-24 (cont'd 3) — closed out every uncapped retry loop found in the item-7 audit

Follow-up to the "Explicitly NOT done yet" list above. Walked through each flagged intent handler
one at a time with Kes, decided the right terminal behavior for each, and implemented all of them
same session. All commits directly to `feat/ghl-call-conversation-sync`.

### What happened, in order

1. **`partial_engagement` → no retry** (same reasoning as `low_confidence_audio`, same session as
   item 7 above): removed the capped-retry-then-escalate design entirely. Now calls the same
   `_mark_cold_no_retry()` terminal action `low_confidence_audio` uses. Removed the now-unused
   `PARTIAL_ENGAGEMENT_RETRY_CAP` constant and its counter helper.
2. **Traced `interested_not_now`/`uncertain` end-to-end** for a Cold Lead contact: confirmed they
   never actually change `campaign_name` for a lead already in Cold Lead (no switch rule exists
   for that transition), but they DO set `status="nurture"` + `next_action_at`
   (`NURTURE_DELAY_DAYS` env — **2 days in production, not the code default of 7** — found via
   direct `.env` check), and `nurture_scheduler.py` graduates that back into a fresh tier-0 Cold
   Lead call automatically. No cap ever existed on this path.
3. **Audited for other uncapped loops** given the above pattern. Found two more genuinely uncapped
   handlers (`failed_booking`, and `human_transfer_request` when unconfirmed) and confirmed the
   voicemail-tier engine itself (`tier_policy.py`) is fine — it has a real terminal tier "3".
   Production query (`scheduled_jobs` grouped by `contact_id` + `intent_reason`) turned up one
   contact, `+19592022210`, with **83 accumulated retries since April** across
   `callback_with_time` (52), `transfer_requested` (13), `booking_retry` (8), `callback_request`
   (6), `call_later_no_time` (4) — traced to `lead_state.normalized_phone` for that contact (and 3
   others in the same query) being `+16822812224`, the same self-call mismapping bug from item 6,
   just surfacing through different handlers this time. Zero currently-pending jobs under any of
   these reasons — nothing actively looping right now, `BLOCKED_DIAL_NUMBERS` already covers it.
4. **New variant of the mismapping bug found in the same audit**: one contact,
   `+16153199706` (`status="closed"`, not currently at risk), has `normalized_phone` set to
   `+19729921028` — Cora's own **Outbound** caller ID, not the Inbound one. Same bug, opposite
   direction; this number was not yet in `BLOCKED_DIAL_NUMBERS`.
5. **Decided and implemented terminal behavior for every flagged path**:
   - `failed_booking` / unconfirmed `human_transfer_request`: these carry real signal (an actual
     booking/transfer attempt happened), so retry isn't wrong in principle — capped at 2 each
     (`FAILED_BOOKING_RETRY_CAP`, `HUMAN_TRANSFER_RETRY_CAP`), then fall through to
     `_mark_cold_no_retry()` instead of looping forever.
   - `callback_request` / `callback_with_time` / `call_later_no_time`: deliberately left uncapped
     — explicit lead asks ("call me back"); a cap would break the legitimate case. Residual risk
     there is intent misclassification, not the retry policy.
   - `interested_not_now` / `uncertain`: the Cold-Lead-only check from item 7 was broadened to
     also cover `campaign_name == "Inbound"` — a lead who called *us* and gave one ambiguous
     answer must not get auto-enrolled into an outbound campaign days later just because they
     didn't explicitly ask for a callback or book. New Lead is untouched — still gets the
     legitimate one-time nurture-then-downgrade into Cold Lead. Helper renamed
     `_is_cold_lead` → `_blocks_nurture_retry` to reflect the broadened scope.
   - `BLOCKED_DIAL_NUMBERS` extended to `+16822812224,+19729921028` (both of Cora's own Synthflow
     numbers now blocked from ever being dialed as a "lead"). Also cleaned up a pre-existing
     duplicate `BLOCKED_DIAL_NUMBERS` line in the local `.env` while making this change.
6. **Tests**: added/updated across `test_intent_actions.py` and `test_live_call_intents.py` —
   cap-boundary tests (below-cap-still-retries / at-cap-marks-cold) for both `failed_booking` and
   `human_transfer_request`, and Inbound-scoped no-retry tests for both `interested_not_now` and
   `uncertain` (alongside the existing New-Lead-still-nurtures regression tests). Full suite:
   1049 passed, 5 failed — same 5 pre-existing flaky failures as every other run this session
   (`MagicMock` vs `int` in `app/worker/claim.py:270`, unrelated), zero regressions.
7. **Updated `directives/spec/06_architecture.md`** to document the full current state of every
   live-call intent handler's retry/terminal behavior in one place.

### Incidental finding — secrets surfaced in an AI conversation

Editing the local `.env` (to update `BLOCKED_DIAL_NUMBERS`) triggered an automatic "file changed
on disk" diff that put the entire file's contents — including live `GHL_API_KEY`,
`GHL_CONVERSATIONS_API_KEY`, `OPENAI_API_KEY`, `SYNTHFLOW_API_KEY`, and `POSTGRES_PASSWORD` — into
the Claude Code conversation context. Not triggered by any print/cat command, just a side effect
of the harness's edit-tracking. Flagged to Kes in the same session; no values were repeated back.
**Worth a credential rotation if this needs to be treated as a real exposure** — not yet decided
or actioned.

### Explicitly NOT done yet

- The credential rotation question above.
- The underlying GHL-side phone-mismapping root cause (item 6 from the prior session) is still
  unresolved — this session's fixes are all Cora-side stopgaps/policy fixes, not a fix to GHL
  itself.
- `+15714782790`'s campaign attribution still unresolved (unchanged from prior session).

### Next steps

1. Decide on the credential rotation question.
2. Investigate the GHL workflow responsible for writing Cora's own numbers into new contacts'
   `normalized_phone` field (both the Inbound and now-confirmed Outbound variants).
3. Deploy this session's changes to Hetzner (code + the `BLOCKED_DIAL_NUMBERS` env update).

## Session: 2026-08-24 (cont'd 4) — urgent-escalation guard (branch `feat/lead-escalation-suppression`)

### What prompted this

Kes pasted a real call log for one lead (Deborah): a human rep (RS) called and left a voicemail
at 2:40 PM; at 2:54 PM an inbound Synthflow call ("Cora - Inbound Admissions Agent") booked her a
callback with an Admissions Advisor after she asked to speak with a human; at 3:12 PM an unrelated
outbound campaign trigger cold-pitched her the AI Systems Architect Accelerator as if none of that
had happened. Separately, the transcript also showed the bot accepting "Two PM" as a booking time
though it had only offered 10:30/11:30/12:30, and the GHL record shows the appointment landed at
3:00 PM, not the 2:00 PM confirmed verbally — both are Synthflow-hosted-action bugs (slot
validation + a timezone step), not fixable in this repo; not addressed this session.

### Investigation

- Confirmed via code read: neither `app/core/campaigns.py::enter_campaign()` nor
  `app/worker/jobs/outbound_jobs.py::launch_outbound_call_job()` — nor
  `POST /v1/webhooks/leads/{campaign_type}` (`call_intake.py`), which calls `enter_campaign()`
  unconditionally — ever checked whether a lead had a recent escalation before dialing. This
  wasn't a regression; the check never existed (`directives/spec/dashboard/12_open_questions.md`
  OQ-07 explicitly deferred any GHL appointment integration in v1).
- Initially scoped this as "poll GHL for appointment/tag data" (Kes's original ask), but a live
  Synthflow trace Kes pasted mid-session (a `GET /contacts/search/duplicate` call from an agent
  literally named "Cora - Inbound Admissions Agent") showed the 2:54 PM escalation call is Cora's
  own inbound Synthflow call, not an external GHL-native bot. That means the escalation is already
  captured in `call_events.detected_intent` and already scores "urgent" in the existing Sales
  Queue (`dashboard_metrics.py::_INTENT_SCORES`) — no new GHL integration needed for the confirmed
  bug. Re-scoped with Kes's sign-off to ship only the pre-call gate this session; GHL-native
  (non-Cora-call) escalation polling deferred as a separate ticket — see
  `directives/spec/22_urgent_escalation_guard.md` Out-of-scope, which also flags that GHL's public
  docs list an inconsistent `Version: v3` header for the appointments endpoint (vs. `2021-07-28`
  used everywhere else in this integration) that would need live verification before shipping.

### What shipped

1. **`app/core/escalation_guard.py`** (new) — `check_urgent_unresolved(session, contact_id)`.
   Blocks when the most recent `call_events` row for a contact in the last 30 days has
   `detected_intent` in `{human_transfer_request, callback_request, callback_with_time, enrolled}`
   and `lead_state.sales_outcome` is not yet set (i.e. no rep has triaged it via the Sales Queue).
   Deliberately narrower than `_INTENT_SCORES`'s "urgent" tier — that tier also includes
   `re_engaged`, which means "call this lead more," not "a human already took over." No schema
   change — reads existing `call_events`/`lead_state` columns only.
2. **`enter_campaign()` gate** — checked right after the existing pause-flag checks; skips entry
   entirely and logs a `warning`-severity `outbound_suppressed_urgent_escalation` exception.
3. **`launch_outbound_call_job()` gate** — belt-and-suspenders, checked right after the existing
   blocked-dial-number guard; cancels the job + same exception type. Catches jobs already
   scheduled before the escalation, or entered via nurture scheduler / voicemail-tier retries
   (neither goes through `enter_campaign()`).
4. **Tests**: `tests/unit/test_escalation_guard.py` (10 new), plus 2 new tests each in
   `test_campaigns.py` and `test_outbound_jobs.py`. Full suite: 1004 passed (up from 1000),
   41 pre-existing failures unchanged (confirmed via `git stash` — all `ModuleNotFoundError:
   openai`-cascade, local-env-only, present on the base branch too; zero regressions).
5. **`directives/spec/22_urgent_escalation_guard.md`** (new) — full spec per this repo's Five
   Primitives (problem statement, acceptance criteria, constraints, out-of-scope, eval design).

### Explicitly NOT done yet

- GHL-native escalation ingestion (a human rep tagging a contact directly in GHL, or an
  appointment with no associated Cora call) — needs Kes to confirm/grant calendar+tag scopes on
  the GHL Private Integration token and a live test of the (currently unverified) appointments
  endpoint before it's safe to build against.
- The two Synthflow-hosted-action bugs from the original transcript (slot validation accepting an
  unoffered time; the 2 PM confirmed vs. 3 PM created timezone mismatch) — live in Synthflow's
  custom-action config, not this repo.
- The separately-tracked, still-open `lead_state.do_not_call` gap in `launch_outbound_call_job`
  (not touched by this change).
- Not yet merged into `feat/ghl-call-conversation-sync` or deployed to Hetzner — awaiting Kes's
  test confirmation per the Pre-Ship Debrief Rule.

### Next steps

1. Kes verifies the golden path (see debrief below), confirms go-ahead.
2. Merge `feat/lead-escalation-suppression` → `feat/ghl-call-conversation-sync`, redeploy Hetzner.
3. Decide whether to open a follow-up ticket for GHL-native escalation polling, and separately
   whether to escalate the two Synthflow-hosted-action bugs to whoever owns that config.
