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
- ~~Not yet merged into `feat/ghl-call-conversation-sync` or deployed to Hetzner~~ — **done
  2026-08-25**: Kes confirmed the debrief, merged `feat/lead-escalation-suppression` →
  `feat/ghl-call-conversation-sync` (fast-forward, `9702521..764b4ee`), pushed, and redeployed
  Hetzner (`docker compose up -d --build`). `migrate` ran clean (no schema change in this
  release), `pgbouncer`/`postgres` healthy, all 11 services `Up` post-rebuild. Live in production.

### Next steps

1. Decide whether to open a follow-up ticket for GHL-native escalation polling (a human rep
   tagging a contact directly in GHL, or an appointment with no associated Cora call — see
   spec/22 Out-of-scope), and separately whether to escalate the two Synthflow-hosted-action bugs
   (slot validation, 2 PM/3 PM timezone mismatch) to whoever owns that Synthflow config.

## Session: 2026-08-25 — staff call quality analysis (branch `feat/call-quality-analysis`)

### What prompted this

Kes asked whether recordings between human sales reps/support staff and leads/students (placed
through GHL's own native dialer, not Cora/Synthflow) could be pulled, transcribed, and quality-
scored — something Cora had zero visibility into before this session. Built in explicit stages
per this repo's spec-first rule, with live verification against real GHL/production data before
writing permanent code at every step (see `directives/spec/23_staff_call_quality_analysis.md` for
the full spec).

### Key discoveries, in order

1. **GHL exposes recording + transcription endpoints natively** — `GET .../recording` and
   `GET .../transcription` — but the current `GHL_API_KEY` token has no Conversations scope at
   all (confirmed live: 401 "not authorized for this scope"). There's already a second,
   correctly-scoped key in `.env` (`GHL_CONVERSATIONS_API_KEY`, built for the 2026-07-16
   InternalComment write path) that the read side had simply never used — not a missing scope,
   a wrong-key bug in the first draft of the discovery test.
2. **GHL is not transcribing calls on this account** — confirmed live against a real 4-month-old
   completed call (`400 CONVERSATIONS_MSG_RECORDING_NOT_FOUND`), ruling out a processing-delay
   explanation. Pivoted the "use GHL's transcript" plan (Kes's original preference) to Whisper —
   confirmed working end-to-end live (real GHL recording, 731,884 bytes, → OpenAI transcription,
   ~6s round trip).
3. **Rep identity lives on the message (`userId`), not the conversation** — confirmed via a full
   raw-object dump. Per Kes: no static rep roster needed, use whatever GHL reports per call.
4. **Lead-vs-student routing field found, but ambiguous** — three near-duplicate GHL picklist
   fields (`Who you are`, `Select an option that best describes you` ×2) share identical options
   (`Potential Student`/`Current Student`/`Business or Partner`) — likely leftover form
   duplicates; the one sample contact checked had none of them populated. Per Kes: check all
   three, persist each one's raw value on every row, decide which is authoritative once real
   data accumulates — do not consolidate now.
5. **Compliance-disclosure content sourced, not invented** — pulled directly from
   `docs/synthflow-warm-lead-prompt.md` / `docs/synthflow-cold-lead-prompt.md` §5 (identical in
   both): dual pricing transparency, no guaranteed-job-placement language, consent before
   SMS/booking, closed DA bootcamp, honest scholarship answer.
6. **Support rubric judges each call against what was actually requested** — per Kes's explicit
   direction. The scoring prompt has the model identify the caller's specific request from the
   transcript itself first, then scores resolution against that — GHL's support-ticket fields
   (`Support Issue`, `What is your issue related to?`, and the student's own post-call CSAT
   survey answers, all discovered in the 85-field custom-field dump) are supporting context only,
   since they can't be reliably tied to *this specific* call (no per-field timestamp, up to 4
   stale numbered ticket slots).

### What shipped

1. **`app/adapters/ghl.py`** — `api_key_override` (use `GHL_CONVERSATIONS_API_KEY` instead of the
   contacts-only `GHL_API_KEY`), `search_conversations()`, `get_message_recording()`,
   `get_message_transcription()`. 15 new tests.
2. **`app/adapters/openai_client.py`** — `transcribe_audio()`, same retry/error shape as
   `chat_completion()`. 8 new tests.
3. **`migrations/versions/0021_staff_call_quality.py`** + **`app/models/staff_call_quality.py`**
   — new table, not yet applied to production (ships with this branch's deploy).
4. **`app/core/call_classification.py`** — `classify_from_known_signals()` (GHL fields →
   `enrollment_date` → Cora's own call history) and `classify_from_transcript_ai()` (last-resort
   fallback). 26 tests — caught a real bug: the first draft of the AI-classification prompt had
   unescaped `{ }` around a literal JSON example, which Python's `.format()` silently
   misinterpreted as a template field and would have crashed every AI classification call in
   production. Fixed before it ever ran for real.
5. **`app/core/ghl_support_context.py`** — pulls issue category/description/CSAT fields from a
   raw GHL contact. 8 tests.
6. **`app/core/call_quality_scoring.py`** — sales + support rubric prompts and `score_call()`,
   with a call-outcome gate (no score for non-connected or <20s calls). 15 tests.
7. **`app/worker/jobs/staff_call_quality_jobs.py`** — periodic scan job, same
   claim/run/reschedule shape as `webhook_recovery_jobs.py`; 15-minute interval, 24h lookback,
   20-per-cycle cap, per-item error isolation. 19 tests.
8. **Off-by-default safety gate**: `STAFF_CALL_QUALITY_SCAN_ENABLED` (new setting, defaults
   `false`) — added after realizing the job as designed would otherwise auto-start scanning every
   sales/support call location-wide the moment it's deployed, spending real OpenAI transcription/
   scoring credits with no explicit opt-in. Gated inside `_run_scan_cycle()`; job still
   self-reschedules on its normal cadence but no-ops (no GHL calls, no OpenAI calls, no rows)
   until Kes flips it on.
9. **`directives/spec/23_staff_call_quality_analysis.md`** (new) — full spec per this repo's Five
   Primitives.
10. **`.env.example`** — documented `GHL_CONVERSATIONS_API_KEY` (was previously undocumented
    despite being live in production since 2026-07-16), `OPENAI_MODEL_CALL_TRANSCRIPTION`,
    `STAFF_CALL_QUALITY_SCAN_ENABLED`.

Tests: 94 new this session, full suite 1125 passed / 11 failed — same 11 pre-existing,
unrelated local-environment failures confirmed via `git stash` comparison both before and after
each major addition; zero regressions.

### Explicitly NOT done yet

- **Dashboard page** — no UI to review scored calls yet; data is queryable directly from
  `staff_call_quality`.
- **Historical backfill** — the scan job only picks up calls in its rolling 24h lookback window
  going forward; `GET /conversations/search`'s `lastMessageType` filter reflects a conversation's
  *most recent* message only, so it isn't reliable for a one-shot backfill (see spec/23).
- **Live end-to-end test of the actual scan job** — every individual piece (GHL reads, Whisper
  transcription, classification, scoring) was live-validated against real production data before
  being written permanently, but the full job hasn't been run together outside of mocked unit
  tests. Recommended before flipping `STAFF_CALL_QUALITY_SCAN_ENABLED` on.
- Not yet committed, merged, or deployed — awaiting Kes's review per the Pre-Ship Debrief Rule.
  Given the real dollar cost once enabled, this one especially should get a live test run before
  `STAFF_CALL_QUALITY_SCAN_ENABLED=true` goes into production `.env`.
- Which of the three duplicate GHL routing fields is actually authoritative — needs real
  accumulated data to decide (that's the whole point of persisting all three).

### Next steps

1. Kes reviews the rubric prompts and routing logic before this goes live for real.
2. Merge `feat/call-quality-analysis` → `feat/ghl-call-conversation-sync`, redeploy Hetzner (with
   `STAFF_CALL_QUALITY_SCAN_ENABLED` left `false` initially).
3. Once deployed, manually trigger one scan cycle against a small, known set of real calls to
   sanity-check output before enabling the recurring schedule for real.
4. After enough data accumulates, decide which of the three duplicate GHL picklist fields is
   authoritative and consider consolidating (or confirm with Kes they're each genuinely used by
   different forms and should stay separate) — now a secondary check behind GHL tags.

## Session: 2026-08-25 (cont'd) — dashboard card, GHL-tags routing signal, do_not_call gate

### Dashboard card

Added a "Staff Call Quality" tile (dashboard home page, Analytics group) linking to a new
`/staff-call-quality` page: 5 stat tiles (scanned/connected/analyzed/flagged/avg-score-by-type) +
a recent-calls table (time, rep, type, score, summary, flag). Backend: `GET
/dashboard/staff-call-quality` → `get_staff_call_quality_summary()` in `dashboard_metrics.py`.
Shows an explicit empty-state banner (not just a blank table) explaining the scan is off by
default, since the card is visible immediately on deploy but has nothing to show until
`STAFF_CALL_QUALITY_SCAN_ENABLED` is turned on. Testing this against a real SQLite DB (not just
mocks) caught a real bug: `call_time` came back as a plain string via raw SQL on SQLite, not a
datetime object, crashing `.isoformat()` — fixed with the same defensive
`hasattr(x, "isoformat")` pattern already used elsewhere in this file. Likely SQLite-only (Postgres
returns real datetime objects for timestamp columns even via raw SQL), but worth having caught
either way. 4 new backend tests (real SQLite data) + frontend `npm run build` verified clean
(typecheck passes; `npm run lint` itself is broken pre-existing/unrelated — `next lint` errors on
its own invocation).

### GHL tags as the primary classification signal

Kes reviewed three real GHL contacts and pointed out their `tags` array (a different field from
the customFields picklists discussed earlier) — populated on all three samples, where the
picklist fields were empty on every contact checked. Kes's rule, added as the *first* check in the
priority chain (ahead of the picklist fields): any tag containing "student" or "enrolled" → student;
tags present but none matching → lead (Colaberry explicitly tags the enrolled-student exception,
not every lead) — but an empty tag list is treated differently and falls through rather than
defaulting to "sales" on zero information. `app/core/call_classification.py` updated
(`extract_classification_signals()` now pulls `contact["tags"]`; `classify_from_known_signals()`
checks it first); `staff_call_quality` gained a `ghl_tags` column (audit trail, same pattern as the
picklist-field snapshots) — folded directly into migration `0021` since it hadn't been applied to
production yet, rather than adding a churny follow-up migration. 8 new tests in
`test_call_classification.py`, 1 new job-level test confirming tags win over a contradicting
picklist value.

### `lead_state.do_not_call` gate

Separate, smaller ask folded in alongside the above: close the previously-known, separately-tracked
gap where `launch_outbound_call_job` never checked `lead_state.do_not_call` before dialing (open
since the 2026-07-15 session). Implemented with the exact same belt-and-suspenders pattern as the
urgent-escalation guard from earlier this session (spec/22): a check in `enter_campaign()` (direct
attribute read — `lead` is already loaded) and a matching check in `launch_outbound_call_job()`
(new `_is_do_not_call()` helper, DB lookup by `contact_id` since the job only has payload data) —
both cancel + log an `outbound_suppressed_do_not_call` exception rather than silently dropping the
lead. Adding this guard exposed an ordering issue in 4 existing tests: with the new check running
before the escalation guard, `_is_do_not_call()`'s real implementation was hitting a `MagicMock`
session and returning a truthy mock instead of `False`, incorrectly short-circuiting those tests —
fixed by explicitly patching `_is_do_not_call` to `False` in every test that expects to proceed
past it. 4 new tests (2 campaigns, 2 outbound_jobs) plus the 4 fixed.

Full suite after all of the above: 1145 passed, same 11 pre-existing unrelated failures.

### Merged + deployed — 2026-08-25

Kes confirmed the debrief, merged `feat/call-quality-analysis` → `feat/ghl-call-conversation-sync`
(fast-forward, `764b4ee..6da5480`), pushed, and redeployed Hetzner. `migrate` applied `0020 -> 0021`
(new `staff_call_quality` table) cleanly; all 11 services `Up`; frontend build includes
`/staff-call-quality`. Live-verified `GET /dashboard/staff-call-quality` against production —
returns the expected zeroed state (`total_scanned: 0`, etc.) since `STAFF_CALL_QUALITY_SCAN_ENABLED`
is still `false`. Everything from this session (spec/22's escalation guard, spec/23's call-quality
pipeline + dashboard card + GHL-tags routing + the do_not_call gate) is now live in production.

## Session: 2026-08-25 — "spam likely" GHL tag guard on outbound dialing

Closes the "spam likely" part of finding #3 from the 2026-07-17 session (a contact GHL had tagged
do-not-contact/"spam likely" from a stale prior interaction got a full re-engagement pitch anyway,
because nothing at dial time reads GHL's own tags). Scoped down to just this one tag rather than
the full DNC-tag-ingestion project that finding also raised — that still needs its own spec.

Branch `feat/spam-likely-tag-guard` (cut from `feat/ghl-call-conversation-sync`). Added
`_has_spam_likely_tag(contact_id, settings)` to `app/worker/jobs/outbound_jobs.py`: live
`GHLClient.get_contact()` lookup, case-insensitive match against the contact's GHL `tags` for
`"spam likely"`. Wired into `launch_outbound_call_job` at the same belt-and-suspenders checkpoint
as the existing `do_not_call` guard — on a match, cancels the job and raises
`outbound_suppressed_spam_likely_tag` (warning). Short-circuits (no GHL call) when `contact_id` is
actually a bare phone number rather than a real GHL ID, and fails open (logs + allows the call) on
any GHL read error, matching the non-fatal GHL-read pattern used elsewhere in this codebase —
deliberately not fail-closed, since a GHL outage blocking all outbound dialing would be a worse
failure mode than occasionally missing this one tag.

7 new tests in `test_outbound_jobs.py` (guard fires/passes, case-insensitivity, no-match, phone
short-circuit, GHL-error fail-open). Full suite: 1177 passed, same 7 pre-existing unrelated
failures (confirmed via `git stash` — identical failures with this change removed).

Merged `feat/spam-likely-tag-guard` → `feat/ghl-call-conversation-sync` (fast-forward,
`e06a514..a5c3d25`), pushed, redeployed Hetzner via `scripts/deploy.sh`. No new migration in this
change. Both health checks (`API`, `Dashboard`) returned `ok` post-deploy; all 12 services `Up`.
Not yet live-verified against a real GHL contact carrying the tag (the manual test-call route only
ever passes a phone number as `contact_id`, which this guard short-circuits on) — would need a
real sandbox contact tagged `spam likely` run through the actual campaign-enrollment flow to
confirm end-to-end; flagged to Kes as an open gap, not yet done.

## Session: 2026-08-26 — Portfolio walkthrough video of the ops dashboard (no code changes)

**Not a feature-development session** — no branch cut, `git status` on
`feat/ghl-call-conversation-sync` confirmed clean before and after. Recorded here only because
it changed local-machine/session state that a future session might otherwise be confused by.

Kes asked for a walkthrough video of the app, following an external "Walkthrough Video Production
Skill" package (`C:\Users\keset\Downloads\Walkthrough Video Production Skill - Desktop Test`, not
part of this repo). Ran in **Mode 1 (Automated Production)** — Claude Code operated the real local
app via Playwright, generated narration via Kokoro TTS, and assembled the final video via ffmpeg,
all already installed/cached on this machine from prior use (no new installs needed except
Kokoro's ~120MB model weights, downloaded to `~/.cache/kokoro/` with Kes's approval).

**What got built, and where (all outside this repo — `C:\Users\keset\cora-walkthrough-output\`):**
- `final/cora_walkthrough_final.mp4` — 1:35, 1440×900, 5-scene tour (Operator Console → Sales
  Queue → Voice Performance → Staff Call Quality → System Controls) with narration and a
  synthetic cursor overlay (Playwright doesn't capture the real OS pointer).
- `storyboard.md`, `narration-script.md`, `transcript.md`, `revision-log.md` — full production
  paper trail, evidence-tier-tagged per claim (spec-only vs. actually-observed).

**Local dev stack stood up for this, using this repo's own `docker-compose.yml` unmodified:**
Postgres/Redis/PgBouncer/API/dashboard-api/worker-default/frontend brought up locally
(`DATABASE_URL=postgresql://...@localhost:5433/cora`, a fresh volume — no real data was ever in
it). Seeded with clearly-fake data only: 10 `demo-*` contact_id rows in `call_events`/`lead_state`,
6 in `staff_call_quality`, 3 `alert_events` tagged `[DEMO]` in their message text — all fictional
names/transcripts, safe to leave or wipe. **As of this session's end, this local stack was left
running and this seed data was left in place** — Kes had not yet said whether to tear it down when
the session wrapped; a future session finding unfamiliar `demo-*` rows in the local dev DB or
Docker containers up that nobody remembers starting should check here first before assuming
they're a bug.

**One real bug found and fixed for the recording, in a way that didn't touch the repo:** the
frontend's Next.js rewrite proxies to `DASHBOARD_API_URL`, which `.env` correctly sets to
`http://localhost:8001` for non-Docker local dev — but that's wrong for the frontend *container*
talking to a sibling `dashboard-api` container, which needs the Docker-internal hostname. Fixed by
passing `DASHBOARD_API_URL=http://dashboard-api:8001` as a shell-level env var into
`docker compose build frontend` (overrides the `.env` value for that one build only) rather than
editing `.env` or `docker-compose.yml`. This is the same class of issue already documented in
memory/architecture notes elsewhere ("Docker internal hostname vs. localhost") — if it recurs,
the fix is that build-arg override, not a `.env` change.

**Still open, needs Kes:**
- Final watch-through/approval of the assembled cut — not yet given as of this entry.
- Video duration (1:35) came in short of the "3-5 min" target Kes gave at intake; flagged rather
  than silently padded. Kes's call: extend, or accept as a tighter highlight reel.
- Minor, accepted issue: the System Controls scene briefly (~0.5s, during a silent pre-narration
  beat) flashes a real local-dev-only config warning (`GHL_FIELD_MARK_AS_LEAD` not set in this
  `.env`) before it's hidden — logged in the revision log, not blocking.
- Whether to tear down the local Docker stack / demo data described above.

---

## Session: 2026-08-26 — normalized_phone agent-line corruption fix (spec/24)

**Branch**: `fix/normalized-phone-agent-line-corruption` (cut from
`feat/ghl-call-conversation-sync`), merged and deployed same session.

### What happened

Kes brought a `blocked_dial_number` critical alert (contact `+13104186986`,
job `372fe32c…`) plus 4 `outbound_suppressed_urgent_escalation` warnings for
triage. The 4 warnings were confirmed working-as-designed (spec/22) and
safe to ignore. The critical alert led to a real bug: `lead_state.normalized_phone`
was corrupted to Synthflow's own agent line (`+16822812224`) on 35 prod rows.

Root cause: `ai_jobs.py` and `lifecycle_jobs.py` both derived
`normalized_phone` for new inbound-caller stubs with
`phone_number_from → phone_number_to → ...`, assuming `phone_number_from`
is the lead's number. Checked against ~28k real `call_events`: `phone_number_to`
matches `contact_id` ~78-80% of the time (both directions); `phone_number_from`
matches under 2% — it's almost always Synthflow's own line. An initial fix
attempt made this direction-aware (mirroring `conversation_context.py`'s
pattern) but prod data disproved that too — the correct fix is
direction-independent: always prefer `phone_number_to`. Full writeup:
`directives/spec/24_normalized_phone_agent_line_fix.md`.

**Fixed**: fallback order in both files; `campaigns.py::enter_campaign()`
now also falls back to `contact_id` as the dial target when `normalized_phone`
is empty and `contact_id` is phone-shaped, so a missing/corrupted
`normalized_phone` no longer means a lead silently never gets called. 4 new
regression tests. Full suite: 1159 passed, same 7 pre-existing failures as
baseline (confirmed via `git stash` diff). Merged to
`feat/ghl-call-conversation-sync` (33a0557), pushed, Hetzner redeployed and
confirmed on the new commit with all containers healthy.

**Backfill**: Kes ran the 35-row `normalized_phone` backfill on prod directly
(not via this session). Verified after: 35 → 1 remaining row with
`normalized_phone = '+16822812224'`.

### Follow-up, same session: `+16822812224` ghost-contact investigation + spec/25

**Investigation of the `+16822812224` lead_state row**: not a real lead.
`call_events` targeting it go back to 2026-04-22 (64 rows, several
`completed`) — long before its `lead_state` row existed (created
2026-08-07). `app/adapters/synthflow.py::launch_new_lead_call()` never sends
a `contact_id` to Synthflow, only `phone`; Synthflow's completion webhook has
no real identity to echo back, so the normalizer falls back to the dialed
number itself. Each of the pre-08-07 calls was almost certainly a different
real lead whose `normalized_phone` (or job payload) had already been
corrupted to the agent line by the same bug class spec/24 fixed — Cora just
has no way to trace which leads now, since the corruption erased that link
before the call was placed. The `lead_state` row itself was created on
2026-08-07 when an inbound call event where both `phone_number_from` and
`phone_number_to` were `+16822812224` (Synthflow calling its own line, likely
a self-test ping) resolved `contact_id` to the agent line via `ai_jobs.py`'s
new-inbound-caller stub path. Once it existed, it re-entered New
Lead/Cold Lead campaigns normally (Aug 13/18/24 outbound rows) — Cora dialing
its own agent line "as a lead." Kes labeled it not-a-contact directly rather
than a DB deletion.

**spec/25 — `conversation_context.py::_fetch_ghl_messages()` fix**: this
function had the exact bug the spec/24 investigation predicted but didn't
touch — direction-aware phone selection (`inbound` → `phone_number_from`,
else → `phone_number_to`), same disproven assumption. Fixed to match spec/24:
always prefer `phone_number_to`, fall back to `phone_number_from` only when
absent. One test rewritten (`test_fetch_inbound_uses_phone_from` →
`test_fetch_inbound_prefers_phone_to_over_agent_line`, asserting the
corrected behavior), one new fallback test added. Full suite: 1160 passed,
same 7 pre-existing baseline failures. Merged to `feat/ghl-call-conversation-sync`
(a7f6c94), pushed, Hetzner redeployed and confirmed on the new commit with
all containers healthy.

### Still open, needs Kes

- None from this session — both follow-up items (ghost contact, GHL
  conversation-context phone lookup) are resolved as of this entry.

---

## Session: 2026-08-26 (cont'd) — Shveta→Roselen GHL contact reassignment investigation (spec/26)

**No code changes — investigation + live read/write mechanism verification only.**

Kes asked about a manual GHL workflow Shveta used to run (reviewing a
view/pipeline and reassigning qualifying leads to Roselen as part of
maintaining the Cold Lead definition), which stopped when she was let go.
Confirmed nothing in this codebase represents that workflow — no
`assignedTo` (contact-owner) tracking anywhere; the only existing
`assignedTo` usage is unrelated CRM task assignment.

**Cold Lead definition, per Kes**: anyone who signs up via a form and
doesn't enroll in a class. Unchanged, still live in GHL (Kes's answer taken
as authoritative, not independently re-verified against GHL config).

**Verified live against GHL with the current Private Integration token**
(full writeup: `directives/spec/26_ghl_contact_owner_reassignment.md`):
- Read: `POST /contacts/search` filtering by `assignedTo` works under the
  existing `contacts.readonly` scope. Counts as of 2026-08-26: Shveta
  (`mW2OSEYWWGDSB9JcKBcr`, ID supplied by Kes) = **2,189 contacts** (mixed
  tags — not Cold-Lead-only); Roselen (`0swBv9tBNeXeYXPYFBSx`) = **695**.
- Write: `PUT /contacts/{id}` with a top-level `assignedTo` field works
  under the existing `contacts.write` scope — confirmed via a deliberate
  no-op (wrote a contact's owner back to its own current value, re-fetched
  to confirm unchanged). No new GHL scope needed for either direction.
- Gap found: `GET /users/` (would let Cora resolve staff GHL IDs itself) is
  **not available** — 401, missing `users.readonly` scope. Staff IDs must be
  supplied manually until that scope is granted.

**Not built**: the actual bulk reassignment script. Blocked on Kes's
decision — reassign all 2,189, or filter to Cold-Lead-tagged/staged only —
and whether this should also get an ongoing safeguard (alert if a new lead
lands on a departed staff member) beyond a one-time backfill.

---

## Session: 2026-08-27 — staff-call-quality discovery fix (spec/23 redesign)

**Branch**: `fix/staff-call-quality-discovery-window` (cut from
`feat/ghl-call-conversation-sync`), merged and deployed same session.

Kes reported: sales reps write a summary note in GHL right after a call
ends, which breaks the staff-call-quality scan's discovery — it uses
`search_conversations(last_message_type="TYPE_CALL")`, which filters on a
conversation's *most recent* message, so the note masks the call and it's
never picked up again.

**While verifying that fix, found a second, independent bug**: GHL's
`start_after_date` is a pagination cursor ("sort value of the last
document"), not a "since this time" filter, and this location's default
sort is descending by recency. The old code passed `now - 24h` with no
explicit sort direction, which — confirmed live against prod GHL with a
three-way comparison (5-min vs. 24h vs. 90-day windows, plus an explicit
ascending-sort test) — walks *backward* into a stale ~24-66-hour-old window,
never the actual last 24 hours. Never affected production —
`STAFF_CALL_QUALITY_SCAN_ENABLED` defaults to `false` and has never been
enabled — but would have broken the scan silently the moment it was turned
on, independent of the note-masking issue.

**Fixed**: `_discover_conversations()` (new function,
`app/worker/jobs/staff_call_quality_jobs.py`) drops the `last_message_type`
filter, explicitly requests `sort_by="last_message_date", sort="asc"`, and
pages forward from the lookback cursor (deduping GHL's inclusive cursor
boundary) until a short page or a `_MAX_DISCOVERY_PAGES=20` safety cap.
`GHLClient.search_conversations()` gained `sort_by`/`sort` params. 9 new
unit tests. Full suite: 1168 passed, same 7 pre-existing baseline failures.
Full writeup: `directives/spec/23_staff_call_quality_analysis.md` →
"Discovery redesign" section. Merged to `feat/ghl-call-conversation-sync`
(c6be755), pushed, Hetzner redeployed and confirmed on the new commit with
all containers healthy.

Feature remains off by default (`STAFF_CALL_QUALITY_SCAN_ENABLED=false`) —
this is pre-launch hardening, not a live-production fix.

---

## Session: 2026-09-08 — outbound calling has been 100% down for 12 days (undocumented Aug 27 GHL cutover + webhook-secret mismatch)

**Branch**: `feat/outbound-stall-alerting` (cut from `feat/ghl-call-conversation-sync`).
**Investigation + observability code only — the actual outage fix is a GHL-console change, not done in this session.**

### What Kes reported

No outbound calls (New Lead, Cold Lead, or voicemail-tier follow-ups) for "the last week."
Inbound seemed unaffected. Asked whether GHL is triggering calls at all or there's a silent
failure.

### Root cause — confirmed against production

The GHL->Cora cutover from spec/21 — which spec/21's own status table and the 2026-07 / 2026-08
PROGRESS sections all record as **"NOT STARTED / explicitly deferred"** — **was actually
performed on 2026-08-27** (~18:22 `.env` edit, `api` container recreated 19:34 UTC), by whom is
unknown (GHL-console + `.env` change, no commit). Both GHL workflow actions ("Cora Outbound -
New Leads" and "Cora Outbound - Cold Leads") were repointed from Synthflow's Make Call webhook to
`https://204-168-245-238.sslip.io/v1/webhooks/leads/{new_lead,cold_lead}`, and the old
direct-to-Synthflow trigger was removed rather than kept as the parallel fallback spec/21
Out-of-scope required.

**The `X-Cora-Webhook-Secret` header on the GHL side has never matched the server's
`CORA_INBOUND_WEBHOOK_SECRET`.** Every request has been rejected 401:

- `api` logs, full retained history: first `intake_lead: rejected` at 2026-08-27T20:00:01Z,
  continuous through the investigation. Count: **~1998 cold_lead + 24 new_lead rejections, zero
  `intake_lead: accepted` ever.** (24 vs 1998 ~= 1.2%, consistent with spec/21's "New Lead <2% of
  volume" — both workflows were cut over, both fail auth.)
- Source IP on every rejected request is the `caddy` container — these are real external GHL
  requests through the reverse proxy, not internal noise. `Caddyfile` is a bare
  `reverse_proxy api:8000`, which passes custom headers through untouched — Caddy is not
  stripping the secret.
- Server secret is fine: `get_settings().cora_inbound_webhook_secret` and all **three**
  duplicate `.env` lines (15, 273, 274 — same value) hash identically; container has it loaded.
  No container/env drift. The mismatch is purely the value configured in GHL's workflow actions.

### Timeline of the collapse (production data)

| Date | outbound `call_events` | Note |
|---|---|---|
| Aug 11-24 | 7-36/day steady | Healthy (Aug 24 = 36, Cold Lead go-live) |
| Aug 27 | 5 | Cutover deployed 19:34; first 401 20:00 |
| Aug 28 / 29 | 3 / 1 | Pipeline draining |
| Aug 30 -> Sep 8 | **0** | Total outbound outage |

- Last `launch_outbound_call` job **created**: Aug 27 16:36. Last `process_voicemail_tier`:
  Aug 29 16:48. Last call-derived downstream job (`process_call_event`, `run_call_analysis`,
  `write_conversation_log`, `create_crm_task`, `send_student_summary`): **Sep 1 20:20**, then
  silence.
- **Why VM-tier / nurture calls also stopped** (Kes's specific puzzle): they are entirely
  downstream of the first-touch call. No GHL trigger -> no `launch_outbound_call` -> no completion
  webhook -> no `process_call_event` -> no voicemail-tier retry scheduled, and zero leads ever
  entered `status='nurture'` after the cutover (nurture leads are created only from call
  outcomes). `run_nurture_scheduler` fires every ~90s and correctly finds nothing to graduate.
- `auto_webhook_recovery` has run 4,273 times finding nothing to recover -> Synthflow genuinely
  is not placing calls. This is a **trigger** failure, not a webhook-delivery failure.
- Workers are healthy (all 13 compose services `Up`, zero restarts, zero OOM, `migrate` exited
  0). The Docker cleanup Kes did earlier is not implicated — build cache pruned (harmless),
  ~14 idle volumes (~3.7GB reclaimable, leftovers — **do not prune without checking**, one may
  be an old DB volume). Worker container is named `...-worker-default-2` (no `-1`) — cosmetic,
  was scaled to 2 once and one removed; the single one processes fine.
- **Inbound**: also tapered to zero after Sep 1 in `call_events`. Inbound does not use the
  intake endpoint (Synthflow -> `/v1/webhooks/calls`, which is confirmed up: `405` on GET, `202`
  on POST over valid HTTPS). Not chased further this session — flag: if Synthflow shows inbound
  calls last week that aren't in `call_events`, that's a separate webhook-URL problem on the
  Inbound workflow.

### Why nothing alerted (the gap behind the outage)

The 401 is rejected at the FastAPI auth layer in `intake_lead` — **before** any
`create_exception()`, job, or DB write. It produced no `exceptions` row, no `alert_events` row,
no `scheduled_jobs` row. Every one of the 6 alert types in `alerting.py` is structurally blind:
`queue_lag`/`worker_offline` (nothing congested/down), `error_rate_spike`/`exception_spike`
(no jobs ran, no exceptions), `ghl_auth_failure` (that's *Cora->GHL API* auth, opposite
direction), `webhook_drop_detected` (needs >=5 completed `launch_outbound_call` jobs in a bucket —
there were 0). **Nothing anywhere asked "did we place any outbound calls today?"** That is the
real bug behind the 12-day blackout.

### What was built this session (branch `feat/outbound-stall-alerting` — NOT merged, NOT deployed)

Two new alert types + the plumbing to feed them:

1. **`intake_auth_failure`** (critical). `call_intake.py` now writes a single deduplicated
   `intake_auth_failed` exception on any 401 (reason recorded: `server_secret_not_configured` vs
   `missing_or_invalid_header`) — best-effort, swallows its own errors, one open row max so a
   401-storm can't flood the table via this public endpoint.
   `alerting.py::_evaluate_intake_auth_failure` fires while any such open exception exists,
   resolves when cleared. Message names the exact fix (X-Cora-Webhook-Secret vs
   CORA_INBOUND_WEBHOOK_SECRET).
2. **`outbound_calls_stalled`** (critical). `alerting.py::_evaluate_outbound_stall`: fires when
   `launch_outbound_call` completions in the last `ALERT_OUTBOUND_STALL_HOURS` (new setting,
   default **4**) == 0, **and** `get_mode_flags` shows not `system_paused` / not
   `outbound_campaigns_paused`, **and** at least one of New/Cold Lead is inside its active
   window (`is_campaign_active`). Fails toward alerting if the window check errors. One page per
   incident (onset -> resolve), not one per 60s cycle — added `_upsert_active_alert` /
   `_resolve_active_alert` helpers for that (the generic `_evaluate_single_alert` re-pages every
   dedup window; deliberate divergence).
3. `.env.example` + `Settings.alert_outbound_stall_hours` documented. No migration (both
   `alert_events.alert_type` and `exceptions.type` are free strings). No frontend change (alert
   list renders `alert_type`/`message` generically — verified no hardcoded allowlist).

Tests: `tests/unit/test_dashboard_v2.py` (+3 intake-auth, +6 outbound-stall covering
fires/paused/outside-window/recent-completion/stale-completion/resolves) and
`tests/unit/test_call_intake.py` (+1 dedup). Full unit suite: **1186 passed, 7 failed** — the
7 are the known pre-existing baseline failures (`claim.py:270` MagicMock, `ghl_adapter` task
payload, `inbound_call_processing` crm task, `admin_routes` redis, `enrolled_intent`),
confirmed identical via `git stash`. Zero regressions. `ruff check` clean on all changed files.

### Next steps, in order

1. **Restore outbound calling — Kes, GHL console (this repo can't do it).** Either:
   - **(A, fastest)** repoint both GHL workflow actions' URL back to Synthflow's Make Call
     webhook (pre-Aug-27 config) — calls resume immediately, Cora endpoint stops mattering; or
   - **(B)** set `X-Cora-Webhook-Secret` on both GHL actions to the server's
     `CORA_INBOUND_WEBHOOK_SECRET` (retrieve from `/opt/cora-recap-engine/.env`), then send one
     test enrollment and watch it go 202 -> `launch_outbound_call` row -> real dial. This path
     has **zero** successful runs in its history, so also verify GHL's `phone` value is E.164
     (`_looks_like_e164` 400s otherwise) and the payload keys match.
   Campaigns are live and unpaused (`shadow_mode_enabled=false`, all pause flags off) — first
   success = a real call.
2. Dedupe the 3 `CORA_INBOUND_WEBHOOK_SECRET` lines in the server `.env` -> 1.
3. Kes reviews this branch's debrief, then merge -> `feat/ghl-call-conversation-sync`, deploy
   Hetzner (`git pull && docker compose up -d --build`; no migration). Confirm
   `outbound_calls_stalled` is *active* immediately post-deploy (it should be — still 0 calls),
   and that it resolves once step 1 lands.
4. Correct spec/21's status table — the cutover is no longer "NOT STARTED".
5. Decide whether the `intake_lead` endpoint should also carry the direct-to-Synthflow fallback
   inline (spec/21 wanted the fallback kept; it was removed) so a future auth/endpoint problem
   degrades instead of going dark.
6. Separately check the Inbound Synthflow workflow's completion-webhook URL if inbound volume
   in `call_events` doesn't recover.

### RESOLVED same session — both breakages fixed, verified end-to-end

Turned out to be **two** independent silent failures, not one:

1. **GHL trigger auth (Aug 27)** — Kes had the `X-Cora-Webhook-Secret` in GHL's **Custom Data**
   (request body) instead of **Headers**. Moved it to the Headers section on both the New Leads
   and Cold Leads webhook actions, republished. `POST /v1/webhooks/leads/new_lead` → **202**.
2. **Synthflow post-call webhook (Sep 1)** — separate breakage found only after fixing #1.
   Synthflow kept placing calls but stopped POSTing completion data to `/v1/webhooks/calls`
   (last one ever: 2026-09-01 20:20:38). Not a network/URL/Cora problem — verified `:8000/health`
   and the sslip.io HTTPS endpoint both reachable externally; the correct URL
   (`http://204.168.245.238:8000/v1/webhooks/calls`) was still in Synthflow's HTTP step. The
   step/workflow itself had stopped firing. Kes restored it in Synthflow.

**Verified end-to-end** with a real test call (contact `+15714782790`, Kes's own "test"-tagged
contact): GHL 202 → `launch_outbound_call` (completed) → Synthflow dialed → call connected →
Synthflow completion webhook from `49.13.34.137` → `POST /v1/webhooks/calls` **202** →
`process_call_event` (completed) → `call_events` row (`call_id 35b19db9…`, outbound, completed) →
all downstream jobs completed: `run_call_analysis`, `create_crm_task`, `write_conversation_log`,
`write_internal_comment_note`, `send_student_summary`, `update_lead_state`. Zero exceptions.

**The full outbound pipeline is live again as of 2026-09-09 ~01:46 UTC.**

### Still open

- **`feat/outbound-stall-alerting` branch is still uncommitted / not merged / not deployed.** It
  is exactly what would have caught both of these on day one. Debrief was delivered; awaiting
  Kes's "looks good" to merge → `feat/ghl-call-conversation-sync` + deploy.
- Dedupe the 3 `CORA_INBOUND_WEBHOOK_SECRET` lines in the server `.env` → 1.
- Confirm **real** GHL enrollments (not just Test Workflow) start flowing through — watch
  `scheduled_jobs`/`call_events` over the next day.
- Minor: the test contact has timezone `US/Central` (not a valid IANA name) — Cora logs a
  warning and falls back to `America/Chicago`. Harmless but worth cleaning in GHL.
- spec/21's status table still says the cutover is "NOT STARTED" — correct it.

### Merged + deployed — 2026-09-09

`feat/outbound-stall-alerting` merged to `feat/ghl-call-conversation-sync` (fast-forward,
`c44e6ef..da1921c`), pushed. Hetzner redeployed via `scripts/deploy.sh` — all 12 services up,
API + Dashboard health checks `ok`, postgres recreated cleanly (no migration this release).
Verified post-deploy: `alert_outbound_stall_hours=4` loaded, both new evaluator functions
import, webhook secret unchanged through the container recreate (all 3 `.env` lines identical),
zero active alerts, zero open `intake_auth_failed` exceptions. Alerting is now live in
production. Email sent to Ali summarizing the routing change + outage.

Remaining: dedupe the 3 `CORA_INBOUND_WEBHOOK_SECRET` `.env` lines; watch real GHL enrollment
volume over the next day; fix the test contact's `US/Central` timezone in GHL; correct spec/21's
status table.

### Follow-up — 2026-09-09: do_not_call suppression is now a log line, not an exception

After the cutover went live, GHL's Cold Lead campaign fired a batch of ~36 enrollments through
Cora (all 202, ~28 calls placed with a normal voicemail-heavy outcome mix, 20 more paced
forward — the pipeline is healthy). Two of those hit the `do_not_call` guard: contacts
`+15714269510` and `+14789603960`, both bulk-closed + flagged `do_not_call` on 2026-06-09
(exhausted cold leads, all-voicemail history, not opt-outs). GHL has no way to know Cora flagged
them, so it re-enrolls them — ~172 such flagged Cold Lead contacts exist, so this would have
produced a steady trickle of dashboard warnings.

Fix (`a3824ee`, branch `fix/do-not-call-suppression-noise`, merged + deployed): `enter_campaign()`
and `launch_outbound_call_job()` no longer `create_exception` on a `do_not_call` suppression —
they log it (`info` / `warning` respectively) and skip, as before. Behavior unchanged (still never
dialed). The urgent-escalation and spam-likely guards are deliberately left as exceptions. Test
renamed/updated in `test_outbound_jobs.py`; full suite 1186 passed / 7 pre-existing failures.

**Manual cleanup for Kes:** the 2 pre-existing open `outbound_suppressed_do_not_call` exceptions
(`b9271f82…`, `d2666ad8…`) won't self-clear — Ignore/Resolve them in the dashboard.

**Bigger fix, GHL-side (not done):** exclude already-worked / exhausted contacts from the Cold
Lead workflow trigger so GHL stops re-triggering dead leads at all.

### Follow-up — 2026-09-09: enrolled students being cold-called (spec/27, branch `feat/enrolled-student-guard`)

**Trigger:** Kes flagged that `+18179402651` (Megan, enrolled data-analytics student) was in the
Cold Lead campaign. She was *not* dialed — the spec/22 urgent-escalation guard happened to block
her — but that was luck, not policy.

**Audit (server-side, GHL lookups over the day's batch):** of 176 contacts GHL enrolled into an
outbound campaign on 2026-09-09, **7 classified as students.** Actually dialed:
`+12067427323` (enrolled student / `customer`, 2 calls → VM), `+13132885957` (ipbc student,
**connected — got the cold pitch**), `+19198026833` (ipbc student, 3 calls → VM), `+13477346630`
+ `+12522595783` (dropped-out students, ~2 calls each). Root cause is **GHL-side**: the Cold/New
Lead workflows trigger on the stale `AI Campaign = Yes` / `AI Campaign Name` custom fields, which
are never cleared when a lead converts to a student, so GHL keeps re-enrolling current students.

**Cancelled (pending forward jobs):** 5 `launch_outbound_call` jobs for 4 confirmed students —
`4b153e19…` (`+13477346630`, was due 09-09 22:32 UTC), `14ba5617…` + `a188f71b…`
(`+19198026833`), `8c16c027…` (`+12522595783`), `7f52b227…` (`+12067427323`). `+19409779004`
("registered - not enrolled") was correctly *excluded* by the negation carve-out.

**Cora-side backstop shipped (spec/27):**
- `app/core/student_guard.py` (new) — `check_is_student(phone, settings)`. Resolves the GHL
  contact by phone, reuses spec/23's `call_classification` classifier (`"support"` ⇒ student),
  adds a `"not enrolled"` negation carve-out. **Fails open** on any GHL error.
- `enter_campaign()` gate — after the urgent-escalation guard; skips entry, logs an `info` line
  with `reason=` + `matched_tags=`. Skipped entirely when `settings is None`.
- `launch_outbound_call_job()` gate — belt-and-suspenders; cancels the job, logs a `warning` line.
- **No dashboard exception / alert.** Per Kes: until the GHL-side fix lands GHL re-enrolls every
  student on each workflow re-eval, so a suppression is expected list churn — nothing to action.
  Log line only (same as the `do_not_call` guard, a3824ee). Server log is the audit trail.
- Tests: `test_student_guard.py` (10 new) + 4 in `test_campaigns.py` + 2 in `test_outbound_jobs.py`.
  Full unit suite: 1224 passed / 7 pre-existing failures (confirmed unchanged via `git stash`).
- `directives/spec/27_enrolled_student_guard.md` (new) — full spec per the Five Primitives.

**GHL-side fixes (Ali — draft email sent to review, thread "Outbound voice — routing change"):**
(1) clear `AI Campaign` / `AI Campaign Name` on enrollment; (2) add a student-tag / won-opp
exclusion to the Cold + New Lead workflow triggers; (3) decide deliberately whether dropped-out
students get a separate win-back campaign (the Cora guard blocks them for now).

**Known limitation:** the guard adds 2 live GHL GETs per campaign entry (search + get_contact).
Fine at ~30 calls/day; revisit under spec/18 if volume grows. Suppressions are visible only in
the server log (`grep "is a student"`), by design — no dashboard surface, so track the GHL-side
fix by log volume, not an exception count.

#### Merged + deployed — 2026-09-09

`feat/enrolled-student-guard` merged to `feat/ghl-call-conversation-sync` (fast-forward,
`7990472..87c18ca`), pushed. Hetzner redeployed via `scripts/deploy.sh` — API + Dashboard health
`ok`, all services up, no migration this release. Verified post-deploy: `student_guard` imports,
`campaigns` + `outbound_jobs` import clean.

**Queued-job cleanup (done by Kes, 22:12 UTC):** `acs.py --apply` re-scanned (5 student contacts,
6 pending jobs by then) and **cancelled 5**. The 6th (`+13477346630`, dropped-out student) had
fired ~20s earlier at 22:12:30 — still on the old pre-guard code (new containers started
22:14:54) — and placed one more voicemail call (`d936c646`). Not a regression: pre-guard, same
class as the day's other student calls. Post-restart the only student-phone job left pending is
`+19409779004` ("registered - not enrolled"), correctly **allowed** by the negation carve-out.

**Ali email:** drafted (reply on "Outbound voice — routing change" thread), not sent — covers the
two GHL-side asks (clear `AI Campaign`/`AI Campaign Name` on enrollment; add a student-tag /
won-opp exclusion to the Cold + New Lead workflow triggers) + the dropped-out-student decision.

---

## 2026-09-10 — Stuck-call recovery + outbound burst cap (spec/29)

**Trigger:** Kes brought two dashboard symptoms — a pile of `call_pending` warnings, and (from
the call-spacing question) evidence that outbound calls were firing far closer than the 75s grid
allows. Investigation on prod tied them together.

**Root cause (both):**
- **Stuck calls** — Synthflow sends a start-of-call webhook (`Status=in-progress`, duration 0,
  no transcript) and then sometimes never sends the completion callback. `process_call_event`
  can't route a non-terminal call → writes a `call_pending` warning and stops. The existing
  auto-recovery job ignored these because a `call_events` row *does* exist (`ce.call_id IS NULL`
  filter). `_create_call_event` also returned the stale row untouched on a dedupe hit, so even a
  forced replay wouldn't fix it. 19 stuck outbound calls on 2026-09-10 (the 13:41–15:58 UTC
  burst).
- **Burst over-dialing** — `_compute_window_run_at` (spec/21 75s grid) had **no concurrency
  protection**. Concurrent schedulers (a wave of GHL re-enrollment webhooks, each its own txn)
  each read the same free-bucket snapshot and wrote to it. Prod on 2026-09-10: two 5-min windows
  with **14 launch jobs each** vs a cap of 4; 66% of the day's 73 outbound calls started <75s
  after the previous, 10% within 5s. Same windows the 11 stuck calls landed in — flooding
  Synthflow is the likely reason it dropped their callbacks.

**Shipped (`feat/webhook-recovery-hardening` → `feat/ghl-call-conversation-sync`, `35c11fb`):**
- `_create_call_event` now **refreshes** a stale `in-progress`/`queue` row in place on a terminal
  replay (status, transcript, duration, end_call_reason, recording, timeline, telephony_*).
  Already-terminal rows still no-op.
- `_resolve_call_pending` closes the `call_pending` exception on any terminal outcome — natural
  late webhook *or* recovery.
- `_recover_stuck_calls` sweep appended to the 5-min `auto_webhook_recovery` cycle: outbound rows
  stuck ≥20 min → ask Synthflow for the real outcome, replay. `recover_missed_webhook` gains
  `route: bool`; raises `StaleLeadConflict` when Synthflow still reports non-terminal.
  `_finalize_unrecoverable_stuck_call` → `failed`/`recovery_no_record` past 24 h.
- `_compute_window_run_at` takes `pg_advisory_xact_lock` (Postgres only) to serialize bucket
  allocation. `_BUCKET_ALLOC_LOCK_KEY = 29090001`.
- `nurture_scheduler` commits per lead so the lock isn't held across a 50-lead batch's GHL calls.
- Tests: +12 unit. Full suite 1235 passed / 7 pre-existing failures.
- `directives/spec/29_stuck_call_recovery_and_burst_cap.md` (new).

**Deploy + verification (Hetzner, 2026-09-10 ~16:14 UTC):**
- `deploy.sh` clean — all services up, health `ok`, no migration.
- Recovery cycles at 16:18 / 16:26 / 16:31 swept the backlog: **18 `call_pending` exceptions
  resolved**, all 11 original stuck calls now carry their real terminal status + transcript
  (7 voicemail, 3 answered → `run_call_analysis` ran, 1 completed). 3 still open — all <20 min
  old / Synthflow still non-terminal, will clear on later cycles. Self-sustaining thereafter.
- **Burst cap holding:** launch jobs per 5-min window since deploy = mostly ≤4 (one window at 6),
  vs 14 at baseline. Consecutive-call gaps <70s dropped from 66% to ~6%.

**Deviation from the debrief:** I expected the recovered 11 to take the *cosmetic* path
(row + exception patched, no re-routing) on the assumption the leads had moved on. In fact most
had **no newer call_event** after the stuck one, so `moved_on` was False and they took **full
recovery** — routed forward (tier advance / analysis), ~2 h late. No pile-ups (5 pending launch
jobs across 11 contacts, properly laddered), no double-advances observed. Arguably the better
outcome (campaigns resume correctly) but not what the debrief said — the `moved_on` heuristic
(needs a newer *terminal* call_event) is stricter than "the campaign moved on".

**Not done:** true cross-transaction concurrency test for the advisory lock (no Postgres in the
unit suite — verified live instead). spec/28 (answering-service handling) still parked on
`feat/answering-service-handling`.

---

## 2026-09-11 — Urgent-escalation and spam-likely guards are now log-only, not exceptions

**Trigger:** Kes flagged an `outbound_suppressed_urgent_escalation` dashboard alert and said the
guard firing correctly (job cancelled, lead held) wasn't in question — just that it shouldn't
surface as a dashboard alert. Asked for an audit of every other `create_exception()` call site
for the same class of noise.

**Audit:** all 15 files calling `create_exception()` reviewed. Two clean buckets — every `*_failed`
type from an actual `except Exception` handler is a genuine, actionable failure and was left
alone. The guard/suppression-on-expected-skip category had 4 members: `do_not_call` and
`outbound_suppressed_student` were already fixed 2026-09-09 (see above). `outbound_suppressed_urgent_escalation`
and `outbound_suppressed_spam_likely_tag` were the two the 2026-09-09 session had *deliberately*
left as exceptions. Kes reversed that decision for both today.

**Shipped (`fix/escalation-guard-log-only` → `feat/ghl-call-conversation-sync`, `54f16eb`):**
- `enter_campaign()` and `launch_outbound_call_job()`'s urgent-escalation gates
  (`app/core/campaigns.py`, `app/worker/jobs/outbound_jobs.py`) — no longer `create_exception`;
  log `warning` and skip/cancel, as before. Behavior unchanged (still never dialed).
- `launch_outbound_call_job()`'s spam-likely-tag gate — same treatment.
- `directives/spec/22_urgent_escalation_guard.md` updated: the "must not silently drop" constraint
  now documents the log-only revision and why, since the original spec explicitly required an
  auditable exceptions row.
- Tests renamed/updated in `test_outbound_jobs.py` (`test_unresolved_escalation_cancels_job_without_raising_exception`,
  `test_spam_likely_tag_cancels_job_without_raising_exception`), matching the `do_not_call` test
  pattern. `test_campaigns.py` needed no changes. Full suite: 1240 passed / 7 pre-existing failures
  (confirmed unchanged via `git stash` before this change).

**Deployed:** Hetzner redeployed via `docker compose up -d --build` — all 13 services up, no
migration this release (`migrate` exited clean), API + Dashboard health both `ok`,
`app.worker.jobs.outbound_jobs` / `app.core.campaigns` import clean, zero errors in service logs
in the 3 min after restart.

**Cleanup check:** unlike the 2026-09-09 `do_not_call` fix (which left 2 pre-existing open
exceptions needing manual Resolve/Ignore), zero open `outbound_suppressed_urgent_escalation` /
`outbound_suppressed_spam_likely_tag` / `outbound_suppressed_do_not_call` / `outbound_suppressed_student`
rows existed at deploy time — no manual dashboard cleanup needed this time.

All 4 outbound-suppression guard types (`do_not_call`, `enrolled-student`, `urgent-escalation`,
`spam-likely`) are now consistently log-only. Server log is the audit trail for all four; none
surface a dashboard alert.

---

## 2026-09-11 (continued) — New-exception + sales-queue-urgent email alerts (spec/30), SMTP credential incident, prompt URL swap

### Email alerts (spec/30) — shipped in 4 small deploys, same session
Kes asked to be emailed on every new exception (not just the existing `exception_spike`
threshold-of-10 alert) and to have Rose/Taiwo emailed when a lead hits urgent sales-queue
priority, routed by topic (admissions → Rose, payment/IPBC → Taiwo) or by name if the caller
asked for someone specifically. Full design in `directives/spec/30_sales_queue_and_exception_email_alerts.md`.

Built on top of the existing live `app/services/alerting.py` SMTP cycle (60s, via
`metrics_jobs.py`) rather than new infra. Also fixed a latent bug found along the way:
`ALERT_EMAIL_TO` was documented as comma-separated but never actually split before
`smtplib.sendmail`, so a second address would silently never receive anything.

Shipped in order, each merged to `feat/ghl-call-conversation-sync` + deployed:
1. `feat/exception-and-sales-queue-email-alerts` (`79fcfcf`) — both new alert types, friendly
   Rose/Taiwo template with a `{frontend_url}/lead/{contact_id}` dashboard link,
   `ALERT_SALES_QUEUE_ENABLED` gate.
2. `fix/sales-queue-email-drop-dashboard-link` (`5970051`) — see incident below.
3. `feat/sales-queue-email-cc-kes` (`08d20e2`) — CC'd Kes on the Rose/Taiwo email.
4. `fix/sales-queue-email-no-cc` (`a0d4ed0`) — Kes reverted the CC minutes later. Mechanism
   (`cc_addrs` param on `_smtp_send`/`_send_sales_queue_urgent_email`) kept, just unused.

`tests/unit/test_alerting.py` (new file, 27 cases) — this module had zero prior coverage.
Full suite stayed at 1267 passed / 7 pre-existing-unrelated failures through all 4 deploys.

### Incident — first live send had a dead link
Prod's `FRONTEND_URL` env var was stale (`http://localhost:3000`, never corrected because
nothing read it before this feature). Before it was caught, the metrics cycle fired once
(16:26:02 UTC) for a genuinely-qualifying lead (`+15082722326`) and sent Rose/Taiwo a real
email with a dead link. Kes's call: drop the dashboard link entirely — Rose/Taiwo check GHL
directly by phone number, not the Cora dashboard. Template now ends with a GHL-lookup
instruction instead. **The stale alert_events row for that lead is still active** (dedup means
it won't resend on its own) — a one-row `UPDATE ... SET status='resolved'` would let it
re-fire correctly on the next cycle, but that write was blocked by the auto-mode safety
classifier and Kes hasn't yet said go/no-go. Still open.

### Incident — SMTP auth was broken, unrelated to any of the above
While verifying the `ALERT_EMAIL_FROM` change (see below), a live test send failed
`535 5.7.8 BadCredentials` — confirmed unrelated to the From-address change by reproducing the
same failure with the original From address. The Gmail App Password itself was being
rejected. **Fixed same session:** Kes generated a fresh app password for `kes@colaberry.com`
and set `SMTP_USERNAME`/`SMTP_PASSWORD` on the server directly (I never saw the password).
Verified with a real test send (`alerting: email sent for smtp_auth_verify` in logs, no error) —
auth confirmed working.

### Config corrections, same session
- `ALERT_EMAIL_FROM`: `asnakebekele2024@gmail.com` → `kes@colaberry.com` (Kes's request).
- `ALERT_EMAIL_TO`: `kesetebeirhan@gmail.com` → `kesetebirhan@gmail.com` — **this was a
  pre-existing typo**, not matching Kes's real address. Unclear how long alert emails (queue
  lag, exception spike, etc.) had been silently misdelivered before this session; worth a
  mental note that "alerting is live" claims from earlier sessions should be treated with
  suspicion until this fix.

### Prompt URL swap — training.colaberry.com → www.myfreeaiclass.com
`docs/update-training-url-to-myfreeaiclass` (`e6d0635`) — 15 occurrences across
`docs/colaberry-knowledge-base.md`, `docs/synthflow-cold-lead-prompt.md`,
`docs/synthflow-warm-lead-prompt.md`. Initially mischaracterized this as docs-only (no `app/`
code references the URL string) — Kes corrected that: `app/adapters/synthflow.py::_load_campaign_prompt`
(`@lru_cache`) reads `synthflow-cold-lead-prompt.md` / `synthflow-warm-lead-prompt.md` directly
off disk at call time and injects the result as the live Synthflow `prompt` payload — those two
files **are** the runtime prompt, not just reference docs. (`colaberry-knowledge-base.md`
genuinely is unreferenced by any code, for whatever that's worth as a distinction.)

Deployed (`docker compose up -d --build`, all workers rebuilt to clear the `lru_cache`) and
verified by calling `_load_campaign_prompt()` directly on the server for both campaigns: 0 old
references, 7 new in New Lead / 5 new in Cold Lead (matches source file counts exactly). Next
call placed on either campaign will use the corrected URL.

### Still open (at the time of writing — see follow-up section below for resolution)
- The stale `+15082722326` `alert_events` row (see incident above) — needs Kes's go-ahead for
  the one-row UPDATE.
- Whether any *other* `ALERT_EMAIL_TO`-style address elsewhere in `.env` has a similar typo was
  not audited — only this one was caught, because it happened to come up.

---

## 2026-09-11 (continued again) — sales_queue_urgent redesigned fire-once; routing bug fixed (caller's-words-only, default-to-Rose)

### Fire-once redesign (`fix/sales-queue-email-fire-once-no-resolution-tracking`, `f40f574`)
Kes: "the system should just send one time email and not check if it has been addressed or not
since there is a different mechanism to do that" (the Sales Queue itself). Removed the
active/resolved lifecycle entirely — `alert_events` now acts as a permanent fire-once tombstone
per contact_id (`status='resolved'` at creation, mirrors `exception_notified:{id}`'s pattern).

One nuance surfaced and fixed in the same pass: "don't keep checking after sending" and "don't
alert me about things already resolved" are two different moments, not a contradiction. Query
now filters `ls.sales_outcome IS NULL` at detection time (skip a lead a rep already resolved
before this cycle even runs) but never re-checks it afterward to gate a resend. Reconciles both
of Kes's asks without dropping either.

**Manual cleanup, same session:** stale `+15082722326` row required a `DELETE` (not `UPDATE`)
under the new any-row-blocks-forever dedup — old `UPDATE ... status='resolved'` plan wouldn't
have worked under the new design. Deleted, verified the corrected email resent automatically on
the next cycle (`email_sent_at` populated, routed to both Rose and Taiwo — routing bug below not
yet fixed at this point).

### Dashboard Alert-tile leak, found by Kes
A *different* lead (`+15716259625`, human_transfer_request) showed up on the dashboard's Alert
tile as an "Urgent sales-queue lead" notice — Kes: "I should not get these... alerts on the
Alert tile." Root cause: that row was created at 17:07 UTC, a few minutes *before* the fire-once
fix deployed (~17:53), so it still carried `status='active'` from the old design, which both the
`/dashboard/alerts?status=active` endpoint and `dashboard_metrics.active_alerts` count. New rows
(post-fix) are written `status='resolved'` from the start and don't have this problem. One-row
`UPDATE ... status='resolved'` on that specific leftover; confirmed `active_alerts` count back
to 0. No code change needed — a transition artifact, not a bug in the new design.

### Routing bug — real live send went to both Rose AND Taiwo (`fix/sales-queue-routing-caller-words-only`, `6633990`)
Kes caught this from an actual inbox screenshot: `+15082722326`'s corrected-template email
(from the fire-once resend above) still went to **both** Rose and Taiwo, reason logged as "the
call touched both admissions and payment/IPBC topics." Root cause, found by pulling the real
transcript: the caller never said anything about payment. The only "payment" mention was **Cora's
own scripted line** — *"no payment, no pressure,"* describing the free Explorer preview —
boilerplate present on nearly every call, both campaigns (see the prompt-URL-swap section above;
same two files). "Admissions" was likewise only in Cora's own line ("a follow-up with
Admissions"), not the caller's words. The keyword matcher had been scanning the whole transcript,
bot lines included.

**Fix:** new `_extract_caller_turns()` isolates `"human:"`-prefixed lines before any keyword/name
matching (falls back to the raw transcript if no such lines exist — fail open, not silent).
Also changed the no-signal fallback from "send to both" to **Rose only** — Kes confirmed routing
should be strictly either/or; Sales Queue leads are inherently admissions-track calls by
construction, Taiwo's domain is the narrower exception. Verified against the real
`+15082722326` transcript directly on the server post-deploy: now routes to Rose only, reason
"the topic wasn't clear from what the caller said, defaulting to admissions." The already-sent
both-recipient email can't be retracted; only affects sends after this deploy.

### Current state
`tests/unit/test_alerting.py`: 31 cases. Full suite: 1271 passed / 7 pre-existing unrelated
failures (confirmed unchanged via `git stash`, as in every prior redeploy this session).
`sales_queue_urgent` is live, fire-once, routes on caller's words only, defaults to Rose when
unclear. `new_exception` unaffected by any of this round's changes.

### Still open
- Whether any *other* `ALERT_EMAIL_TO`-style address elsewhere in `.env` has a similar typo was
  not audited.
- The transcript-classification approach (`_extract_caller_turns` + keyword lists) is a
  heuristic, not a real NLU classifier — worth revisiting if a future transcript still
  misroutes despite excluding Cora's own lines.

---

## 2026-09-11 (continued, yet again) — removed resolution emails from every alert type; Ali email drafted; Email Draft Rule added to global config

### Resolution emails removed (`fix/remove-alert-resolution-emails`, `f2335a9`)
Kes forwarded a real inbox example: a `webhook_drop_detected` email with `Status: resolved`,
"Alert webhook_drop_detected resolved." Not one of this session's two new alert types — this was
every pre-existing alert in `alerting.py` (`queue_lag_exceeded`, `error_rate_spike`,
`exception_spike`, `worker_offline`, `ghl_auth_failure`, `intake_auth_failure`,
`outbound_calls_stalled`, `webhook_drop_detected`) sending a second `[RESOLVED]` email when its
condition cleared, on top of the original active-alert email. Kes: doesn't want the follow-up —
the active alert already did its job.

Removed `is_resolution` from `_send_alert_email` entirely (dead parameter — always `False` after
this change) and the now-unused `settings`/`severity` params from `_resolve_active_alert`. `4`
call sites across `_evaluate_single_alert`, `_evaluate_ghl_auth_failure`,
`_resolve_active_alert`, and `_evaluate_webhook_drop` no longer send anything on resolution —
`alert_events.status` still flips to `'resolved'` in the DB, just silently. Found and fixed a
test in `test_dashboard_v2.py::TestAlertingService` I didn't know existed when spec/30 claimed
"no prior test coverage" — that claim was wrong; minor, not worth a special note beyond this one.

Deployed, verified healthy, no errors. Full suite: 1271 passed / 7 pre-existing unrelated
failures (unchanged).

### Ali email — drafted, not sent
Per Kes: covers the `training.colaberry.com` → `www.myfreeaiclass.com` swap across Cora's
knowledge base, both Synthflow outbound prompts (Cold Lead, Warm/New Lead), the Synthflow
inbound prompt, and the GHL knowledge base — the latter two updated by Kes directly outside this
repo/session, confirmed by him before the email was drafted. Also mentions the new alert system
briefly. Created as an actual Gmail draft (not just chat text) via `mcp__claude_ai_Gmail__create_draft`,
addressed to `Ali@colaberry.com` — sitting in Kes's Drafts folder, not sent.

### New global-config rule
Kes: "Update claude.md so that when I ask for an email draft, it means I need the draft already
created in my email." Added an **Email Draft Rule** to `C:\Users\keset\.claude\CLAUDE.md`
(personal global config, not this repo) — future "draft an email" requests create the actual
Gmail draft, not just chat text, asking for the recipient's address first if unknown.

---

## 2026-09-11 (continued, once more) — data-driven delay before emailing self-healing alerts; resolution email brought back, gated on having actually notified

### Real trigger
Kes forwarded a `webhook_drop_detected` "Status: resolved" email — even after resolution emails
were removed (see above), he'd apparently still gotten one shortly before that removal deployed.
His actual ask, once unpacked over a couple of messages: don't email at all for something that
clears itself quickly; only email if a problem *persists*. Then, once persistence-gating existed,
a follow-up concern — if something persists long enough to notify him, and *then* clears, he
wants to know it cleared (the fully-removed resolution email from earlier in the day went too
far). Confirmed via AskUserQuestion before building the second part.

### Part 1 — delay before emailing (`feat/delay-self-healing-alert-emails`, `acd8bcb`)
Queried real `alert_events` history (median `resolved_at - created_at` per type) before picking
any numbers — see the query and full table in this session's earlier notes / the spec doc.
Results: `webhook_drop_detected` 4.5 min (n=31), `error_rate_spike` 7.5 min (n=15),
`queue_lag_exceeded` 9.6 min (n=46, right-skewed), `exception_spike` ~9.5h (n=26).

New `_ALERT_EMAIL_DELAY_SECONDS`: 15 min for the first three, 5 min debounce for
`exception_spike`. New `_send_pending_delayed_alert_emails` sweep (runs every cycle,
independent of which evaluator created the row): an active row for a delay-eligible type is
created with `email_sent_at` left `NULL`; only gets emailed once it's been active past its
type's delay without resolving. Deliberately left immediate: `outbound_calls_stalled` (the
flatlined-pipeline backstop from the 12-day silent outage — delaying it defeats the point),
`worker_offline` (unambiguous and severe, zero historical samples to calibrate against anyway),
`ghl_auth_failure`/`intake_auth_failure` (auth doesn't self-heal).

### Part 2 — resolution email restored, gated on email_sent_at (`feat/resolution-email-only-if-notified`, `55bfe04`)
Restored `is_resolution` on `_send_alert_email` (had been fully removed a few commits earlier
the same day). Every resolve branch now checks the existing row's `email_sent_at` before
sending — `NOT NULL` (this alert did notify) → resolution email; `NULL` (self-healed before
ever crossing its delay) → resolve silently, same as before. `alert_events.status` always
transitions to `'resolved'` regardless; only the email send is conditional.

Found and fixed a latent correctness issue while touching `_evaluate_webhook_drop`: its
`existing` row lookup is a 2-hour lookback with **no status filter** (by design — see its own
comment about suppressing re-fire on the same historical bucket). Without an extra
`existing[2] == 'active'` guard, a row already resolved in a prior cycle would have re-sent a
resolution email on every subsequent cycle within that 2-hour window. Caught before deploy, not
in production.

### Verification
7 new tests in `test_dashboard_v2.py` (5 for the delay sweep, 2 for conditional resolution
email) + existing suites all still pass. Full suite: 1278 passed / 7 pre-existing unrelated
failures (unchanged). Deployed, healthy, no errors.

## Session: 2026-09-11 — training.colaberry.com vs myfreeaiclass.com by lead type (`fix/training-url-lead-type-routing`, `825267e`)

A prior session (same day) did a blanket find/replace of `training.colaberry.com` →
`www.myfreeaiclass.com` across `docs/colaberry-knowledge-base.md`,
`docs/synthflow-cold-lead-prompt.md`, and `docs/synthflow-warm-lead-prompt.md`. That was too
broad — Kes clarified the actual policy: **`training.colaberry.com` is the default site for most
cases; `www.myfreeaiclass.com` (free, no-commitment preview) is only correct for Cold Lead calls
or for a caller who is undecided/unsure/still exploring.**

Fixed by lead type:
- **Cold Lead prompt** (`synthflow-cold-lead-prompt.md`): no functional change — every mention
  was already `www.myfreeaiclass.com`, which is correct since Cold Lead calls are re-engaging a
  lapsed prospect and should always lead with the free, no-pressure preview. Added an explicit
  rule line under Additional Rules documenting this is intentional, not an oversight, so a future
  session doesn't "fix" it back to training.colaberry.com by mistake.
- **New Lead (warm) prompt** (`synthflow-warm-lead-prompt.md`): now defaults to
  `training.colaberry.com` (Website field, primary-goals bullet, end-of-call summary, additional
  rules) and only uses `www.myfreeaiclass.com` in the branches that were already conditioned on
  "lead isn't ready to commit" / "wants to explore first" — those branches were left as-is since
  they were already correctly scoped, just missing the training.colaberry.com counterpart for the
  "ready" branch.
- **Knowledge base** (`colaberry-knowledge-base.md`): not campaign-specific and not read by any
  runtime code (only `synthflow-cold-lead-prompt.md`/`synthflow-warm-lead-prompt.md` are loaded
  via `_load_campaign_prompt`) — kept as a human/GHL reference doc. Updated the "About Colaberry"
  and "How to Enroll" sections to state training.colaberry.com as the default with the cold-lead/
  undecided exception called out explicitly, since a manual GHL knowledge-base sync would
  otherwise carry the same wrong default forward.

**Deployed:** merged `fix/training-url-lead-type-routing` → `feat/ghl-call-conversation-sync`
(`825267e`), pushed, `git pull && docker compose up -d --build` on Hetzner (rebuild needed —
`_load_campaign_prompt` is `@lru_cache`d per-process, so only a fresh process picks up the file
change). Verified directly against the live loaded prompts post-deploy:
`_load_campaign_prompt('New Lead')` → 5 `training.colaberry.com` / 8 `myfreeaiclass.com`;
`_load_campaign_prompt('Cold Lead')` → 1 `training.colaberry.com` (the new documenting-rule
sentence only) / 6 `myfreeaiclass.com`. All 13 containers healthy, `/health` OK, no errors in
logs since restart.

### Follow-up same session — cold leads who convert mid-call (`3c31a33`)

Kes asked: on a Cold Lead call, if the caller decides they're ready to enroll, which site do
they get? The first pass above had made Cold Lead unconditionally `www.myfreeaiclass.com`
(including a rule explicitly forbidding training.colaberry.com on that campaign) — too rigid.
Confirmed via AskUserQuestion: a cold lead who converts mid-call should be handled the same as a
ready warm lead (training.colaberry.com + Admissions handoff), not forced through the free
preview funnel just because of campaign type.

Updated `synthflow-cold-lead-prompt.md`: Website field now notes the default/exception the same
way as the warm-lead prompt; Section 4 gained an explicit "If they say they're ready to enroll
now" branch (previously had only the free-start pitch, no ready-to-convert branch at all — a
real content gap, not just a URL issue); end-of-call summary and Additional Rules updated to
match. Also softened `colaberry-knowledge-base.md`'s About-Colaberry line, which had the same
"cold lead → always myfreeaiclass.com" absolutism.

Deployed (`git pull && docker compose up -d --build` on Hetzner). Verified post-deploy:
`_load_campaign_prompt('Cold Lead')` → 5 `training.colaberry.com` / 7 `myfreeaiclass.com`,
contains the new "ready to enroll now" branch text. All containers healthy, no new errors (one
`AbandonedJobError` in worker-quality logs is the routine artifact of restarting a worker
mid-job during deploy, unrelated to this change).

## Session: 2026-09-14/15 — InternalComment 401 triage, disguised-timeout retry fix, new_exception first-run alert bug

**Branch**: `feat/ghl-call-conversation-sync` (worked directly, no feature branch cut — small,
isolated fixes; not yet committed as of this entry)

### 1. Triaged a `internal_comment_note_failed` alert (job_id=4ffedaf0-8a98-405b-996e-f91d60e26d5a)

Root cause was **not** a bad `GHL_CONVERSATIONS_API_KEY` (confirmed non-empty in
`worker-callbacks`, no pattern of repeated 401s in 72h of logs). GHL's backend occasionally
reports its own internal command timeout as `HTTP 401 {"statusCode":401,"message":"Command timed
out"}` instead of a 5xx. `app/adapters/ghl_internal_comment.py`'s retry loop only treated
`{429,500,502,503,504}`/timeouts as retryable, so this one-off transient GHL error went straight
to `scheduled_jobs.status='failed'` with zero retries.

**Fix**: added `_is_disguised_timeout_401()` — retries a 401 only when the body exactly matches
that shape; any other 401 body (a real bad-token rejection) still fails immediately, no retry.
Two new tests in `test_ghl_internal_comment_adapter.py`. Manually requeued the stuck job
(reset `scheduled_jobs` row to `pending`, `run_at=now()`) — it completed successfully on retry,
confirming the transient diagnosis. The `exceptions` row (`4437ceb1-...`) this created is still
`status='open'` — nothing auto-resolves an exception when its underlying job later succeeds on a
requeue; left as-is (Kes would resolve/ignore it in the dashboard), not in scope of this fix.

### 2. Found and fixed why no email arrived for that exception

`_evaluate_new_exceptions` (spec/30, `app/services/alerting.py`) is supposed to email once per
newly-opened exception, any type/severity. It used a single `is_first_run` boolean (ledger of
`exception_notified:*` rows completely empty = first run = seed every currently-open exception
silently, no email, to avoid a backlog flood on first deploy). This was, in fact, the *first
time this evaluator had ever run against an open exception* since it shipped 2026-09-11
(`79fcfcf`) — confirmed via `alert_events`: exactly one `exception_notified:*` row exists in the
whole system, created at the moment this incident's exception was processed. So the InternalComment
failure — a brand-new, live exception — got misclassified as "pre-existing backlog" and silently
swallowed, purely due to bad luck of being the first exception the feature ever saw.

**Fix (per Kes: exceptions represent permanent failures — nothing in this codebase auto-retries
a failed scheduled_job or auto-resolves an exception, confirmed by reading
`app/worker/exceptions.py` — so there's no self-healing case to wait out here, unlike the
aggregate health-metric alerts which debounce via `_ALERT_EMAIL_DELAY_SECONDS` because those
genuinely can self-clear):** replaced the `is_first_run` flag with a per-exception age check —
`_NEW_EXCEPTION_BACKLOG_CUTOFF_SECONDS = 300`. An exception already older than 5 minutes the
first time it's seen is backlog (silent); anything newer emails immediately, regardless of
whether the ledger was empty a moment ago. This also closes a second latent flood risk the old
flag never covered: after any metrics-worker downtime, a pile of accumulated old exceptions
would previously all email at once on catch-up (ledger already non-empty by then) — age-based
backlog detection suppresses that too. Four tests in `test_alerting.py` cover: old exception on
first-ever sight (silent), fresh exception on first-ever sight (emails — the actual regression),
downtime-catchup backlog batch (silent), no open rows (no-op).

### Status as of this entry

Both fixes implemented and tested locally (`pytest tests/unit/test_ghl_internal_comment_adapter.py`
8/8 pass, `pytest tests/unit/test_alerting.py` 32/32 pass, full suite 1259 passed / 7 pre-existing
unrelated failures confirmed via `git stash` baseline diff). Committed `309bb47`, pushed, deployed
to Hetzner (`git pull && docker compose up -d --build`) — all 13 containers healthy, `/health` 200,
no errors in `worker-callbacks` logs post-restart.

### Follow-up same session — verified April New Lead VM tiers exhausted; found and fixed Cold Lead → "New Lead" mislabeling (`a54d163`)

Kes asked to verify the April 2026 New Lead cohort's voicemail tiers were fully exhausted (no
leads still stuck cycling through retries 5 months later). Confirmed: 35 April New Lead leads,
zero `process_voicemail_tier`/`launch_outbound_call` jobs in `pending`/`claimed`/`running` for
any of them — everything terminal. One already-known, already-resolved item surfaced (the
2026-07-16 Cold Lead webhook failure from the 2026-07-17 session, unrelated to April). No new
issues in the April cohort itself.

Follow-up ("any more new leads in general pending calls") surfaced a real, currently-live bug:
89 (later 104 by the time of the fix) pending `launch_outbound_call`/`send_sms`/`send_email` jobs
carried `payload_json.campaign_name = "New Lead"` while the lead's actual `lead_state.campaign_name`
was `"Cold Lead"` — 100% of the sample, including a lead created as Cold Lead *that same day*.
Two real consequences: (1) `outbound_jobs.py`'s slot-bumping logic treats `"new lead"` as
priority — these mislabeled Cold Lead jobs were getting undue scheduling priority; (2) the
Cold-Lead-only pause control checks the job's own payload `campaign_name` — pausing Cold Lead
specifically would **not** have stopped these particular calls (confirmed not currently biting;
`cold_lead_campaign_paused` was `false` at the time).

**Root cause**, found by tracing one contact's full call/job history: `_resolve_outbound_campaign()`
(the existing 2026-08-24 fix for Synthflow's unreliable self-reported `Agent`/`campaign_name` on
outbound calls — New Lead and Cold Lead share one physical Synthflow workflow, so Synthflow's
webhook always says "New Lead" regardless of which campaign actually placed the call) correctly
repairs `call_events.campaign_name`, but `app/worker/jobs/call_processing.py`'s three
`_route_to_voicemail`/`_route_to_call_through` call sites (process_call_event, lines ~494/499/548)
kept passing the raw, uncorrected `payload.get("campaign_name")` instead. That wrong value seeds
`process_voicemail_tier`'s payload, which `voicemail_jobs.py` prefers over `lead_state.campaign_name`
when scheduling the next retry — so the mislabel propagates through every subsequent voicemail-tier
retry and its SMS/email follow-ups.

**Fix:** all three call sites now use `call_event.campaign_name or payload.get("campaign_name")`
(falls back to the raw value only when `_resolve_outbound_campaign` found no matching launch
record — e.g. inbound calls, unaffected). Two new regression tests in `test_call_processing.py`.
Full suite: 1261 passed, same 7 pre-existing unrelated failures.

**Production backfill:** 104 already-scheduled `pending`/`claimed` jobs (92 → 104 by the time
Kes approved, as more got mis-scheduled by the still-live bug in between) had their
`payload_json.campaign_name` corrected from `"New Lead"` to `"Cold Lead"` via a direct
`jsonb_set` UPDATE, scoped to jobs where `lead_state.campaign_name = 'Cold Lead'` — verified
zero mismatches remain after.

Committed `a54d163`, pushed, deployed to Hetzner — all containers healthy, `/health` 200.

---

## Session: 2026-09-17 — `ghl_vm_message_update_failed` triage: disguised-401 gap in core GHL client, doomed-write guard, lossy retry-payload fix (`fix/ghl-disguised-401-contact-resolution`, `033f665`)

Kes brought alert `fc684f56-e7d3-4d01-8eb0-ae80daaafe9c`: `ghl_vm_message_update_failed` for
`lead:+14054632590`, `GHL HTTP error: 400 /contacts/+14054632590 | Contact with id
+14054632590 not found`.

**Root cause, found via SSH into Hetzner (`ssh root@204.168.245.238`,
`/opt/cora-recap-engine`) — DB rows + worker logs:**
`update_ghl_after_vm_message` (`crm_jobs.py`) resolves a phone-number `contact_id` to a real
GHL UUID via `search_contact_by_phone` before writing. Worker log showed the search hit GHL's
disguised-timeout 401 (`{"statusCode":401,"message":"Command timed out"}`) — the exact bug
already fixed once, 3 days earlier, in `GhlInternalCommentClient` (`309bb47`, 2026-09-14) — but
that fix only patched that one adapter class. `app/adapters/ghl.py`'s core `GHLClient` (used by
`search_contact_by_phone`, `update_contact_fields`, `get_contact`, etc.) still only retried
`{429,500,502,503,504}`, so the 401 was treated as permanent, the search "failure" was logged as
non-fatal and swallowed, `contact_id` was left as the raw phone number, and the code fell
through unconditionally to `PUT /contacts/+14054632590` → 400. Kes confirmed the GHL contact has
existed since 2024 — this was purely the transient-401-misclassified-as-permanent bug, not a
real not-found.

**Also found while tracing how the existing alert would get fixed**: `retry_now`/
`retry_with_delay` (`app/services/dashboard.py`) re-enqueue a job using the *exception's*
`context_json` as the new payload, but `update_ghl_after_vm_message`'s exception context only
ever stored `{contact_id, job_id, error, attempt_count}` — not `channel`/`message_body`/
`message_subject`/`campaign_name`. Those fields aren't re-derivable from anywhere else (the VM
follow-up text only exists in the original job's payload). Clicking "Retry Now" on this
exception type would have silently written **blank** Message / Support Ticket #2 fields to the
lead's real GHL contact instead of resending the actual follow-up content — a live data-loss bug
sitting behind a routine-looking dashboard button.

**Fix (branch `fix/ghl-disguised-401-contact-resolution`, commit `033f665`):**
1. `app/adapters/ghl.py`: ported `_is_disguised_timeout_401` retry handling into the core
   `GHLClient._request` (previously only in `ghl_internal_comment.py`).
2. `app/worker/jobs/crm_jobs.py`: `update_ghl_after_vm_message` now fails explicitly
   (`"Could not resolve GHL contact for phone ..."`) when phone resolution doesn't produce a
   real contact ID, instead of attempting the doomed write.
3. `app/worker/jobs/crm_jobs.py`: the exception context now carries the full retry payload
   (`channel`, `message_body`, `message_subject`, `campaign_name`) so future Retry Now/Delay
   clicks on this exception type reproduce the real write instead of blanking fields.
4. `directives/spec/dashboard/11_runbook.md`: added a dedicated `ghl_vm_message_update_failed`
   entry (previous one-liner wrongly said "GHL field ID mismatch").
5. New regression tests: `test_ghl_adapter.py` (disguised-401 retried, genuine 401 still fails
   fast), `test_crm_jobs.py` (unresolved-phone fails without writing; exception context carries
   full payload). Full suite: 1265 passed, same 7 pre-existing unrelated failures (confirmed via
   `git stash` diff against pre-change baseline).

**Deploy:** merged to `feat/ghl-call-conversation-sync`, pushed (`033f665`), Hetzner pulled +
`docker compose up -d --build` — all containers healthy, confirmed `_is_disguised_timeout_401`
present in the deployed `worker-callbacks` image, no startup errors.

**Existing alert closed out manually** (code fix only prevents *new* occurrences — nothing
auto-retries on deploy, and this exception's own `context_json` predates fix #3 above so the
dashboard's Retry Now button would still have blanked its fields): wrote a one-off script run
inside the `api` container that read the *original* failed job's (`b0254520-bda6-48b7-ace2-4c753b5c8bd6`)
still-intact `payload_json` and called `schedule_job()` directly with it — same mechanism the
dashboard retry buttons use, just fed the correct payload. New job
`ebc4b80b-ad60-4a10-996f-500454089bb3` ran within seconds, resolved the contact, wrote the
fields, completed clean. Then called `resolve_exception()` directly on `fc684f56...` to close it
out (exceptions don't auto-resolve when a later job for the same entity succeeds — only explicit
dashboard actions call `resolve_exception`). Temp scripts removed from the box after.

## Session: 2026-09-18/19 — AI cold lead tagging feature built, deployed, and a same-day production incident fixed (spec/31, `329bdc5`, `3e03b6d`)

**What was built:** a daily batch job that tags GHL contacts matching a
verified filter (`type=lead`, `dnd=false`, `phone wildcard "+1*"`, not
carrying one of 10 exclusion tags, `lastActivity` >30 days ago) as
`ai cold leads`, plus a dashboard tile (`/ai-cold-lead-tagging`) showing
backlog (live GHL count) and recent activity. Full spec and the live
filter-verification record (run read-only against production before any
adapter code was written): `directives/spec/31_ai_cold_lead_tagging.md`.
1,594 contacts currently match, as of 2026-09-18.

**Safety design:** dedicated `ai_cold_lead_tagging_enabled` ModeFlag
(default **False**, independent of the shared `ghl_write_mode` — which
was already `live` in production for other features going into this).
Checked in `run_tagging_cycle` before any `add_contact_tag` call. New
`tag_ai_cold_leads_runs` table logs one row per cycle (dry_run flag,
scanned/tagged/skipped/failed counts).

**Deploy incident (2026-09-18, ~30–40 min window):** `scripts/deploy.sh`
does not include the `migrate` service in its image-build list (existing
latent bug, not introduced this session) — so migration `0023` didn't
apply even though the new `api`/`dashboard-api`/`worker-*` code (which
queries the new `tag_ai_cold_leads_runs` table inside `get_card_metrics`)
was already live. Result: **`GET /dashboard/card-metrics` 500'd for every
nav-card indicator on the main dashboard**, not just the new tile, until
fixed. Separately, the daily job itself ran against the missing table,
and because the worker job's exception handler didn't call
`session.rollback()` before `fail_job`/`_reschedule`, the aborted
transaction cascaded and the job was left stuck in `running` status with
no next run ever scheduled (a `scheduled_jobs` row from this incident,
`e4fb3d92-...`, is still sitting inert in `running` — harmless, a fresh
job already runs independently, and it'll self-clear via
`recover_expired_claims()` around 2026-09-19 23:37 UTC if nobody bothers).

**Fix:** rebuilt+ran the `migrate` image manually to bring the DB to
`0023`; added `session.rollback()` to the job's exception handler
(commit `3e03b6d`) plus decoupled the claim lease (1h) from the daily
reschedule interval (24h) so a genuinely stuck claim self-heals in an
hour, not a day. Redeployed; confirmed clean: a "skipped" (flag-off) run
completed in 11ms, `card-metrics`/`tag-ai-cold-leads/runs` endpoints and
the new dashboard page all verified healthy from outside.

**Not yet done — next step:** a canary live run. Plan (already discussed
with Kes): temporarily set `ai_cold_lead_tagging_batch_cap=5`, flip
`ai_cold_lead_tagging_enabled=true` for exactly one cycle, verify those 5
specific contacts in GHL before/after, then raise the cap back up.
Nothing has been live-written to GHL yet — needs Kes's explicit go-ahead
before flipping that flag, given the incident above.

**Known follow-up, not fixed this session:** `scripts/deploy.sh`'s build
step (`docker compose build $NO_CACHE api dashboard-api frontend
worker-default worker-ai worker-callbacks worker-retries worker-quality`)
should include `migrate` too, or a future migration will silently
reproduce this exact incident.

## Session: 2026-09-19 — outbound_calls_stalled false-positive fix: post-wake grace period (`fix/outbound-stall-sleep-grace-period`, `d9932eb`)

### Trigger
Alert `dabea8f5-6022-467d-b0d2-296479c0cdff` (critical) fired at 17:35:21 UTC
("1 lead(s) overdue a call with zero completions in the last 4h") and
self-resolved at 17:36:51 UTC — 90 seconds later, on a healthy pipeline.
Kes: the 4h trigger window doesn't account for campaign sleep (overnight /
weekend) — reported false positive, asked for a proposal.

### Root cause
`_evaluate_outbound_stall` (`app/services/alerting.py`) checks "zero
`launch_outbound_call` completions in `ALERT_OUTBOUND_STALL_HOURS` (4h)"
against a plain wall-clock lookback, gated only by whether the campaign
window is active *right now* (`is_campaign_active`, a point-in-time check).
The instant a window reopens after being asleep, "zero completions in the
last 4h" is trivially true — no call could have completed while every
campaign was asleep — so a normal backlog that piles up overnight/weekend
looks identical to a real stall for the first few minutes after wake-up.

### Fix
Two pieces, `app/core/campaign_schedule.py` + `app/services/alerting.py`:
- New `current_window_start()` — returns when *today's* active window
  opened (or `None` if not currently active), unlike the existing
  `next_active_window_start()` which answers "when does the *next* window
  open."
- `_evaluate_outbound_stall` now computes the most-recently-woken active
  campaign's wake time and stays silent (resolves any active alert instead
  of firing) if that's less than `WINDOW_BUFFER_HOURS` (1h — the same
  constant already used for rescheduled-call placement, renamed from
  private `_WINDOW_BUFFER_HOURS` to public since it's now shared) ago.
- **Explicitly did not** clamp the 4h completions lookback itself to the
  wake time — a first pass tried that and it was backwards: shrinking the
  lookback window right after wake makes "zero completions" *more* likely
  to be true, not less. Only the grace gate was needed. Kes confirmed the
  fixed ~60–90 min buffer approach over tying the grace to the full 4h
  threshold (AskUserQuestion).

Tests: 6 new in `tests/unit/test_campaign_schedule.py` for
`current_window_start`; `tests/unit/test_dashboard_v2.py`'s existing
`TestOutboundStallAlert` tests patched to also mock `current_window_start`
(returning `None`, preserving prior behavior exactly — avoids wall-clock
flakiness) + 3 new tests for the grace period itself. Full suite:
1319 passed / 7 pre-existing unrelated failures (confirmed via `git
stash`), `ruff check` clean on all 4 changed files (confirmed pre-existing
findings elsewhere in `test_dashboard_v2.py` via the same stash check).

### Deployed
Merged `fix/outbound-stall-sleep-grace-period` → `feat/ghl-call-conversation-sync`
(fast-forward, no divergence, no PR needed per this repo's branching
model), pushed, redeployed Hetzner via `scripts/deploy.sh` — all 13
services up, both health checks `ok`, migration step ran clean (no new
migration in this change), confirmed the new code imports cleanly inside
the live `api` container.

### Not yet done
No `/directives` spec doc exists for this alert to update (its design
rationale lives in `alerting.py`'s docstrings, which were updated
in-place). If one should exist, needs a follow-up.

## Session: 2026-09-19 (continued) — same alert kept false-firing; sleep-aware threshold replaces the grace period (`fix/outbound-stall-sleep-aware-threshold`, `e4af0eb`)

### Trigger
Kes: "getting the alert almost every 1-5 minutes" — the grace-period fix
above didn't hold. Pulled live `alert_events` history: **15 fires in
~6.5 hours** the same Saturday, clustering to every 5-9 min by late
afternoon, each firing and self-resolving in 90-270s.

### Root cause (confirmed against live production data)
Two distinct bugs, both in `_evaluate_outbound_stall`'s backlog query
(`run_at <= now`, no minimum overdue duration):

1. **Sleep carryover, still broken.** The 1h grace period from the
   earlier fix only gated on how long the *campaign* had been awake, not
   on how long any given *lead* had actually been overdue. A lead due at
   9:58pm (before a 10pm window close) sits at 10h overdue by an 8am
   wake-up; the 1h grace clears by 9am, and that lead — still nowhere
   near having a fair chance — could immediately look like a real stall.

2. **New failure mode, unrelated to sleep timing.** Today is Saturday.
   Cold Lead's window is Mon-Fri only; New Lead runs every day. Traced a
   live example: a Cold Lead job for `+18643372797` came due at
   `19:26:15`, got cancelled and rescheduled to Monday `16:00 UTC` by
   `19:26:23` — an 8-second handoff. The alert's backlog query has no
   minimum overdue duration, so that 8-second window was enough for a
   60s metrics-cycle tick to occasionally catch it as "1 lead overdue,"
   combined with genuinely low weekend New-Lead completion volume (confirmed
   zero completions in a 2h+ stretch at the time), to fire the alert.
   45 Cold Lead jobs were queuing through this exact cancel+reschedule
   pattern that afternoon — one every ~75s (call-pacing stagger) — which
   is what produced the 5-9 minute cadence.

Kes caught a further gap before implementation: even a naive "require 4h
overdue" backlog filter would still misfire on the *first* bug, since raw
wall-clock overdue time doesn't stop accumulating during sleep — a lead
overdue 10h at wake would immediately clear a 4h bar the moment the (much
shorter) wake-grace elapsed.

### Fix
Replaced the standalone 1h grace period with one unified rule in
`_evaluate_outbound_stall`: a lead only counts as backlog if
`run_at <= now - ALERT_OUTBOUND_STALL_HOURS` **and** the campaign's
current wake session (`current_window_start`, added in the prior fix) is
itself at least that old. Both the completions lookback and the backlog
query now share the same `cutoff = now - hours` value. This one rule
covers all three cases:
- Sleep carryover: wake session too young → resolves regardless of how
  overdue the lead looks on paper.
- Same-day pileup: wake session old enough, lead genuinely overdue past
  the cutoff → fires (unchanged real-stall behavior).
- Reschedule-handoff noise: lead overdue by seconds, never gets near the
  cutoff → never counts as backlog, regardless of campaign wake time.

`tests/unit/test_dashboard_v2.py::TestOutboundStallAlert`: rewrote the 3
grace-period tests (threshold changed from a fixed 1h to the full
`ALERT_OUTBOUND_STALL_HOURS`) and added 2 new tests reproducing the
Saturday reschedule-blip scenario directly (overdue-by-2-minutes must not
count; overdue-by-4h01m must). 12/12 pass. Full suite: 1321 passed / 7
pre-existing unrelated failures (unchanged baseline). `ruff check` clean
on both changed files (same 6 pre-existing findings elsewhere in
`test_dashboard_v2.py`, confirmed via `git stash`).

### Deployed
Merged `fix/outbound-stall-sleep-aware-threshold` → `feat/ghl-call-conversation-sync`
(fast-forward), pushed, redeployed Hetzner via `scripts/deploy.sh` — all
13 services up, both health checks `ok`, no new migration. Confirmed the
new code imports cleanly in the live `api` container. One alert row was
active at deploy time (`cfcd2e48`, created 20:35:26 UTC) — verifying it
resolves under the new logic on the next metrics cycle rather than
lingering.

## Session: 2026-09-30 — Wrong Date Monitor + Next Open House Date setting (spec/32, `feat/open-house-date-alert`)

**Status: built and tested locally; NOT committed, NOT merged, NOT deployed** (awaiting Kes's local sign-off).

### What was built
- New setting `next_open_house_date` (Settings page, Streamlit, AI prompt context; migration 0024 seeds `October 29, 2026`, never overwrites a saved value). Prompts now carry both dates and tell the model not to invent any.
- Detector `app/core/wrong_date_guard.py`: class-start / open-house dates in a sent message vs. the dashboard values; appointment / "we'll call you" dates are classified by nearest keyword and ignored.
- `app/services/wrong_date_monitor.py`: scanner (runs each metrics cycle), per-message incident (UNIQUE → idempotent), one email per incident, correction SMS via the GHL Message field, dismiss.
- Dashboard: "Wrong Date Monitor" tile (badge = open count) → `/wrong-dates` page with Send correction SMS / Dismiss.
- Migration 0024 adds `wrong_date_incidents`.

### Tests
46 new (30 pure, 16 DB-backed opt-in via `WRONG_DATE_TEST_DATABASE_URL`). Full unit suite: 1353 passed, 5 failed — the same 5 fail on the untouched branch HEAD (test_enrolled_intent, test_ghl_adapter x2, test_inbound_call_processing x2), unrelated. Migrations 0001→0024 verified on a throwaway local Postgres.

### Not done / to verify after deploy
- `alembic upgrade head` on Hetzner (0024) + `up -d --build`.
- Confirm in live mode (test contact) that writing the GHL Message field actually triggers the SMS workflow.
- Messages sent by GHL-native workflows/templates (not in `outbound_messages`) are not checked.

### Deployed (2026-09-30)
Merged `feat/open-house-date-alert` → `feat/ghl-call-conversation-sync` (`435506e`), pushed, redeployed Hetzner via `scripts/deploy.sh`. All services up, API + Dashboard health `ok`, migration 0024 applied (`next_open_house_date` = October 29, 2026; `next_class_start` = November 12, 2026). `/dashboard/wrong-dates` returns 200. Still to verify in live mode: the GHL Message-field correction actually triggers the SMS workflow (test contact).

## Session: 2026-09-30 (later) — Wrong Date Monitor bulk actions (`feat/wrong-date-monitor-bulk-close`)

**Status: built and tested locally; NOT committed/merged/deployed.**
Investigation finding first: the flagged messages to +16148932691 (and ~115 others) were generated before Kes saved the new dates (19:27/19:50 UTC) and before the open house date existed in the prompts (deployed 21:04 UTC); two post-19:50 emails said Open House = the class date because the prompt had no open house date. No wrong-date message generated since the deploy.
Added: auto-close a lead's other open incidents when a correction is sent; "Dismiss all older incidents" (before last date-settings change); "Send correction SMS to all" (one SMS per lead, batched, failure-capped). 54 wrong-date tests pass.

### Deployed (2026-09-30, later)
Merged bulk-actions branch (`a143779`), redeployed Hetzner, no migration. Health ok.

## Session: 2026-09-30 (later still) — date auto-expiry, free-signup fallback, 24h tile metrics (`feat/date-expiry-and-metrics`)

**Status: built and tested locally; NOT committed/merged/deployed.**
- Dates now clear themselves the day after they pass (year required); prompts then say there is no class/open house and invite leads to www.myfreeaiclass.com; RSVP link withheld; correction SMS has a no-dates variant; monitor flags any dated class/open-house mention sent after a clear.
- Tile + page show Open now / Closed (24h) / corrected / dismissed / new.
- 98 wrong-date tests pass (DB-backed ones need WRONG_DATE_TEST_DATABASE_URL). No migration.

## Session: 2026-09-30 (night) — correction SMS actually delivered via Ticket #4 + SMS safeguards (`feat/date-expiry-and-metrics`)
**Status: built and tested locally; NOT committed/merged/deployed.**
- Root cause of "no SMS": GHL workflow "AI Agent - Send SMS" starts on `Support issue Ticket #4` changing and texts that field; corrections only wrote `Message:`. 154 production corrections wrote Message only (no SMS). Fix writes the text to Message AND Ticket #4; migration 0025 adds `sms_triggered_at`; "Send the missing SMS" re-triggers the 154.
- Safeguards (spec/32): DND/STOP/opt-out block, replied -> hold, campaign + TCPA window, fail-closed contact read, 5s pacing between real sends, daily cap 300. 170 wrong-date tests pass.
- Open: confirm API writes fire the workflow (test on one contact first); how Cora's regular follow-ups are delivered is still unverified (email workflow "AI Agent - Send Email" triggers on Support Issue Ticket #2 + 5s wait).

## Session: 2026-10-01 — email is the correction channel; SMS built but off (`feat/date-expiry-and-metrics`)
**Status: built and tested locally; NOT committed/merged/deployed.**
- Live test on Kes's own contact: Ticket #2 write -> email arrived; Ticket #4 write -> no SMS (SMS workflow not firing/sending; pending GHL execution-log check).
- GHL conversations: Cora's "SMS" follow-ups are delivered as emails (Ticket #2 workflow). So corrections go by EMAIL; SMS corrections gated off (`sms_corrections_enabled`), SMS test-to-self kept to confirm the channel when restored.
- Added: channel-aware send/bulk/test, per-channel ledgers + caps (email 100/day, 3 s), email-specific DND/no-address/window rules, tile email buttons + test buttons. Migration 0025 reopens the 219 never-delivered "corrected" incidents.
- Before first use after deploy: INSERT app_config `correction_test_contacts` (Kes's email) via SQL - deliberately NOT in the repo.

### Deployed (2026-10-01)
Merged `feat/date-expiry-and-metrics` (`f2f9670`) -> `feat/ghl-call-conversation-sync`, redeployed Hetzner (migration 0025 applied; all services up, API + Dashboard health ok). 219 incidents / 137 leads reopened, 0 corrections sent. `correction_test_contacts` set via SQL (not in repo). SMS corrections remain off. SMS workflow verified end-to-end by a live API test to the operator contact (trigger had been watching the wrong field).

## Current state and next steps — early 2026-10-01 (SUPERSEDED: see 'END-OF-DAY STATE 2026-10-01' at the bottom of this file)
**Live in production** (branch `feat/ghl-call-conversation-sync`, deployed `f2f9670`, migrations through 0025): wrong-date detection + alert emails; tile `/wrong-dates` with 24h open/closed metrics; email corrections (one / all) with DND, unsubscribed, no-address, reply, campaign-window, fail-closed safeguards, 3 s pacing, 100/day cap; test-send to Kes's own contact; date auto-expiry with the www.myfreeaiclass.com fallback; `next_open_house_date` in Settings and prompts.
**Explicitly NOT done:** no correction has been sent to any lead (219 incidents / 137 leads open, awaiting Kes's click on "Send correction email to all" after a test email). SMS corrections are built but OFF (`sms_corrections_enabled`).
**Known facts:** Cora's follow-ups (even "SMS" ones) reach leads as EMAIL via GHL workflow "AI Agent - Send Email" (Ticket #2 -> body); the SMS workflow reads Ticket #4 and works end-to-end since its trigger field was fixed on 2026-10-01. If a GHL workflow trigger/field is edited, corrections silently stop — re-run the tile's test buttons.
**Next steps (ordered):** (1) Kes: "Send test email to me", confirm inbox; (2) "Send correction email to all" — first run stops at 100, rest after 24 h; (3) check that real follow-up emails don't arrive as subject-only bodies; (4) decide whether/when to enable SMS corrections; (5) GHL-native SMS automations (not seen by the monitor) still need their own date review; (6) `Mark as Lead` field label does not resolve (separate fix).
Full detail: `directives/spec/32_wrong_date_monitor.md`.

## 2026-10-01 (night) — first live "send to all": proxy timeout, not a data error (`fix/bulk-send-request-timeout`)
Kes clicked "Send correction email to all": 11 leads emailed (no duplicates), 8 auto-skipped (GHL tag "do not contact"). The page then showed "ApiError: Internal Server Error": the Next.js rewrite proxy (`Failed to proxy ... socket hang up ECONNRESET`) drops requests > ~30 s and a 10-send batch took ~35 s; the API completed normally, but the browser loop aborted so 118 leads remain. Fix (local, not yet deployed): 18 s per-request time budget on the bulk send (`BULK_TIME_BUDGET_SECONDS`), UI loop continues on any progress, interrupted requests refresh state instead of leaving a stale error. Tests: 74 DB-backed pass.
Timezone noise (approved by Kes, fixed locally): `get_contact_timezone` logged "unrecognised timezone 'US/Central'" for ~7,350 leads because the containers' slim tzdata lacks the legacy `US/*` aliases; it fell back to America/Chicago, which is the SAME zone, so no scheduling was ever wrong (Eastern/Pacific leads already carry valid names such as America/New_York). Added `canonical_timezone()` alias map (US/Central->America/Chicago, US/Eastern->America/New_York, ...) — behaviour-neutral, removes the log spam; test_campaign_schedule updated, new tests/unit/test_timezone_alias.py.

### Deployed (2026-10-01, night)
Merged `fix/bulk-send-request-timeout` (`9a3e5e3`) -> `feat/ghl-call-conversation-sync`, redeployed Hetzner (no migration; health ok). Verified live: bulk time budget 18 s active, US/Central -> America/Chicago. Open incidents unchanged (188 incidents / 118 leads); nothing sent by the deploy. Next: Kes re-clicks "Send correction email to all" (100/day cap; 11 already sent today).

### Correction run completed (2026-10-01, ~02:17 UTC)
All 219 wrong-date incidents / 137 leads processed via "Send correction email to all" (in short rounds after the proxy-timeout fix): 86 leads emailed (136 incidents, none twice, no failures, cap 100 not reached); 51 leads auto-skipped (83 incidents): 29 tagged 'do not contact', 16 no email address, 5 DND all channels, 1 Email DND. Open = 0. SMS corrections remain off. Ali summary email drafted in Gmail (not sent).
**Remaining/ongoing:** new wrong-date messages (if any) will reappear on the tile; re-verify the Ticket #2 email-body issue and the GHL-native SMS automation dates (see current-state section above).

## 2026-10-01 — "stale date still arriving" sample traced: pre-fix message + GHL email workflow has Subject/Body SWAPPED
A reported sample (sent Sep 30 10:01 AM ET = 14:01 UTC) was generated by Cora at 14:01 UTC with the OLD Settings, 5+ hours before the date change (19:27 UTC) and 7 before the deploy (21:04 UTC). Since the deploy Cora recorded 74 messages, 0 with a wrong date; latest wrong-date message 20:34 UTC. GHL shows both of that lead's Sep 30 messages (the "SMS" text and the real email) as TYPE_EMAIL, source=workflow, 17-24 s after Cora's writes -> GHL workflow "AI Agent - Send Email" (trigger Support Issue Ticket #2 changed).
**Bug found:** that workflow's email SUBJECT is `Message` (the whole story) and its BODY is Support Issue Ticket #2 (only Cora's short subject) - swapped. Correct mapping: Subject = Ticket #2, Body = Message (Cora writes subject->Ticket #2, body->Message). All 86 correction emails were verified to carry the correction text in BOTH fields (Message was overwritten by the first run), so their subjects have correct dates (just long).
**If the workflow is swapped in GHL:** the correction code must then write Message = body and Ticket #2 = a short subject (today it writes only Ticket #2) - do this together with the GHL change.
**Heads-up:** now that the SMS workflow trigger works, any text something else writes into `Support issue Ticket #4` (e.g. the Synthflow post-call recap SMS) will be SENT as an SMS; legacy values in that field carry old dates (1/31, 3/28, 5/30/2026) - review the Synthflow prompt/output for dates.

## 2026-10-01 (late) — correction email subject/body fix (`fix/correction-email-subject-body`)
GHL workflow "AI Agent - Send Email" had Subject = Message and Body = Ticket #2 (swapped); Kes re-mapped it (Subject = Ticket #2, Body = Message). The 86 correction emails already sent had the full correction text in both subject and body (dates right, subject long) - NOT re-sent. Engine fix: correction email now writes a short subject (from the engine: `correction_email_subject`) to Ticket #2 and an HTML body to Message in one update; test email builds a labelled subject/body. Found while testing: the scanner would have flagged our own HTML correction (list items read as one sentence) - `clean_text` now treats block tags as sentence breaks. 198 wrong-date tests pass.

## 2026-10-01 (night) — cadence, daily call cap, real-SMS routing (spec/33, `fix/cadence-sms-routing-call-cap`) — NOT deployed
Kes: voicemail cadence must match Settings (Cold 120/2880/2880, New 120/1440/2880), "SMS" must be SMS, max 2 calls per lead per day unless a callback was requested. Root cause of 3-4 messages/day: the slot rebalancer re-ranked ALL pending calls from "now" whenever one slot was over capacity (48 h retries ran at a median 4.6 h); plus SMS text emailed via Ticket #2. Fixed locally: rebalancer only spreads excess later (never earlier), daily cap 2 (callbacks exempt), sms -> Ticket #4 real SMS (DND/opt-out/no-phone skip, TCPA 08-21), classification no longer written to Ticket #4, `sms_delivery_mode=email` rollback switch. 28 new tests. **URGENT:** until deployed, the old code can write a classification word into Ticket #4 (now an SMS trigger) when calls resume in the morning. See directives/spec/33_call_cadence_and_sms_routing.md.

### 2026-10-01 (night, later) — link rule for SMS
Kes: block links in SMS (links go by email), then relaxed: `myfreeaiclass.com` is allowed in SMS. Implemented `app/core/sms_links.py` + routing: any other link -> the follow-up is delivered as an EMAIL (Ticket #2 subject + Message HTML), never as a text; allow-list `sms_allowed_link_domains` (default myfreeaiclass.com, empty = no links). Queue check: 167 pending calls only (no pending sms/email/update jobs); 3% of last-7-day SMS carried a link. Tests: 47 cadence + 76 DB-backed pass. Still NOT deployed.

### Deployed (2026-10-01, ~04:35 UTC): cadence fix + daily call cap + real SMS + NO links in SMS
Merged `fix/cadence-sms-routing-call-cap` (`60e2315`) -> `feat/ghl-call-conversation-sync`, redeployed Hetzner (no migration, health ok). Kes's final link decision: NO link of any kind in an SMS (not even myfreeaiclass.com); `sms_allowed_link_domains` defaults to empty; link-bearing texts go by email. Verified live: allow-list empty, rebalancer ran twice post-deploy with no failures, no tracebacks.
**Kes also asked to cancel all scheduled Cold Lead calls so new cold/warm leads can be monitored:** 167 pending Cold Lead `launch_outbound_call` jobs (all Cold Lead; none had been handed to a worker) set to `cancelled` in one transaction, audited as `cancel_all_pending_cold_lead_calls` (job ids stored in `audit_log.context_json->'job_ids'`). 0 pending launch calls afterwards. New leads entering via GHL create fresh calls as normal; GHL re-enrolling old leads (stale AI Campaign flags) would also create calls.
Restore (only if wanted): `UPDATE scheduled_jobs SET status='pending', version=version+1, updated_at=now() WHERE status='cancelled' AND id IN (SELECT jsonb_array_elements_text(context_json->'job_ids') FROM audit_log WHERE action='cancel_all_pending_cold_lead_calls' ORDER BY created_at DESC LIMIT 1);` (restored jobs whose run_at has passed fire immediately, still subject to the window and 2/day cap guards).
Watch next day: the two verification queries at the bottom of directives/spec/33_call_cadence_and_sms_routing.md.

## 2026-10-01 (night) — pre-send SMS gate + 999/day budget (spec/34) — DEPLOYED
Kes: make SMS abide by Twilio sole-proprietor limits, monitor every SMS before sending, cap 999/day, move overflow to the next day. Research: 1,000 segments/day to T-Mobile (Pacific day), 1 MPS, ~15/min AT&T (Twilio pages returned 403 to our fetch; numbers from search summaries — confirm in the Twilio console).
Built: `app/core/sms_gate.py` (pure rules), `app/services/sms_ledger.py` (reserve/mark_sent/mark_failed/snapshot, advisory lock), migration `0026_sms_send_ledger`, gate wired into `update_ghl_after_vm_message` (defers by releasing the job) and `wrong_date_monitor` (corrections + test sends; bulk stops on exhausted budget), `GET /dashboard/sms-monitor` + SMS Monitor tile. Tests: 23 new pure/sqlite + 4 DB-backed (Postgres) ; full DB-backed wrong-date suites 80 pass. Unit suite: no new failures (2 admin-route Redis tests also fail on a clean tree).
Deploy needs: `alembic upgrade head` (0026). Not counted: GHL-native/manual/Synthflow texts.

**Deployed** (commit `e7eac1a`, merged into `feat/ghl-call-conversation-sync`, Hetzner redeployed via scripts/deploy.sh): Alembic at 0026, `sms_send_ledger` exists (empty at deploy), `GET /dashboard/sms-monitor` returns cap 999 / min gap 5 s / 12 per min / 4 segments, no tracebacks in the first 3 minutes. First real SMS will create the first ledger row.
**Next (Ali, 2026-10-01 emails):** report DELIVERED not attempted; add per-channel (email / SMS / call) last-activity timestamps measured on delivery, with a scheduled silence check (not an error handler) that alerts Kes and copies Ali after 2.5 days quiet. Ali: leave SMS on, SMS corrections stay off.

## 2026-10-01 — delivery health: sent vs delivered, per-channel silence alert, reply logging (spec/35) — built, tested locally, NOT deployed
Ali: report delivered not attempted; last activity per channel (email/SMS/call); scheduled silence alert after 2.5 days (to Kes, cc Ali). Built: migration `0027_channel_events` (channel_events, channel_sync_state, inbound_messages.external_id), `app/core/channel_health.py` (pure rules), `app/services/delivery_sync.py` (GHL conversations -> deliveries + replies, throttled/resumable), `app/services/channel_health.py` (snapshot/detail/hourly silence check), hand-off recording in `update_ghl_after_vm_message` + corrections, `GET /dashboard/delivery-health[/{channel}]`, Delivery Health tile. Live probe findings: SMS status delivered/undelivered(+Twilio error) is in the message list; email status is only behind meta.email.messageIds[0]; calls use call_events (call matching validated on prod: ~93% delivered, ~1% unmatched). Replies were never logged before (inbound_messages was empty) - now they are (last_replied_at deliberately untouched). Tests: 20 pure + 7 Postgres-backed new; all DB-backed wrong-date suites pass (117). Deploy needs `alembic upgrade head` (0027).

## 2026-10-01 — delivery health deployed (`518035c`, migration 0027) + opt-out -> real GHL DND (spec/36)
Delivery health live: alembic 0027, first sync read 38 deliveries + 2 call replies; SMS/email "sent" counts start at 0 (hand-offs recorded only from the deploy). Finding: `inbound_messages` had never been written; Cora's call regex never set GHL DND (16 of newest 40 Cora do-not-call leads had no DND in GHL; free-form SMS/email opt-outs also none).
Built (Kes: "DND by the lead's wording; yes to all decisions"): `app/core/optout.py` (wording->scope), `app/services/optout.py` (apply / LLM judge / review / undo / reconcile / `cora_block_reason`), `GHLClient.set_dnd` (payload round-tripped on Kes's own contact: SMS-only and all-channel set + cleared), hooks in `delivery_sync` (replies), `ai_jobs` (do_not_call calls) and `update_ghl_after_vm_message` (Cora-side block), migration `0028_optout_actions`, tile `/optouts` ("Opt-outs & DND"), reconcile each metrics cycle. Tests: 39 new (wording matrix + Postgres-backed); all DB-backed suites 156 pass; unit suite only the 7 known failures.

## 2026-10-01 — one offer + SMS is a notification (spec/37) — built, tested locally, NOT deployed
Kes: only the AI Systems Architect Accelerator is offered; SMS = missed-call notification / upcoming-call reminder (1 segment, 2 max), email = the program description. Found: prompt hard-coded "Data Analytics or AI" (46 % of 3,528 SMS and 63 % of emails said Data Analytics in 14 days); story library entirely the retired program; one LLM call per channel generated both; curly apostrophes made 28 % of SMS 3 segments. Built: `app/core/offer.py`, new `vm_sms_notice` prompt family + email-only `vm_followup_generator`, `generate_vm_sms/_email` with retry+fallback, `normalize_sms`, gate rules (retired terms in all SMS, marketing words in follow-up SMS, max 2 segments), `channel=` routing in `channel_jobs`. Live sample with the real model: 4 tiers 127-156 chars, 1 segment, gate-approved. Tests: 31 new; unit suite only the 7 known failures; DB-backed suites 156 pass. Open: upcoming-call reminder SMS (not built), Synthflow/GHL-native texts unreviewed, KB "rolling enrollment" vs Settings Nov 12.

### 2026-10-01 — deployed: offer/SMS-notice (`37ed7ba`) and opt-out DND fixes
Live check after deploy: production settings give 1-segment gate-approved SMS ("...about the AI Systems Architect Accelerator. Please reply with a good time to talk.") and an email with the Open House + self-paced options and no retired terms. Decisions: no upcoming-call reminder SMS (none existed, none to be built); Synthflow and GHL-native SMS prompts not reviewed; enrollment is ongoing for the self-paced option, the Open House is for people who want live classes.
Opt-out live findings: 4 DND writes failed - (a) GHL refuses to overwrite PERMANENT SMS DND; (b) the reply sync used the conversations token for the write. Fixed: only missing channels are written; contacts token; automatic retry (max 5). 185 call opt-outs await review, 16 already DND, 203/203 checked.

### 2026-10-01 — Delivery Health false "not confirmed" fixed
Kes's screenshot showed SMS 12 sent / 0 delivered / 8 not confirmed. GHL itself showed all 12 texts `delivered` within ~7 s of each hand-off (source `workflow`) - the miss was ours: the sync was still in its one-time history load and only walked history, so today's messages were not read. Fixed: every run now first walks the newest conversations (forward, since watermark - 30 min) and then continues the history load with the remaining budget. Drill-down now shows each lead's full phone, email and GHL contact id (looked up from GHL, parallel and time-boxed) so rows can be verified in GHL.

## END-OF-DAY STATE 2026-10-01 — read this first
**Production** = branch `feat/ghl-call-conversation-sync` at `11f1e72` (+ this docs commit), Hetzner redeployed after every change, Alembic at **0028**. All services up; no tracebacks at last check.

**Live and verified**
| Area | What runs | Spec / key code |
|---|---|---|
| Wrong-date alert + corrections | detector, tile `/wrong-dates`, email corrections (SMS corrections OFF), auto-expiry of passed dates | spec/32 |
| Call cadence | retries keep their delays; 2 calls/lead/day cap (callbacks exempt); real SMS; no links in SMS; 167 Cold Lead calls cancelled (restore SQL in the 04:35 UTC entry) | spec/33 |
| SMS gate + budget | every Cora text checked first: 999 segments/US-Pacific day (deferred to next legal hour), 5 s gap, 12/min, <=2 segments, content rules; ledger `sms_send_ledger`; tile `/sms-monitor`; alerts at 80%/100% | spec/34, `sms_gate.py`, `sms_ledger.py` |
| Delivery health | per channel (email/SMS/calls): handed over vs delivered vs not-confirmed vs failed, last delivered, 7-day trend, Replies row; sync every 2 min (newest first, parallel, 5 calls/s cap, resumable); hourly silence check -> email Kes cc Ali at 60 h without delivery (or >=20 sent & <50% delivered); labeled TEST alert sent and logged as delivered to SMTP | spec/35, `delivery_sync.py`, `channel_health.py` |
| Opt-outs -> real GHL DND | DND by the lead's wording from answered calls and SMS/email replies; LLM judges free-form (>=0.85) else review; Cora's own follow-ups withheld for opted-out leads; reconcile of Cora do-not-call leads vs GHL; tile `/optouts` (apply / dismiss / undo / apply-all) | spec/36, `optout.py` |
| One offer + SMS purpose | only the AI Systems Architect Accelerator; SMS = short missed-call notice (1 segment); email = program description (self-paced anytime vs live classes via Open House); retired-course terms blocked in code; student stories OFF | spec/37, `offer.py`, `vm_sms_notice.py` |

**Migrations this day:** 0024 incidents, 0025 triggered-at, 0026 `sms_send_ledger`, 0027 `channel_events` + `channel_sync_state` + `inbound_messages.external_id`, 0028 `optout_actions`.
**app_config knobs (no deploy needed):** `sms_daily_segment_cap` (<=999), `sms_min_gap_seconds`, `sms_per_minute_cap`, `sms_max_segments_per_message` (2), `sms_allowed_link_domains` (empty), `offer_name`, `offer_facts`, `offer_forbidden_terms`, `channel_silence_hours` (60), `channel_silence_cc` (ali@colaberry.com), `channel_silence_muted`, `delivery_sync_interval_seconds` (120), `optout_llm_enabled`, `optout_reconcile_enabled`, `sms_corrections_enabled` (false).

**Findings worth remembering**
* The SMS workflow had matched nothing for a month while "attempted" looked healthy -> measure DELIVERED per channel (spec/35). GHL message statuses: SMS `delivered/undelivered` (+Twilio error), email status only behind `meta.email.messageIds[0]`, calls from `call_events` (log arrives 10-15 min later).
* Cora's call regex never set GHL DND (16 of the newest 40 Cora do-not-call leads had none; free-form email/SMS opt-outs also none); `inbound_messages` had never been written. GHL refuses to overwrite a PERMANENT DND (write only missing channels); the conversations token cannot edit contacts (use the contacts token).
* 46% of 3,528 SMS and 63% of 229 emails (14 days) named the retired Data Analytics course; the whole story library is the retired program.
* First delivery-health version false-alarmed (SMS 12 sent / 0 delivered) because history loading starved fresh messages; GHL showed all 12 delivered. Fixed (forward pass first, parallel, rate-capped). GHL answers 429 above its burst limit - keep sync calls <= 5/s.

**Waiting on Kes**
1. `/optouts`: ~185 call opt-outs proposed for DND in GHL (scope from the lead's own words) - review a few quotes, then "Apply DND to all"; 1 item needs a second Apply (earlier permanent-SMS-DND failure).
2. Confirm Twilio sole-proprietor limits in the Twilio console (taken from search summaries; Twilio's pages returned 403).
3. Ali: draft reply to his 8:46 AM email is in Kes's Gmail Drafts (NOT sent). Ali should confirm he received the labeled TEST alert.
4. Settings still carry next class start Nov 12 while the self-paced option is open-enrollment; the email follows the Settings schedule block.

**Scheduled (session-only, die if the Claude session closes):** hourly audit crons for 2026-10-02 (`7 * 2 10 *`) and a 23:52 report email to Kes. If the session is closed, re-run the spec/33 and spec/34 verification SQL by hand.

**Explicitly not done / decisions**
* No upcoming-call reminder SMS (none existed; Kes: do not build). Synthflow's end-of-call text and GHL-native automations are NOT covered by the SMS gate/ledger/offer rules (Kes: out of scope) but share the Twilio quota.
* Voicemails are not transcribed for opt-outs; email "delivered" = provider accepted, call "delivered" = connected; email/SMS "sent" counts start at the 2026-10-01 deploy.
* Pre-existing unit-test failures (7): test_admin_routes x2 (Redis), test_enrolled_intent, test_ghl_adapter x2, test_inbound_call_processing x2 - fail on a clean tree; four more fail only at night (campaign windows).

**Next steps (ordered):** (1) Kes approves the opt-out list; (2) read tomorrow's hourly-audit report; (3) check `/delivery-health` shows no red channel for 24 h and the silence check keeps running (`audit_log` action `channel_silence_check`); (4) decide on Accelerator success stories for email; (5) consider extending SMS Monitor rows with phone/email like the Delivery Health drill-down.


### 2026-10-01 (evening) — scan of existing free-text opt-outs; classifier tightened
Kes cleared the reconciliation DNDs and sent the Ali email. Scanned production call transcripts (read-only, lead's own lines, answered calls only) for opt-out wording not yet covered: 19 leads with clear DND wording (9 call-only, 10 all channels; in GHL: 8 no DND, 6 partly, 2 already, 3 unreadable), 30 with unclear wording (stop / remove / spam), 4 wrong-number. Separate finding: of 52 calls where Cora's own regex concluded `do_not_call`, 40 leads are flagged, 7 have no `lead_state` row (flag could never be stored), 5 are flagged FALSE and all 5 were called again after the opt-out (all May 2026). Nothing from this scan has been applied or queued - waiting for Kes. Bug found by the scan and FIXED + deployed: the wording rules matched verb and object up to 4 words apart ("I never received the email" read as an email opt-out; "don't forget to email me"); now the verb must sit next to its object ("stop calling", "don't send me any more emails", "don't call or text me"), plus 15 regression tests. Voicemail greetings are excluded from scans (status hangup_on_voicemail).

### 2026-10-01 (evening) — INCIDENT: 5 wrong automatic DNDs (Cora's own email read as a lead's opt-out) — fixed
Kes spotted a queued "history_email" item whose "reply" was Cora's own email with the GHL footer ("...If you no longer wish to receive these emails you may unsubscribe [link]"). Verified: ALL 15 email "replies" recorded so far were Cora's own emails logged by GHL as inbound; 5 of them had auto-applied DND on ALL channels to leads who had said nothing (applied by the reply sync earlier the same day; the "3 unsubscribes applied" line in the Ali summary was therefore WRONG). Remediation (done): all 5 undone via the tile's undo path (GHL DND removed except one lead's pre-existing SMS DND; no Cora lead flags had changed - those leads had no lead_state row; no scheduled jobs had been cancelled), 10 echo records removed from inbound_messages/channel_events, 10 history_email rows dismissed. Root causes fixed + deployed with regression tests: (1) classifier ignored Cora's own wording / platform footer (`split_own`, `is_own_echo`), (2) only the first 350 chars of an email count, (3) third-party "how to stop" boilerplate (Stripe "Reply STOP to cancel", verification-code texts, appointment-reminder footers) is not an opt-out, (4) echoes are not recorded as lead replies (the Replies row had counted them). Lessons: never auto-apply on text a human has not seen the shape of; the scan of real data found in minutes what unit tests missed. The historical 90-day replies scan was restarted with the corrected rules (review-only).

### 2026-10-01 (evening) — 90-day history scan finished; audit of the 187 applied reconciliation DNDs
Replies scan (review-only, corrected rules): 4,064 conversations over 90 days; 389 inbound SMS + 74 inbound emails stored in `inbound_messages` (Cora's STOP check now sees history). Findings: 16 exact STOP-type replies (all already DND in GHL), 1 genuine "Stop calling" SMS (GHL has no Call DND -> on the review list), 1 false positive ("not in my spam" in a complaint that an email never arrived -> "Not an opt-out"), 0 new email opt-outs. Review list now: 50 `history_call` (20 clear, 30 unclear) + 2 `history_sms`. Audit of the 187 reconciliation DNDs Kes applied: 36 backed by the lead's own words on a call; 125 already carried a GHL "do not contact" tag; 2 had partial DND; 2 were weak call evidence but ALSO carry the GHL tag; 22 have no evidence anywhere (Cora had them closed/do-not-call via an unknown earlier route) - list given to Kes (phone/email/GHL id) for a manual check in GHL; undo is available on the tile. Lesson: the reconcile proposal defaulted to "all channels" when no transcript existed - show the evidence basis on the tile before bulk-applying.

### 2026-10-01 (evening) — review list quality: LLM triage, weaker word hits, full phone/email on the tile (migration 0029)
Kes: two "unclear" items were plainly not opt-outs ("leave me a message", "not in my spam") and the tile showed truncated contact ids that cannot be pasted into GHL. Word matching only NOMINATES a line; fixes: (1) hint words tightened ("leave me alone/be", spam only as a complaint about us); (2) every "unclear" item is read by the LLM before a human sees it - a clear non-request (>= 0.8) is dismissed with the reason on the row (decided_by llm), otherwise its verdict is attached; (3) `optout_actions.contact_phone/contact_email` (0029) filled once from GHL, shown with the full GHL id as selectable text on every review/applied row (labels added for history_* sources). Result on production: 52 -> 24 items waiting, all `history_call` (several are robocall transcripts mislabeled "human:", plus a billing request - "stop my subscription for IPBC" - that is not a DND). Cycle now also triages 5 unclear items per minute.

### 2026-10-01 (evening) — "Could not resolve GHL contact for phone" on Apply: fixed
Three review rows (bare numbers 33569149532 / 31960551778 / 36544831645: +33, +31, +36 callers with a single call in April 2026, no GHL contact, no lead_state, no pending jobs) failed on Apply because there was no GHL contact to put DND on. Apply now treats "GHL has never heard of this number" as a recorded no-op: nothing is written to GHL, Cora's own record is stopped if one exists (also tries the +E.164 form of a bare number), the row becomes `applied` with the reason shown, and Undo needs no GHL contact (`previous_state.no_ghl_contact`). Genuine errors (401/429/timeouts) still stay on the tile as "apply failed". The three rows were re-applied. Tests: no-contact apply + undo, lead-record-without-contact stops Cora calling.

### 2026-10-01 (evening) — "queue lag 1001 s / 1 stuck job" investigated: duplicate quality-scan chains
Not a stuck job: the only overdue jobs were `staff_call_quality_scan` (isolated queue/worker, 4-12 min per run, avg 258 s over 206 runs). Cause: each scan reschedules itself and every worker start enqueued ANOTHER scan if none was `pending` (a running one was ignored), so each of today's ~12 deploys could add a permanent extra chain (3 existed); with one worker, extra chains sit past-due and the lag metric (oldest overdue pending job, all types) tripped the 300 s alert (9 times in 24 h, each self-healing in 2-5 min). Fixed + deployed: (1) startup check and `_reschedule` both refuse to create a second chain; (2) the surplus pending scan was cancelled (audit_log `collapse_duplicate_scan_chain`); (3) `queue_lag_seconds`, `stuck_pending_count` and the Queue Health stuck list exclude the isolated batch queue; (4) new alert `staff_quality_scan_stale` (warning): scan enabled and no scan completed in 60 min (setting `alert_quality_scan_stale_minutes`) and none running with a live lease. Tests: tests/unit/test_batch_queue_health.py (5).

### 2026-10-02 - missed-call text delay was 2 min, not 30 (fixed, deployed)
Hourly audit found missed-call texts going out ~2 min after voicemail processing (17-19 min after the call), not +30 min (spec/33).
Cause: server `.env` has `SMS_FOLLOWUP_DELAY_MINUTES=2` (overrides the code default 30) and `voicemail_jobs.py` read the Settings
object directly, so the Settings-page value (`app_config.sms_followup_delay_minutes=30`) was ignored. Fix (`634357c`): both reads
(`send_sms`, `send_email` scheduling) now use `get_int` (app_config -> env -> 30). Regression test
`test_sms_delay_comes_from_the_settings_page_not_a_stale_env_value`. Deployed ~15:20 UTC; verified in a worker: env 2, effective 30.
Texts already scheduled before the deploy keep their original 2-min run time. The `.env` line was NOT edited (now harmless; remove when convenient).
Other code that reads a Settings field directly for a key that is also on the Settings page may have the same flaw - not audited.
Suite: 7 unit tests fail with or without this change (admin routes, ghl adapter, enrolled intent, inbound call processing) - pre-existing.
Also drafted `directives/spec/38_offer_single_source_roadmap.md` (uncommitted, proposed; decisions D1-D5 pending Kes).

### 2026-10-02 - SMS delay reverted to 2 minutes (Kes decision; deployed)
Kes confirmed the intended missed-call text delay is **2 minutes** (original design, spec/00 and spec/16). The 30-minute figure in
spec/33, campaign-steps-reference and expected_outcomes was wrong and has been corrected. Production Settings value
`sms_followup_delay_minutes` set to 2 (via `set_config_value`, updated_by `kes-request`); code default, dashboard default and docs now 2
(`4599a62`). The SMS prompt no longer says "about 30 minutes ago" (now "a few minutes ago"). The earlier fix (`634357c`: the Settings
value, not a stale env value, drives the delay) stays, so the Settings page is now the one place to change it. Migration 0009 still seeds
"30" for a fresh install; the live row is 2. Hourly audit rule "text 25-40 min after the call" is obsolete: expect ~15-20 min after the call start
(2 min after voicemail processing, which lags the call by 10-15 min).

### 2026-10-02 - audit follow-ups: SMS outcome recording, unsent texts, extra texts (deployed `f7eddd0`)
- **outbound_messages.status now means something.** It used to stay `pending` after every real send, so a silent non-send looked like a send.
  `update_ghl_after_vm_message` now marks the lead's newest pending row `sent` (handed to GHL) or `skipped` (opt-out / DND / no-send plan) and logs
  a warning for skips. Today's rows were backfilled from the send ledger (11 sent, 2 skipped). Tests in `tests/unit/test_crm_jobs.py`.
- **Two texts generated but not sent (10:14, 10:20 CT).** Both numbers DO exist in GHL (Kes's guess that they were missing was checked). +15853097197
  (Janice B Griffin) has SMS DND active in GHL (not set by Cora), so withholding the text was correct. +13463705071 (Marthe Sandra Ngono Ottou) has no DND
  and no explanation yet - to be watched; a third pending row (10:50) was in flight at deploy time.
- **Extra delivered texts in GHL with no Cora handoff.** Ngugi Ngugi (+19728017769, joyngugi@yahoo.com): she replied at 10:00 and 10:26 and an outbound
  text was delivered 9-18 s after each reply, plus two more at 10:15 and 10:42 - an automatic responder in GHL (not Cora's gate, not counted in the daily cap),
  content not visible to us. Wilbert Young (+16013837412): one delivered text at 09:15 with no Cora handoff. To be identified in GHL (workflow / Conversation AI).
- **The 102 cancelled Cold Lead calls (Sep 30 23:36 CT)** were cancelled on purpose because they carried the wrong message (Kes).

### 2026-10-02 - Delivery Health: leads with no email in GHL (deployed `a061e02`)
Kes: some "unconfirmed" email leads have no email address in GHL. Checked: of 10 unconfirmed email hand-offs since Sep 30, 7 had no email on the
GHL contact (3 had one; one is a typo domain "uahoo.com"). New `mark_no_address` (runs after the sync, once per contact) tags those hand-offs;
they are excluded from Sent / Not confirmed / Failed and shown as "+N no email on file (not counted)" (drill-down: "no email on file"). On
production: 8 checked, 7 no address; email last 24h = 58 sent, 55 delivered, 3 unconfirmed, 7 no email on file, green. spec/35 updated; DB tests pass.
NOT done: Cora still generates and hands off an email for a lead with no email address (the follow-up job does not look the address up). Sequencing
note: the earlier outbound_messages status fix (`f7eddd0`) had already been deployed when Kes asked for this to be considered "before commit and deploy".

### 2026-10-02 - alert headers + lead-by-lead exposure report (Ali's follow-up), deployed `41e4024`
- Delivery-health alerts now carry hidden headers (`X-Cora-Alert: health`, `-Channel`, `-State`, X-Priority/Importance on active) and fixed subjects
  (`[CRITICAL] Cora: Email|SMS has gone quiet`, `Calls have`, `[RESOLVED] ... is/are delivering again`) so Ali can pin an inbox rule to the header. No "TEST" in any real alert.
  Other system alerts (`Cora Alert: <type>`) do not carry the header yet. Sample renders: scratchpad `sample_alerts.txt`.
- Exposure report (read-only, scratchpad `exposure.py`): 223 applied DNDs, 191 measurable (32 undated), 68 contacted after the recorded stop. Cora called again: 15 leads
  (May 28-Jun 3; pending retries not cancelled; gate added Aug 25). Cora-style follow-up email 3-25 min after the flag: 46 leads (all Jun 9, workflow). Text/email >24 h later: 5 leads,
  15 messages (11 from the GHL app, 4 workflow). Staff calls from GHL after: 4 leads, 14 calls. Caveat: ~150 of the 187 reconciled leads share bulk-update timestamps
  (Apr 30: 23, Jun 1: 57, Jun 9: 70), so the "opt-out date" is when Cora recorded do-not-call. The Oct 1 statement that Cora showed no sends after the flag was WRONG (Cora's own table
  does not record them); corrected in the draft reply to Ali. Draft reply with samples + report created in Gmail (not sent).

### 2026-10-02 - portable GHL integration kit (for the next app, GHL Voice AI instead of Synthflow)
Assessed the existing docs: `docs/ghl-integration-pattern-reference.md` (Jul 31) and `directives/spec/16_ghl_integration.md` (Aug 26) are accurate for auth/lookup/create/retries but
predate DND (`set_dnd`), delivery and email-status reads, the field-write-triggers-workflow send pattern, the 5 calls/s limit, message `source`, the SMS gate and the opt-out/tag rules;
there was no GHL skill. `.env.example` was also missing 14 GHL settings (Ticket 2/3/4, Message, history, OAuth app, independent write gates). Added:
`.claude/skills/ghl-integration/{SKILL.md,KIT.md}` (copy the folder to the other repo's `.claude/skills/`), the missing vars in `.env.example`, and pointer notes at the top of the two older docs.
GHL Voice AI section of the kit is explicitly UNVERIFIED (call start, result delivery, outcome mapping, DND) - needs live-account/docs research and its own spec before coding.
- 2026-10-02 (later): the GHL skill/kit was made project-neutral at Kes's request - no cadences, timings, caps, wording, offer, campaign names, field names or file paths from this project; only API behaviour, env variables, safety standards and the unverified Voice AI research list. Project-specific rules stay in this repo's specs (33-38) and runtime config.

### 2026-10-02 - "blocked dial number" critical alert: bad normalized_phone (fixed, deployed `a9982d8`)
Alert for contact +12563486405 (natasha bryant, 11:40 CT): campaign entry scheduled a call to +19729921028 (Cora's own Synthflow line) because the lead's `normalized_phone`
held the agent line (legacy inbound-call bug, spec/24); `BLOCKED_DIAL_NUMBERS` guard cancelled the job - no call placed. 5 phone-keyed leads had the same corruption (+12145641367,
+12563486405, +13345408915, +14432783125, +16153199706): `normalized_phone` reset to `contact_id` (audit_log `normalized_phone_repair`), alert resolved. Code: `enter_campaign` now dials
a phone-shaped `contact_id` first and only uses `normalized_phone` when contact_id is not a phone (regression test added). Left alone: +12183102142 (normalized +15134963210, closed lead) and the
2 rows where the agent line is itself the contact_id (blocked by design). Also noted: this lead is tagged do-not-contact in GHL yet was enrolled - the GHL-tag call gate is still unbuilt.

### 2026-10-02 (night) - Ali's follow-up: headers on all alerts, alert drill, responder + DND checks (deployed `a6394d7`)
- `X-Cora-Alert: system` (+Type/Severity/State, priority) on generic system alerts; health alerts keep `health`. `python -m app.services.alert_drill --kind health|system [--channel email|sms|call] [--dry-run]` sends one real-shaped alert plus `X-Cora-Alert-Drill: true`, writes nothing. Not run for real yet (waits for Ali's rule). System alerts go to `alert_email_to` only (Ali not cc'd unless asked).
- GHL automatic responder identified: an AI admissions assistant in GHL (source `app`), not Cora. Since Sep 28: 36 sms replies, 26 answered within 3 min, 6 opt-out-worded replies, 0 answered. 6 of 39 app-sent texts in those threads name Data Analytics (retired) - GHL-side prompt/KB needs fixing (outside this repo).
- DND honoured by GHL: read-only evidence only (222 leads, 0 outbound after DND), NOT proof; a controlled test on a test contact (staff send, workflow send, bulk send, STOP reply) is pending a test number/email.
- Duplicate email to Ali was two Gmail drafts both sent (my draft edit left the old one), not a system duplicate; no alert event emailed twice. The blocked-dial alert email was confirmed in the Sent folder (11:40 CT).
- Unit suite at this hour shows 11 failures: the usual 7 plus 4 time-of-day tests (`test_channel_jobs` x2, `test_messaging` sms outbound, `test_shadow_mode` synthflow) that only pass inside calling hours; verified they fail identically on the previous commit at 22:25 CT. Worth making those tests time-independent.
- Draft reply to Ali created in Gmail (not sent). Still open: call gate on GHL tags/DND (unbuilt), who sent the 14:26 GHL-app blast.

### 2026-10-03 - GHL Conversation AI agent "Cora": root cause and replacement prompt
Root cause of the responder naming the retired course: the GHL agent's own prompt (Dec 2, 2025, Data Analytics program, 50% scholarship / $1,000 tuition, Climb Credit, 71% employment rate, TWC, SQL/Python) was never updated; it lives only in GHL and was on no list. The Oct 1 knowledge-base update did not change the prompt, and the prompt wins in opening replies. Same structural cause as the follow-up texts (offer hand-written in several places, nothing checks agreement). Replacement prompt saved at `docs/ghl-conversation-ai-cora-prompt.md` (`0cbec42`); NOT yet applied in GHL (needs someone with GHL access); register row 11b added to spec/38. Pending: date-separated check of whether the agent stated scholarship/$1,000/Climb/71% to any lead since the new program launched; daily retired-term check of GHL outgoing text once applied.
- 2026-10-03: Kes reports the replacement prompt is now applied in GHL (Conversation AI > Cora). Follow-ups: daily retired-term / opt-out check of the agent's outgoing text for a week (not yet built; manual query possible), date-separated check of earlier scholarship/$1,000/Climb/71% statements, decide Auto-Pilot channel scope, detach the Feb 2026 knowledge base.
- 2026-10-03: date-separated check of the GHL assistant's claims (app-sent texts in the threads of 118 leads who replied by text, ~1,200 texts): old-program claims (scholarship, $1,000 tuition, Climb/IPBC, 71% employment rate, TWC) appear regularly Oct 2025 - Aug 2026 (true then for the Data Analytics program, not judged). Since Sep 4 (new-program knowledge base): no scholarship offer, $1,000, Climb/IPBC or TWC claim; ONE unsupported claim - Oct 1 4:49 PM CT to Kehyonnah Clark (+14047075594): "our program has a 71% employment rate"; and one correct "No scholarships are available right now" (Oct 2). Not covered: email, Instagram, Facebook, WhatsApp, live chat replies; staff texts are mixed into source `app`. Decision for Ali: whether to correct Kehyonnah Clark. Ali draft updated.

### 2026-10-03 18:26 CT - outbound calls PAUSED (Kes, on Ali's instruction) until the GHL opt-out call gate is built
`app_config.outbound_campaigns_paused = true` (set via `set_config_value`, updated_by notes the reason). Effect (`outbound_jobs.py:378`): New Lead and Cold Lead call jobs are released back to pending every 60 s instead of dialing; Inbound continues; texts/emails already scheduled for earlier missed calls still go. Verified from a second session (flag and mode flags both true); no call job completed in the 30 minutes before the pause. Context: 59 pending calls in the next 24 h, 10 to leads with GHL opt-out tags, first at 19:15 CT. TO RESUME: build + deploy the call gate (GHL Call DND, `dnd`, tags do-not-contact / do-not-call-again / not-interested; configurable), check it against those leads, then set the flag back to false (dashboard Settings or `set_config_value`). Caution: held jobs become due immediately on resume (check `rebalance_call_slots` spacing and the calling-hours window first). Also open from Ali's Oct 3 reply: "free" wording vs TWC rules (68 of 124 emails since the fix, voice prompts, KB, offer facts, the live GHL prompt); employment-rate ban to be coded; corrected prompt to be sent to Ali; DND test on Kes's own contact.

### 2026-10-03 evening - opt-out call gate built, deployed (`1f74361`), outbound calls RESUMED
Per Ali (calls must read the GHL flag; pause until built). `app/core/call_gate.py` + `app/services/call_gate.py` + hook in `launch_outbound_call_job` (spec/39): before every call, read the lead's live GHL record; block on `dnd`, Call DND, or tags in `app_config.call_block_tags` (default: do not contact, do not call again, do not call, not interested, dnd, unsubscribed). Block = cancel this and the lead's other pending jobs, close the lead, set Cora `do_not_call` for "asked us to stop" tags/DND, audit_log `call_blocked_ghl_optout`. GHL unreadable = fail closed (retry 5 min, give up after 6 with a warning exception). Kill switch `call_gate_enabled=false`.
Verification: 7 unit failures = the usual pre-existing set; new tests pass (rules, outage, block effects, job hook). Dry-run against all 127 pending call jobs: 28 would be blocked (all tag `do not call again`), 99 allowed, 0 lookup failures. Then ran the live block path on those 28 while calls were still paused: 28 jobs cancelled, 28 `call_blocked_ghl_optout` audit rows, 28 leads set `do_not_call`; 99 call jobs remained. Un-paused at 00:46 UTC (19:46 CT Oct 3), `outbound_campaigns_paused=false`. First real call after resume being watched. Still open: GHL workflow tag->DND, reconcile tagged contacts without DND, staff-facing "tag vs DND" note + dashboard banner, the 'free' wording question, employment-rate rule in code, corrected-prompt email to Ali, DND test on Kes's contact.
- 2026-10-03 19:50 CT: after the resume the 9 call jobs that came due (19:46-19:50) were NOT dialed: the campaign active-window check (Cold Lead runs Mon-Fri by default) cancelled and rescheduled each to Mon Oct 5 09:01-09:38 CT, so the first real calls with the gate are Monday morning; 0 gate lookup failures, 0 active alerts. Employment/placement/job-guarantee terms added to the forbidden-terms default (`517af2d`, deployed). Draft prompt email to Ali created in Gmail (not sent; includes the "free" question).

### 2026-10-03 20:43 CT - first live test of the call gate and per-channel DND (Kes's own contact)
Contact `nggGZQByw53cbL8ws9Xg` (Kes Del, +15714782790, kesetebirhan@gmail.com), Call DND ON in GHL, Email/SMS DND off. At 01:43:31 UTC a Cold Lead call job (`source dnd_test_call_gate`) was queued and the email trigger fields (Support Ticket #2 + Message) were written at the same moment. Result: the call was cancelled by the gate at 01:43:38 (`call_blocked_ghl_optout`, reason "GHL Call do-not-disturb is active"); the GHL workflow sent the email at 01:43:32 and GHL's email status is `delivered`. Proves: the gate reads Call DND live; Call DND does not block email (per-channel). NOT proven yet: GHL blocking an email when Email DND is on, a staff send, a bulk send, or the Conversation AI replying to a STOP - next run needs Email (and SMS) DND set on the same contact.

### 2026-10-03 night - GHL Voice AI call placed despite Call DND (observation from Kes's test contact)
Kes set Call DND on his own contact at 01:40 UTC. The "Cora Outbound - New Leads" workflow ran at 8:56 PM CDT (all steps Executed, webhook to Cora accepted 202; Cora skipped entry because the contact is tagged enrolled student; no Cora call job). At 01:59:36 UTC a GHL **Voice AI** outbound call (`subType VOICE_AI`, from +19729921024, 101 s, completed) was placed to the contact - from a custom GHL outbound voice agent (not Synthflow, not Cora's call job, so the Cora call gate was not in its path; earlier VOICE_AI calls to the same contact on Oct 2 at 18:05 and 23:10 UTC). Conclusions: (1) GHL Call DND blocks the manual dialer (tooltip) but did not stop a workflow-launched Voice AI call; (2) DND does not stop a workflow's non-messaging actions (webhook ran); (3) real protection has to be an If/Else gate inside each GHL workflow before any Voice AI/call action (exit on Call DND or opt-out tag) plus Cora's own gate for Cora-launched calls. Open: find which GHL workflow/action launched the 8:59 PM CDT Voice AI call and add the gate; retest. Kit KIT.md section 8 item 5 updated (project-neutral).

### 2026-10-03 21:33 CT - DND test 2 (Call DND and Email DND both ON, all tags removed from the test contact)
At 02:33:35 UTC: Cold Lead call job queued + email trigger fields written for Kes's contact. Call: cancelled by the gate at 02:33:41 (`call_blocked_ghl_optout`, "GHL Call do-not-disturb is active"). Email: NO outbound email appeared in the GHL conversation within ~50 s (the earlier control email, DND off, appeared within 1 s); only the "DnD enabled by user" activity - consistent with GHL's workflow email action skipping a contact with Email DND on. To confirm: Kes's inbox shows nothing and the email workflow's execution log for the contact shows the send skipped/blocked. The gate again set Cora do_not_call/closed on the test lead; reset (audit `test_artifact_reset`) so the New Lead workflow test can reach the gate. Staff note on tag vs DND deferred by Kes until bulk-message DND behaviour is explored.

### 2026-10-03 21:41 CT - end-to-end New Lead workflow test, Call DND on (Kes's contact, tags removed): PASS
GHL "Cora Outbound - New Leads" workflow ran 9:41:49-9:41:56 PM CDT (all steps Executed). Cora: webhook POST /v1/webhooks/leads/new_lead 202 at 02:41:55 UTC -> `launch_outbound_call` job created (`campaign_entry:new_lead`, run 02:42:30) -> picked up 02:42:41 -> the call gate cancelled it at 02:42:42 (`call_blocked_ghl_optout`, "GHL Call do-not-disturb is active"). No call event, no exception, no call or message on the contact in GHL afterwards. Side effect (by design): the test lead is now closed / do_not_call in Cora; clear it before any real-call test with DND off.

### OPEN TO-DO as of 2026-10-03 night (supersedes earlier open lists)
**Kes:** (1) send the prompt/DND reply to Ali (drafts in Gmail; DND text updated, tag->DND workflow line removed on purpose: Cora handles its own sends, bulk workflows are what need fixing); (2) get Ali's answer on the "free" wording / TWC scope; (3) fix GHL bulk-sending workflows (If/Else at the start: exit on the right DND or opt-out tag) and decide staff guidance; (4) set the "DND Tag Workflow" (adds "Non Marketing Contact") to Draft until its "Disabled" filter and the tag's purpose are confirmed; (5) find which GHL workflow/action launched the Voice AI call at 8:59 PM CDT Oct 3 (unexplained; Voice AI STOP handling left as is); (6) remaining DND tests needing hands: staff email with Email DND on, bulk email, STOP reply (needs SMS DND + phone); (7) GHL assistant: Auto-Pilot channel scope, detach Feb 2026 knowledge base; (8) Ali items to chase: Kehyonnah Clark correction, the five contacted later, who sent the 14:26 blast, cc Ali on system alerts?, his inbox rule live -> drill; (9) spec/38 decisions D1-D5; (10) test contact: to test a real call turn Call DND off and tell Claude to clear the Cora flag first.
**Claude:** (1) email-channel gate: read GHL tags / Email DND before Cora's follow-up emails [awaiting Kes go]; (2) daily check of the GHL assistant's outgoing text for a week (retired terms, employment claims, unanswered opt-outs) [promised to Ali]; (3) campaign-entry skip logging to audit_log; (4) after Ali answers: replace "free" everywhere + add to forbidden terms; (5) send the alert drill when Ali's rule is live; (6) run the remaining DND tests with Kes (watch logs); (7) Monday: review first gated calls (blocked count, lookup failures); (8) course-change checklist/runbook from the spec/38 register [unblocked]; spec/38 phases 2-6 after decisions; (9) shorten second-attempt text to 1 segment (14% are 2); (10) make 4 time-of-day unit tests time-independent; (11) investigate slow call logs (about 1 in 5 texts late); (12) staff note + Opt-outs banner [on hold until Kes finishes the bulk-message exploration].

### 2026-10-04 (early) - Claude's assignments done and deployed (`7b89f5d`); REMAINING TASKS as of this entry (supersedes the Oct 3 to-do list)
**Done and live:** (1) email follow-ups now obey GHL opt-outs like texts (DND all/Email, opt-out tags, no email address, unreadable record = fail closed; skip removes Ticket #2 + Message); (2) content audit of everything GHL sends: `channel_events kind=content_flag` for retired-course / employment-claim wording and for opt-out replies answered with a sales message, one `ghl_content_flag` alert email while any exist in 24 h (proven on the production DB inside a rolled-back transaction; no real traffic yet); (3) every campaign-entry skip writes `audit_log campaign_entry_skipped` with its reason; (4) missed-call text is one segment always (3 attempts, ~150-char fallback; the old fallback was itself 162-167 chars); (5) `directives/runbooks/course-change.md` (checklist built from the spec/38 register); (6) 4 time-of-day unit tests pinned open (suite now shows only the 7 old failures: test_admin_routes x2, test_enrolled_intent, test_ghl_adapter x2, test_inbound_call_processing x2); (7) earlier today: call gate, employment-claim ban, headers + drill command, employment terms, DND tests 1-3 (call gate blocks from queued job and from the full New Lead workflow; Email DND on = no email).
**Root cause of late missed-call texts (investigation, no code changed):** Synthflow's first webhook for ~1 in 5 calls arrives ~11 min after the call with Status "in-progress", duration 0 (not terminal). Cora cannot run the voicemail tier on it. `auto_webhook_recovery` (runs every 5 min) skips call rows younger than `_MIN_AGE_MINUTES = 20` (`webhook_recovery_jobs.py:38`, measured from when the row was created, i.e. when the late webhook arrived), then fetches the final state ("hangup_on_voicemail") and the voicemail tier + text run 20-25 min later, i.e. 32-36 min after the call started. Call starts are fine (median 0.4 min after launch) and webhook arrival is a steady 12-18 min. PROPOSED FIX (needs Kes's go; touches call-event processing): in recovery, use min age 5 min since row creation AND 10 min since the call's own start, so a non-terminal row for a call that began >10 min ago is resolved within ~5 min instead of ~20.
**Kes:** send the Ali reply (prompt draft + updated DND text); get Ali's answer on "free"/TWC; fix GHL bulk-sending workflows (If/Else gate on DND/tags) and decide staff guidance; set "DND Tag Workflow" to Draft until its filter and the "Non Marketing Contact" tag's purpose are confirmed; find what launched the 8:59 PM CDT Voice AI call (Voice AI STOP handling left as is); remaining DND tests needing hands (staff email, bulk email, STOP reply with SMS DND); GHL assistant channel scope + detach the Feb 2026 knowledge base; chase Ali (Kehyonnah Clark correction, the five contacted later, who sent the 14:26 blast, cc Ali on system alerts?, inbox rule live -> drill); spec/38 decisions D1-D5; to test a real call turn Call DND off and ask Claude to clear the test lead's Cora do_not_call flag first.
**Claude:** replace "free" everywhere + forbid the word once Ali answers; send the alert drill when Ali's rule is live; Monday: review first gated calls (blocked count, lookup failures) and the first content flags; apply the stale-recovery fix above if approved; spec/38 phases 2-6 after decisions; staff note + Opt-outs banner (on hold until bulk-message exploration is done); run remaining DND tests with Kes (watch logs).

### 2026-10-04 - INCIDENT: stale Open House email from a GHL workflow; ACTION PLAN (Ali approved, replaces the "free"/TWC open question)
**What happened:** at 10:54 AM CDT a GHL workflow emailed an old "Virtual Career Switch Open House" message (Colaberry School of Data Science & Analytics, "Data Analytics", "Explore Financing Options") to one cold lead. Caught by the new `ghl_content_flag` check on its first day with real traffic (the only flag so far). Not sent by Cora. Same root cause as the Conversation AI prompt: the offer is hand-written in places nobody lists. Ali: three instances of one root cause is a pattern; the list of places is the fix, keep adding to it.
**Ali's decisions (2026-10-04):** (a) suspend both workflows now; (b) report how many leads they already emailed and over what period (decides cleanup vs. telling people); (c) the ONLY goal of these workflows is to get the prospect to create an account (not enrolment, a call or financing); short, one action; (d) "free" is banned as a standalone claim including "free account" (TWC); use the site wording: "$0 to start", "No card needed", "Membership from $149/mo"; (e) standing bans carry over: no employment rate, scholarship, financing claim, retired course name, accreditation claim; (f) bring rewritten text to Ali before anything is re-enabled.
**Workflows to fix (carry banned content):** 1. School Catalog Request Flow (sent the flagged email), 2. Cold Initial Outreach Email Workflow (old information), 3. Open House Registration Confirmation & Reminders (added by Kes from a screenshot; contains banned info). The contact's past-workflows list also shows New Leads Workflow, Left Voicemail Tag Automation, AI Agent - Send SMS, Cora Outbound - Cold Leads; read those in the sweep.
**KES (in GHL):**
1. Set School Catalog Request Flow and Cold Initial Outreach Email Workflow to Draft NOW; also set Open House Registration Confirmation & Reminders to Draft unless it is needed for people already registered (decide; if kept, send Claude its text first). Confirm to Claude when done.
2. Before drafting, note each workflow's execution count and date range if GHL shows it (Execution Logs); also check whether any workflow is still sending the old Open House email (Ayana Coleman's log around 10:54 AM CDT).
3. Paste or export the text of every email and SMS step in the three workflows (or give Claude the template names) so Claude can list what is wrong. Claude cannot read workflow steps through the API.
4. Review and send Claude's rewrite proposals to Ali; re-enable each workflow only after Ali's written approval.
5. Send the draft to Ali that asks the earlier questions only if still needed; the "free" question is answered. Still open from before: GHL assistant channel scope, detach the Feb 2026 knowledge base, the 8:59 PM Voice AI call, remaining DND tests, bulk-send If/Else gate, set "DND Tag Workflow" to Draft.
**CLAUDE:**
1. Count the leads emailed by these workflows and the period: from GHL conversations (outbound, source "workflow", matching the old Open House text) and the `channel_events` table; report the number and date range to Kes for Ali (no contact data in the email).
2. Review every step Kes provides; list each wrong item (retired course, financing, "free", employment, scholarship, accreditation) with a proposed replacement.
3. Draft the new messages: one action (create an account), short, with "$0 to start / No card needed / Membership from $149/mo"; bring to Kes, then Ali, before re-enable. SMS single segment.
4. Sweep the other GHL workflows, templates and snippets for the same wording using `directives/runbooks/course-change.md` section 2; add every new place found to the register in `directives/spec/38_offer_single_source_roadmap.md` and the runbook.
5. Unblocked now that Ali answered: replace "free" in Cora's own text (app/core/offer.py facts, templates, prompts, the Conversation AI prompt copy in docs/) with the site wording and add "free" as a forbidden term in `offer_forbidden_terms`; the new content check then catches it in what GHL sends. Note "Explorer is free" appears in docs/ghl-conversation-ai-cora-prompt.md and must change in GHL too (Kes pastes).
6. Fix the false match: "excel" in the forbidden terms matched the phrase "skills needed to excel"; match it only in the context of the Excel program (or drop it). Add a test.
7. Reply to Ali with the lead count once it is known.

### 2026-10-05 - alert drill confirmed; alert fixes deployed
- SMS health drill sent 12:11 PM EDT and confirmed received (to Kes, cc Ali, in the inbox, `X-Cora-Alert` rule live on Ali's side). Ali asked to be told the channel: SMS. Ali is now copied on every generic system alert via `ALERT_EMAIL_CC` (set on the server `.env`; not copied on to_override sends such as sales-queue). Done items removed from the to-do: "send the drill", "cc Ali on system alerts".
- `1338828`: content-flag alert fires once per flagged message (message id stored in the alert), names the lead (name, phone, email), dashboard Resolve button + `resolve-alert` endpoint. Cause of the repeat alerts: Acknowledge set status `acknowledged`, the check only looked for `active` and raised a new one.
- Calls silence alert fixed: silence is counted on Cold Lead active days only (Mon-Fri), so the weekend no longer raises "Calls have gone quiet" (false alarm Mon Oct 5 8:01 AM CDT; calls were running by 8 AM).
- Still open from the Oct 4 incident plan: suspend School Catalog Request Flow, Cold Initial Outreach Email Workflow (and decide on Open House Registration Confirmation & Reminders); send workflow text; lead count; rewrites to Ali; replace "free"; fix the "excel" false match.

### 2026-10-05 - spec/38 decisions D1-D4 answered by Kes (D5 still open)
D1 prices only when the lead asks; D2 main aim $0 start via myfreeaiclass.com, offer self-paced vs Open House (live) when asked, same on voice/email/assistant; D3 yes, "ready to enroll" gets the training.colaberry.com link in follow-ups; D4 Nov 12 class start is real (keep setting). Recorded in `directives/spec/38_offer_single_source_roadmap.md` section 4. Open: whether the myfreeaiclass.com domain name needs special treatment under Ali's TWC ruling on "free"; D5 (which GHL snippets / knowledge bases are in use). Implementation (voice/email/assistant wording, text link for D3) waits on Ali's approval of the rewritten text and spec 38 phase 2.

### 2026-10-12 - content audit: interview marker, LLM decline judge, company-name exclusion (deployed)
Three false-positive classes of `ghl_content_flag` fixed; all deployed to Hetzner (`/opt/cora-recap-engine` at `90c35cd`, `docker compose up -d --build`, containers healthy, logs clean; migrate ran with no schema change).
- **Interview notification** (alert for Kes Del, a workflow SMS naming a Data Analyst job): a message whose visible text (HTML stripped) STARTS with `[Interview Notification]` (`app_config` `interview_marker`; empty disables) is not flagged for course-name hits. The GHL interview workflow must send that marker as the first characters / first line of the body. Email subject is not scanned; calls are never scanned.
- **Correct refusal** (Olu Omidiji, Conversation AI said it no longer enrolls in the Data Analytics bootcamp): when every hit is a course name, `app/services/content_judge.py` asks `openai_model_content_audit` (gpt-4o-mini) whether the reply only declines it. Only `declines` removes the flag; `offers`, `unclear`, error, bad JSON, `content_llm_enabled=false` or exhausted budget (shared with the opt-out judge, 8 per sync run) leave it flagged. Verdict cached in memory per message id; non-declines verdict stored in `detail.llm_verdict`. No temperature pin (the OpenAI adapter has no temperature parameter).
- **Company name in a signature** (Claudia Zapata, staff email signed "Colaberry School of Data Analytics"): `app_config` `brand_phrases` (default "Colaberry School of Data Analytics,School of Data Analytics") is removed before the scan; a course named beside it is still flagged.
- Employment / placement claims are never exempted and never sent to the LLM. The audit reads each message's own body only (no thread history).
- Tests: `tests/unit/test_content_audit_exemptions.py` (31). Full unit suite: 8 failures that predate this work (test_admin_routes x2, test_cadence_fixes, test_enrolled_intent, test_ghl_adapter x2, test_inbound_call_processing x2).
- Spec: `directives/spec/35_delivery_health.md` (Exemptions, Company name). Commits `d2c114c`, `90c35cd` on `feat/ghl-call-conversation-sync`.
- **Open:** (1) not yet verified live end to end (first real refusal / interview text is the first judge call); (2) Ali has not signed off on loosening his 2026-10-03 rule; (3) resolve the existing Claudia Zapata alert by hand; (4) the "excel" false match ("skills needed to excel") from the Oct 5 list is still not fixed; (5) `main` has one commit not on the production branch (534124e, merge of PR #2): not investigated; (6) the interview call must come from the GHL Voice AI agent on (972) 992-1024, not Cora's Synthflow line +19729921028 - check the candidate is not in a Cora campaign.

### 2026-10-13 - outbound calls RESUMED after the Oct 6-8 Synthflow/Twilio outage; no code changes
**Timeline (from `call_events`, `scheduled_jobs`, `audit_log`):** Oct 5 healthy (222 voicemails detected, 19 pickups). Oct 6 degrading (42 failed, 37 no-answer). Oct 7-8: 375 calls to 372 leads, none lasted 20 s (all failed / no-answer, ~5 s). Oct 8 14:46 CDT Kes set `outbound_campaigns_paused=true` ("Synthflow outbound calls failing since Oct 6; support ticket sent"). Twilio was out of funds (warned Oct 6 to the techadmin mailbox; Ali confirmed funded Oct 13). Texts via GHL were never affected (262 delivered Oct 5, 36-54/day after; only normal 30003/30005/30006/21211 errors, no 30007 carrier filtering).
**Resume (Kes request, 14:23 CDT / 19:23 UTC):** `outbound_campaigns_paused=false` via `set_config_value` (audit `config_updated`, updated_by "kes-request: resume outbound calls, Twilio funded + test call OK"). `updated_by` is varchar(100): a longer note rolled back the first attempt. Pre-checks: 0 pending call jobs, 0 `nurture` leads due, calls spaced on the 75 s grid, opt-out gate live, inside the Cold Lead window (Mon-Fri 08-22). Kes's own Synthflow test call connected earlier.
**Decision (Kes): new entries only.** 750 leads that hit campaign entry while paused (Oct 8-13, audit `campaign_entry_skipped` reason `outbound_campaigns_paused`) are NOT replayed; the 372 leads burned by dead-line calls are NOT retried. Nothing re-enters them automatically.
**Verified after resume (14:31-14:53 CDT):** 18 call jobs for 15 leads, 12 completed, 2 cancelled by the opt-out gate (tag "do not call again"), 4 pending second attempts (~2 h later, within the 2/day cap). First results: 4 `hangup_on_voicemail` (35-59 s), 2 `in-progress` (webhook lag ~11 min). 0 exceptions, 0 gate lookup failures. Staff-contact check (GHL conversations, each lead's own entry time as cutoff): no staff-sent calls/texts/emails; two leads got one automated `workflow` SMS each (inferred automated, source `workflow`). Kes's own contact has a callback job at 16:24 CDT.
**Daily cap:** `max_calls_per_lead_per_day` is not in `app_config`, so the code default 2 applies (`call_policy.DEFAULT_MAX_CALLS_PER_DAY`, `outbound_jobs.daily_cap_deferral`); lead-requested callbacks exempt. No lead exceeded 2 launched calls in a local day in the last 14 days. GAP: the cap counts only Cora-launched jobs; two New Lead calls (Oct 9 10:16, Oct 12 13:41 CDT) had no Cora launch job (origin unknown) and GHL Voice AI calls are not counted. To make it 1: set `max_calls_per_lead_per_day=1` (config only).
**Inbound:** there is no inbound pause flag (the pause held New Lead + Cold Lead only). Zero inbound rows in `call_events` since 2026-09-01: inbound is not reaching Cora's webhook or moved (possibly GHL Voice AI). Not investigated.
**Ali's 2026-10-13 review (content flag change approved with conditions):** (1) the `[Interview Notification]` prefix must not become the permanent mechanism - gate on the source workflow; NOT built, needs Kes's go. (2) replay the other flags against the new rule: done read-only on all 7 stored flags (the 6 flag count in Ali's note vs 7 rows here: row 1 is the Oct 4 stale Open House email). Result: stale Open House email stays flagged (judge `unclear` 8/8); the true decline (Olu Omidiji) clears 8/8; the signature clears by brand strip. FINDING: the two mock interview EMAILS (not marker-prefixed) were cleared by the LLM judge as `declines` in 4 of 8 runs - the unsubscribe footer's "no longer wish to receive these emails" reads as a decline - while the same text as SMS came back `unclear`. Cleared for the wrong reason; the judge is not deterministic on ambiguous text. No real violation was cleared in the replay, but the rule is looser than intended. PROPOSED (not built): strip unsubscribe/footer boilerplate before the judge, require 2 of 2 `declines` verdicts, and move the interview exemption to a source gate. (3) Twilio recovery: calls connect again (voicemail detections 35-59 s); human-pickup rate and carrier spam-labelling cannot be judged from 6 calls - recheck after ~50 calls. Cora holds no Twilio credentials, so Twilio itself cannot be queried from here. (4) separate issue (Ali): Twilio's Oct 6 Action Required + 2 error alarms went to the techadmin mailbox nobody reads; candidate fix is a Twilio Usage Trigger / Monitor Alerts webhook into the dashboard alert system.
**Open:** recheck first ~50 calls (connect/pickup rate); decide the content-judge tightening and the source-gated interview exemption; decide cap 1 vs 2; find what launched the two non-Cora New Lead calls; investigate inbound silence; resolve existing alerts by hand (Claudia Zapata); the "excel" false match ("skills needed to excel") still unfixed.

### 2026-10-13 (later) - content judge hardened on `fix/content-audit-source-gate-and-judge` (built, tested, deployed to Hetzner 2026-10-13 - see the deploy note below)
Ali's condition 2 (replay the other flags; tighten if too loose). Files: `app/core/content_audit.py` (`strip_footer`), `app/services/content_judge.py` (2 votes, all must say declines; `has_decline_phrase` guard), `app/services/delivery_sync.py` (judge reads footer-stripped text), `tests/unit/test_content_audit_exemptions.py` (31 -> 55), `directives/spec/35_delivery_health.md`. Live replay of the 7 stored flags with the new code, read-only (modules loaded from /tmp in the container, removed afterwards): stale Open House email flagged 8/8, 4 mock interview tests flagged 8/8 (the real ones carry the marker), true refusal cleared 8/8, signature cleared by the brand strip. Before the change two mock emails were cleared 4/8 and 8/8 by the model alone. Full unit suite: 8 failures, the same pre-existing 8; ruff clean. NOT BUILT: Ali's condition 1 (source gate instead of the text marker) - GHL message metadata carries no workflow id; needs Kes to pick a GHL-side identity (dedicated sender number/email or a contact tag written by the interview workflow). Confidence-tiered alerts (high = email, doubtful = dashboard only, daily digest) were discussed and deliberately left out.

### 2026-10-13 16:30 CDT - judge hardening DEPLOYED (`7795338`), Hetzner rebuilt
Merged only commit `7795338` (fast-forward) into `feat/ghl-call-conversation-sync`, pushed, then on Hetzner `git pull && docker compose up -d --build` (migrate ran, no schema change). Verified after the restart: server at `7795338`; new code in the image (`JUDGE_VOTES=2`, `has_decline_phrase`, `strip_footer`); no errors in api/worker-default logs; `outbound_campaigns_paused`, `cold_lead_campaign_paused`, `system_paused`, `shadow_mode_enabled` all false; 5 call jobs completed in the 20 minutes after, 0 exceptions. The `[Interview Notification]` exemption is STILL ACTIVE in production. The removal commit `5b7afa2` stays on `fix/content-audit-source-gate-and-judge`, not merged, until Ali answers (draft question sent to him as a Gmail reply; Ali condition 1). When merging it expect a trivial PROGRESS.md conflict (both sides append at the end). Process note: the auto-mode classifier blocked the first push to the shared branch ("out-of-place publication"); Kes authorised it, and four exact allow rules were added to the gitignored `.claude/settings.local.json` (merge of `7795338`, push of this branch, the two ssh deploy/status commands). Next: recheck call connect/pickup rate on a full day of calls (Ali asked whether Twilio recovery is real or masks a carrier block); wait for Ali on the exemption.

### 2026-10-13 (evening) - Ali's Oct 4 "stale Open House email" reply re-read; status of each ask (nothing new built)
Kes re-pasted Ali's 2026-10-04 message (suspend School Catalog Request Flow + Cold Initial Outreach Email Workflow; read every step; lead count and period; one goal = create an account; no standalone "free" incl. "free account" (TWC); use "$0 to start / No card needed / Membership from $149/mo"; standing bans: employment rate, scholarship, financing claim, retired course name, accreditation claim; bring rewritten text to Ali before re-enabling; keep the register of every place the offer lives). It is already recorded above in the 2026-10-04 incident entry. Status as of today:
- **Suspend the two workflows (Kes, in GHL):** UNKNOWN from Cora's side (Cora cannot read workflow state). Evidence only: no stale-wording content flag from a workflow since Oct 4 (the only flagged workflow messages after it were the Oct 12 mock interview tests). Ask Kes to confirm both are Draft; also decide Open House Registration Confirmation & Reminders.
- **Lead count and period for those two workflows (Claude):** NOT DONE. Needs a GHL conversation scan for the catalog subject ("Here is your requested catalog from Colaberry School!") and the Cold Initial Outreach email; `channel_events` only holds ~72 h of deliveries and no subject, so it cannot answer this. Roughly 6,800 contacts; at the 5 req/s GHL limiter a read-only scan is ~25-30 min. Report number + date range only, no contact data.
- **Workflow step text (Kes) and wrong-wording list with replacements (Claude):** NOT DONE; Claude cannot read workflow steps through the API. Needs Kes to paste/export the steps.
- **Rewrites for Ali's approval (Claude):** NOT DONE (blocked on the step text). Nothing is to be re-enabled before Ali approves in writing.
- **Replace "free" in Cora's own text (Claude, Oct 4 item 5):** NOT DONE and LIVE. `app_config` has no `offer_facts` / `offer_forbidden_terms` / `offer_name` override, so the code defaults run in production: `app/core/offer.py` DEFAULT_OFFER_FACTS ("anyone can start free at {free_url}", "the free Open House"); `app/prompts/families/vm_followup_generator.py` (lines ~46, 67, 94, 103: "start free", "the free start link", "Free start:"); `app/services/wrong_date_monitor.py` (~360 text, ~412 email: "start learning for free"). The voicemail follow-up generator writes real emails/texts from these. "free" is not in `DEFAULT_FORBIDDEN_TERMS`, so the content audit would not flag it either. Also check `docs/ghl-conversation-ai-cora-prompt.md` ("Explorer is free") and the GHL-side prompt (Kes pastes).
- **Fix the "excel" false match ("skills needed to excel"):** NOT DONE (`excel` is a bare term in DEFAULT_FORBIDDEN_TERMS; match only the Excel program or drop it).
- **Keep the register of places the offer lives (spec/38, runbook):** add the three Cora files above if not already listed, plus both workflows and any others found in the sweep.
Needs Kes's go before building: (1) the "free" replacement in Cora's code (touches live customer-facing text; propose "$0 to start", "No card needed", "Membership from $149/mo", add "free" to forbidden terms with a test that the standalone word is flagged but e.g. "freedom" is not); (2) the GHL lead-count scan; (3) the "excel" fix. Needs Kes in GHL: confirm both workflows are Draft and paste the step text.

### 2026-10-13 (night) - interview-notification exemption REMOVED (Ali condition 1 closed by deletion) on `fix/content-audit-source-gate-and-judge` - DEPLOYED 2026-10-14 (see the note below)
Inbox manager session (code-checked, not memory): SMS and email notices are built by `execution/notification_messages.py` from employer, date, time, format and first name only; the position is always the fixed phrase "for the position you applied for"; `safe_employer()` swaps an employer name containing a flagged term for "the employer". Only the VOICE call names the position (`interview_facts()` -> "Position: ...") and Cora never scans calls. An approver-edited email draft is sent as written, but InboxGenius's send-time `audit_problem` blocks any listed title (an unlisted title such as "Nursing Assistant" would pass both audits and is not flagged by Cora anyway). So Cora's `[Interview Notification]` exemption never fires on real traffic; the only flags it ever saw were the 4 mock tests of Oct 12. Change: removed `DEFAULT_INTERVIEW_MARKER`, `is_interview_notice` and the branch in `delivery_sync._flag_content`; `interview_marker` config key was never set in production; tests rewritten (a marker-prefixed or marketing text naming a listed term is flagged; a notice with no listed term is not; employment claim never reaches the judge); spec/35 updated. The signed-proof design (inbox manager repo, `cora-interview-notice-source-gate-proposal.md`) is parked: revisit only if a notice must ever name a title. Needs Ali's okay before deploy (it changes the rule he approved). Full unit suite: 1763 passed, 8 failed (the same pre-existing 8); ruff clean. Committed on the branch as its own commit; deliberately NOT merged or deployed until Ali answers (his 2026-10-13 condition 1 asked for a source gate; this deletes the exemption instead).

### 2026-10-14 08:55 CDT - interview exemption removal DEPLOYED (`cee087b`); Ali's three answers recorded
Ali (2026-10-14) answered the Gmail question: (2) DELETING THE INTERVIEW EXEMPTION: YES - "no token to spoof rather than a token we have to authenticate"; keep the signed-proof design parked; if a real notice trips a flag it is one alert like any other. (3) THE JUDGE HARDENING: DEPLOY IT - happy with both runs having to agree, the plain code check, and footer stripping before the judge; "8 of 8 stability is enough" (it had been live since 2026-10-13 16:30 CDT, `7795338`). Merged `5b7afa2` into `feat/ghl-call-conversation-sync` (PROGRESS.md conflict resolved keeping both sides), pushed `cee087b`, Hetzner `git pull && docker compose up -d --build`. Verified: server at `cee087b`; image has no `is_interview_notice`, `JUDGE_VOTES=2`, `strip_footer` present; no errors in api/worker logs; `outbound_campaigns_paused` / `cold_lead_campaign_paused` / `system_paused` / `shadow_mode_enabled` all false; 0 exceptions; 7 call jobs completed in the hour after. Full unit suite: 1763 passed, the same 8 pre-existing failures; ruff clean. Ali's conditions 1 and 2 on the content-flag change are closed. Still open (Ali Oct 4 list): the "free" wording in Cora's own text, the "excel" false match, the GHL lead-count scan, workflow step text and rewrites (see the 2026-10-13 evening entry); full-day call connect/pickup recheck for Ali's Twilio-vs-carrier question.

- **2026-10-14 08:58 CDT - Twilio-vs-carrier recheck (Ali's question), outbound `call_events`:** Oct 5 baseline (healthy day): 252 calls, 7.1% not connected (failed / no-answer / busy), 7.9% human conversations, 214 voicemails, 6 failed. Oct 13 from the 14:23 CDT restart to midnight: 125 calls, 12.8% not connected, 12.8% human conversations (16), 93 voicemails, 5 failed. Oct 14 so far (7 calls): 2 human, 2 voicemails, 3 not connected - too few to read. Reading: connectivity is restored and human pickup is at least the baseline, which argues against carrier spam-labelling (a flagged number shows low pickup / high failure); `failed` is 4.0% vs 2.4% on Oct 5 but 5 vs 6 calls is noise. Caveats: one partial day, 125 vs 252 calls (the 750 skipped leads are not replayed), no Twilio credentials in Cora so Twilio itself is unchecked. Recheck again after ~3 full days of calls.

### 2026-10-14 - "free" wording + "excel" false match fixed in code (branch `fix/replace-free-wording-and-excel-match`, merged as `1cd4b8b`) - DEPLOYED 2026-10-14, enforcement of "free" STAGED OFF
Ali's Oct 4 asks #5 and #6. Kes: fix 1 and 2, he updates all live GHL workflow scripts / Conversation AI prompts / knowledge bases; ignore the lead-count scan and the workflow rewrites (items 3 and 4 dropped). Changes: `app/core/offer.py` (DEFAULT_OFFER_FACTS now "$0 to start, no card needed, membership from $149/mo only if asked" + a "never say free" sentence; `free` added to DEFAULT_FORBIDDEN_TERMS; `forbidden_hits` gets per-term exceptions: "feel free"/"free time" not flagged, and the verb "excel" ("to excel", "you will excel", "who excel"...) not flagged while "learn Excel"/"Microsoft Excel"/"Excel course" still are); `app/core/content_audit.py` (`is_hard_term`: "free" never goes to the decline judge); `app/prompts/families/vm_followup_generator.py` (4 "free" phrases replaced, new hard rule: never "free", $149/mo only if asked, no scholarship/financing/employment-rate/accreditation); `app/core/schedule_context.py` (the schedule override told the model to "start learning for FREE"); `app/services/wrong_date_monitor.py` (correction text + email); `sms_gate.py` / `ai_message_generator.py` (rejection reason now "uses banned wording ..."). Tests: new `tests/unit/test_offer_terms.py` (matcher both ways, hard term, Cora's own text) + 2 in `test_content_audit_exemptions.py` + 3 reason-text assertions and the prompt scrubber in `test_sms_notice.py`. Full suite 1797 passed, the same 8 pre-existing failures; ruff clean. Spec/35 updated.
**Deploy risk found (read-only estimate on the 202 leads with deliveries in the last 7 days):** of 602 outbound GHL texts/emails, 251 would be flagged for "free" (GHL `app` source: 127 emails + 115 texts, i.e. the Conversation AI / staff; `workflow` source: 9 emails), on 136 leads, ~60-90 per day on Oct 9, 12, 13. Each flag emails an alert, so deploying the new default now floods Kes and Ali until GHL is cleaned. Staged option: set `app_config.offer_forbidden_terms` to the default list without `free` at deploy, delete the row when GHL is updated (the override replaces the list; it is never merged).
**Remaining places that still say "free" (not code, Kes updates):** docs copies and the live systems they feed - `docs/ghl-conversation-ai-cora-prompt.md` (5 lines: "start free on the Explorer plan", "Explorer is free", "To explore for free", "free Explorer plan"), `docs/colaberry-knowledge-base.md` (8 lines incl. plan name "Explorer (Free)"), `docs/sales-call-script.md` (5), `docs/support-call-script.md` (1, "Free preview"), `docs/synthflow-cold-lead-prompt.md` (16), `docs/synthflow-warm-lead-prompt.md` (12), `docs/synthflow-inbound-prompt.md` (9) - the three Synthflow voice prompts are pasted into Synthflow (not scanned by Cora; calls are never scanned); GHL: Conversation AI "Cora" prompt + Feb 2026 knowledge base (to detach), School Catalog Request Flow, Cold Initial Outreach Email Workflow, Open House Registration Confirmation & Reminders, New Leads Workflow, AI Agent - Send SMS, other templates/snippets; the GHL voice AI agent (972) 992-1024; the training site plan name "Explorer (Free)" and the domain myfreeaiclass.com (spec/38 D5, Ali's call whether the domain itself needs treatment). Internal docs only (no change needed): spec 37/38, runbook course-change.md.

### 2026-10-14 09:55 CDT - "free" / "excel" change DEPLOYED (`1cd4b8b`); the "free" ban is STAGED OFF on purpose
Kes: deploy, and he updates the rest of the GHL workflows / Conversation AI / knowledge bases / Synthflow prompts manually. Sequence: first set `app_config.offer_forbidden_terms` = the previous default list (21 terms, exactly the old default, no `free`; updated_by "kes-request: staged - old default list, no free, until GHL is clean"), then merged + pushed `1cd4b8b` and rebuilt Hetzner. Why: ~250 of 602 GHL texts/emails in the last 7 days contain "free" (Conversation AI / staff `app` source mostly), so enforcing it now would email an alert per message. Live now: Cora's own wording is fixed ($0 to start / no card needed; $149/mo only if the lead asks), the "excel" verb false match is gone, "free" can never be cleared by the decline judge. Verified on the server: image on `1cd4b8b`; effective terms = 21 and `free` not enforced; with the override deleted the default enforces `free` ("Start free today" -> hit, "feel free to call" -> no hit); "skills needed to excel" no hit, "learn Excel" hit; no log errors; after a full audit cycle 0 new content flags, 0 alerts, 0 exceptions, delivery sync healthy (0 errors), call jobs completing.
**TO TURN THE "FREE" BAN ON (do this when GHL is clean):** delete the override row so the code default applies: `DELETE FROM app_config WHERE key='offer_forbidden_terms';` (Claude can do it on request). Expect content-flag alerts for any GHL text/email that still says "free" from that moment; check first with a read-only count of the last 24 h. Do NOT leave the override long term: it replaces the default list, so future additions to `DEFAULT_FORBIDDEN_TERMS` would not apply while it exists.
Remaining places are listed in the entry above (GHL workflows, Conversation AI prompt + KB, three Synthflow prompts, call scripts, voice AI agent, plan name "Explorer (Free)", domain myfreeaiclass.com = spec/38 D5).

### 2026-10-14 (afternoon) - staff-recipient email exemption (AI Call Recap false positive) built on `fix/content-audit-skip-internal-recipients`
Incident: alert "6 new outbound messages ... found: power bi" = one GHL system email "AI Call Recap: <caller>" (source `app`, the AI Receptionist) sent to 6 staff/test contacts at 16:28 UTC; "Power BI" is the caller's own words. No external lead involved. Kes asked to deploy the fix so it cannot recur (the draft to Ali asking for approval was sent as a Gmail draft; Ali had not answered when Kes said to ship it). Change: `content_audit.parse_internal_domains` / `is_internal_email`; `delivery_sync._contact_email` + an early return in `_flag_content` for emails to staff domains (details in spec/35). Findings that shaped it: GHL email messages have NO recipient address (`to`/`from` None, `meta.email` has only direction/messageIds/subject), so the check reads the conversation contact's email. Tests: 23 new in `test_content_audit_exemptions.py` (73 in the file); full suite 1820 passed, the same 8 pre-existing failures; ruff clean. Also noticed: `docs/colaberry-knowledge-base.md` is modified in the working tree (Kes's manual KB edit) - left uncommitted on purpose.

### 2026-10-14 (afternoon) - staff-recipient email exemption DEPLOYED (`bbc4f76`)
Merged `fix/content-audit-skip-internal-recipients` (fast-forward), pushed, Hetzner `git pull && docker compose up -d --build`. Verified on the server with the production code against real data: the 6 contacts that received the "AI Call Recap" at 16:28 UTC all resolve to internal addresses (5 colaberry.com + 1 trillionsquare.com) -> skipped; 40 other recent email recipients all resolve to outside addresses -> still audited (0 wrongly exempted); containers up; no log errors. The existing active `ghl_content_flag` alert about those 6 messages is not removed by the deploy (the 6 flags stay in the 24 h window): click Resolve on the dashboard. Ali's approval of this exemption is still pending (Gmail draft sent for his okay); to undo it set `app_config.audit_internal_email_domains` to an empty string (disables the exemption; takes effect immediately). Also still open: the voice-agent call (alumnus asking about a Power BI resume) should be listened to for any accreditation / outcome claim.

### 2026-10-14 15:30 CDT - Ali: calls failing with Invalid Caller ID (+16822283242) - it is NOT the Cora agents (read-only investigation, nothing changed)
Ali (Oct 14 7:56 AM, 8:03 AM): Twilio records show Oct 14 112 calls / 13 completed / 99 failed (avg 2 s), twilio errors 11200 / 32204 Invalid Caller ID, every failing call from +16822283242 (owned=false, verified=false; the account owns 75 numbers); same fault documented 2026-09-10 (Telephony 403, caller ID ending 3242), hidden by the suspension; also `TWILIO_NUMBER` = +18885764480 not owned/verified; `synthflow_api` health stays green because the API returns 200 - needs a call-OUTCOME check. Ali says the Synthflow account is his, Kes has the credentials and may change the caller ID himself; pointing the agent at one of the 75 owned numbers is his preferred fix; do not spend time on spam labelling or A2P.
**Cora's own data disagrees for Cora's calls:** `call_events.raw_payload_json.phone_number_from` is +1...1028 for every Cora-launched call on Oct 5, 13 and 14 (Cold Lead and New Lead). Oct 14 (to 15:28 CDT): 185 calls, 163 connected (88%), 7 failed, avg 32 s - the same as the healthy Oct 5 baseline (252 calls, 234 connected, avg 33 s). Only 3 calls over Oct 13-14 have a blank from-number, all failed at 0 s. So the 99 failing 3242 calls are not Cora campaign calls.
**Synthflow assistants (read-only `GET /v2/assistants`, last 4 digits only):** the agent attached to ***3242 is **"ROI Architect"** (id ...4145db), not a Cora agent. Cora outbound agents: "Cora - Outbound Admissions Agent" (...60c90f) and "Colaberry Student Outreach" (...c7941d) are on ***1028; a second "Cora - Outbound Admissions Agent" (...5eaff5) and "Cora2 - Outbound Admissions Agent" (...bf5971) have NO number. Other numbers in the account: ***2224 (Cora Inbound), ***7060 (Edu Cory Inbound), ***0787 (Little Ali), ***3210 (Nexus_Outbound_Voice), ***2568 (Edu Cory Outbound). 15 assistants in total.
**Recommendation (Kes):** do NOT pause Cora's campaigns (they are healthy); confirm with Ali that the 3242 caller ID belongs to ROI Architect, then in the Synthflow dashboard point ROI Architect at one of the 75 owned numbers (Ali's preference) - Ali authorised Kes; ask Ali to run the owned/verified check on the other agent numbers (7060, 0787, 3210, 2568, 2224, 1028). `TWILIO_NUMBER` is not referenced anywhere in this repo (it is set in another service). Build (needs Kes's go): a call-outcome health check that reads Synthflow call results for ALL assistants (not just webhooks Cora receives): alert when, in the last N calls, the failed share is high with near-zero duration or "Invalid Caller ID / Telephony 403" appears. Caveat: Cora cannot see calls from agents that do not post to its webhook.
Also done today: committed Kes's manual KB edit (`docs/colaberry-knowledge-base.md`, 'free' replaced) as `d2e3ad4`.

### 2026-10-14 (evening) - judge rewritten: "does it OFFER a retired course?" decided by the LLM in context, no wording lists (`fix/judge-offer-vs-mention`)
Trigger: two alerts for Olu Omidiji (staff texts sent from the GHL app at 20:27 / 20:28 UTC explaining the move from Data Analytics to the AI program). The 13 Oct phrase guard (`has_decline_phrase`) downgraded text 1 to "unclear" although the model said it was a clear decline 6/6 ("transitioned from" was not in the phrase list). Kes (2026-10-14): stop fixing wording scenarios one by one; the keyword filter should be supplemented by an LLM that interprets the context and decides whether the message is offering an old course. Change: `content_judge` now asks one question - does the message OFFER a retired course? - verdicts `offers` / `not_offering` / `unclear` (old word `declines` still accepted); the guard and all phrase lists are gone; the matched terms are shown to the model; up to 3,000 characters are read (the stale Open House email keeps its offer past 800); unchanged fail-closed rules: two votes must both say `not_offering`, anything else (offers, unclear, split, error, budget) keeps the flag, employment / placement / "free" never reach the judge. NEW audit trail: every message the judge clears is stored as `channel_events` kind `content_ok` so it can be spot-checked (`SELECT event_at, contact_id, detail FROM channel_events WHERE kind='content_ok' ORDER BY event_at DESC;`). Evaluation with the real model, 8 runs each, 26 cases, 0 failures: the real stale Open House email (Oct 4) and 7 synthetic offers (bootcamp seats, decline-then-offer, "still available", curriculum list "covers Python, SQL and Power BI", cohort reply, Open House promo with financing, price/scholarship) were never cleared (0/8 each); 18 non-offers cleared 8/8 (Olu Oct 12 refusal, today's both texts, the 4 mock interview messages, the 6 AI Call Recap emails, plus a decline, a job title and a quoted caller). Unit suite 1815 passed, same 8 pre-existing failures; ruff clean. Trade-off to tell Ali: on 10-13 he praised "a plain code check means the model alone can never clear a message"; this reverses that part on Kes's instruction, keeping two agreeing votes, fail-closed behaviour, the hard terms excluded and the content_ok audit trail instead.
- **DEPLOYED 2026-10-14 ~18:20 CDT (`ec99830`)**, Hetzner rebuilt. Verified in the image: phrase guard gone, `JUDGE_VOTES=2`; the judge returns `not_offering` for the "transitioned from offering a standalone Data Analytics program" text and `offers` for "Our Data Analytics bootcamp starts Monday and a few seats are left"; containers up, no log errors. The two existing Olu Omidiji flags (and their alerts) are historical rows and stay until resolved by hand on the dashboard. Still open: tell Ali the judge no longer has the code-level check he praised on 10-13; correct the GHL Open House snippet ("$0 down", "career opportunities", "student success stories") - none of those are in the term list.

### 2026-10-14/15 (night) - ROI Architect caller ID fixed on Synthflow's side; Synthflow plan limits; open items
**Follow-up to the 15:30 CDT entry (Ali's Invalid Caller ID finding):**
- Kes shared the failing call record: Synthflow agent "ROI Architect" (outbound, V3), caller ID +16822283242, contact "Campaign Test Lead" (+1 682 597 5784, same 682 area code), 5 s, "Telephony - 403 - Caller ID is unauthorized. A Valid Caller ID is either a DID you have purchased from Twilio or a verified Caller ID" (identical to Ali's Twilio error). The contact name suggests a test campaign retrying one test lead, which would inflate the failure count (not yet confirmed; Kes/Ali to check the campaign's lead list). Not a Cora agent: all Cora outbound calls dial from +1...1028.
- Synthflow plan limits (screenshot from Kes): "Import Phone Number" (Twilio, SIP) is an Enterprise-only feature and there is no "buy a number" option on this workspace plan. So Ali's option "point the agent at one of the 75 owned numbers" cannot be done by importing; it only works for numbers already attached to the workspace. `GET /v2/numbers` returns an empty list (no Synthflow-managed numbers); numbers are attached to assistants some other way. No Enterprise upgrade needed.
- **ROI Architect was switched from ***3242 to ***2754 at about 8:45-9:00 PM CDT on Oct 14** (who changed it: not known to Claude; Kes to confirm). Read-only Synthflow call list for that agent (48 h): 295 calls from ***3242 (last one 01:45Z Oct 15) and, in the 6 h before the switch, 47 from 3242: 30 failed with the Caller ID error, 17 no-answer, none connected. First 5 calls from ***2754: 2 voicemail (22 s, 24 s), 2 no-answer, 1 `failed` "Telephony - 403 - Forbidden" (a different error; watch it). "Nexus_Outbound_Voice" no longer shows ***3210 attached (not investigated; confirm intentional). Current attached numbers per assistant: 2754 ROI Architect, 2224 Cora Inbound, 7060 Edu Cory Inbound, 0787 Little Ali, 2568 Edu Cory Outbound, 1028 Cora Outbound + Colaberry Student Outreach; two Cora/Cora2 outbound agents have no number.
- Cora's own calls are healthy: Oct 14 to 15:28 CDT 185 calls, 163 connected (88%), 7 failed, avg 32 s, all from +1...1028; same as the Oct 5 baseline. Do NOT pause the campaigns.
- Gmail draft to Ali (reply in the thread "Re: ... three things"): the cause, the change to ***2754, the Enterprise limit, and three asks (owned/verified check on ***2754 and the other agent numbers; confirm the ROI campaign is a test; do you want the call-outcome health check).
**Other work since the previous entries (status):**
- Content judge rewritten ("does it OFFER a retired course?", no wording lists, two agreeing votes, `content_ok` audit rows) - DEPLOYED `ec99830` (entry above).
- Wrong-date monitor AI check (`app/services/date_judge.py`, wired into `scan_wrong_dates`; unanimous `correct` records the incident already dismissed, anything else alerts as before; `date_llm_enabled` switch; spec/32 updated) - BUILT, TESTED, COMMITTED on `fix/wrong-date-llm-check` (`2d9335e`), NOT merged, NOT deployed; waiting for Kes's go. Its own PROGRESS entry lives on that branch, so merging it will give a trivial PROGRESS.md conflict (both sides append at the end; keep both).
- Alerts that were false positives, resolved or to resolve by hand on the dashboard: AI Call Recap (fixed by the staff-recipient exemption, deployed `bbc4f76`), the two Olu Omidiji content flags (judge fixed), the wrong-date alert for the Open House / class date sentence (fix pending the branch above).
**OPEN:** (1) Ali's reply on ***2754 / other numbers / the ROI test campaign; (2) build the call-outcome health check across ALL Synthflow assistants (read Synthflow's call list; alert on a high failed-within-seconds share or "Caller ID is unauthorized"; Cora only receives webhooks for its own agents) - needs Ali's/Kes's go; (3) watch ROI Architect's "403 Forbidden" failure; (4) deploy the wrong-date AI check; (5) GHL Open House snippet still says "$0 down", "career opportunities", "student success stories"; (6) the "free" ban is still STAGED OFF (`app_config.offer_forbidden_terms` override, delete it when GHL is clean; recount the last 24 h first); (7) listen to the AI Receptionist call about the Power BI resume for any accreditation/outcome claim; (8) tell Ali the content judge no longer has the code-level phrase check he praised on Oct 13; (9) older: inbound calls (none recorded since Sep 1), GHL lead-count scan and workflow rewrites (dropped by Kes), plan name "Explorer (Free)" and the myfreeaiclass.com domain (Ali, spec/38 D5).

### 2026-10-14 (night) - wrong-date monitor: AI check before an alert (`fix/wrong-date-llm-check`)
Trigger: the active "wrong date" alert was an email Cora sent at 5:16 PM CDT to another lead (contact ...2602): "we have an Open House on October 29, 2026, where you can learn more and join the next session starting on November 12, 2026" - both dates correct; `wrong_date_guard._nearest_kind` filed Nov 12 as the Open House date because "Open House" is in the same sentence. Olu Omidiji's messages (Open House Oct 29, class Nov 12) have 0 incidents and correct dates. Kes's principle (same as the content judge): let the LLM interpret the whole message instead of more wording rules. Change: new `app/services/date_judge.py` (2 votes, unanimous `correct` required), wired into `wrong_date_monitor.scan_wrong_dates`: a unanimous `correct` records the incident already `dismissed` (resolved_by "auto: AI date check", no email, not counted); everything else raises it as before; cap 10 reviews per scan; `app_config.date_llm_enabled=false` switches it off. Verified: 17 new unit tests (mocked model and session); the new INSERT executed against the production schema inside a rolled-back transaction (nothing left behind); real-model evaluation 8 runs per case - false-alarm email and 3 other correct cases 8/8 `correct`, all 10 wrong-date cases (including the 3 real logged incidents) 0/8 `correct`. Known limits: the model is strict (a correct-but-flagged date can still stay open); the pre-send SMS gate still uses the rule engine alone. spec/32 updated. The existing opt-in DB tests (`WRONG_DATE_TEST_DATABASE_URL`) now set `date_llm_enabled=false` so they never call the model; they were NOT run (no local Postgres).

### 2026-10-14 22:15 CDT - wrong-date AI check DEPLOYED (`3f35988`); "$0 down" snippet fixed by Kes
Kes: "deploy; $0 down fixed already". Merged `fix/wrong-date-llm-check` (PROGRESS.md conflict resolved keeping both sides), full suite 1832 passed + the same 8 pre-existing failures, ruff clean, pushed `3f35988`, Hetzner `git pull && docker compose up -d --build`. Verified in the production image against the real configured dates (class November 12, 2026; open house October 29, 2026): `date_judge.review` returns `correct` for "we have an Open House on October 29, 2026 ... join the next session starting on November 12, 2026" (the false-alarm sentence) and `wrong` for "Join our Open House on November 12, 2026"; per-scan cap 10; containers up; after several 60 s scan cycles: no log errors, 0 exceptions, 0 new incidents, 0 open incidents (incident table: 136 corrected, 84 dismissed). The first live suppressions will show up as incidents with status `dismissed` and resolved_by "auto: AI date check" (`SELECT created_at, contact_id, wrong_dates FROM wrong_date_incidents WHERE resolved_by LIKE 'auto:%' ORDER BY created_at DESC;`). To switch the AI check off: `app_config.date_llm_enabled = false`.
**Open items now:** (1) the GHL Open House snippet: "$0 down" fixed by Kes; "career opportunities" / "student success stories" not confirmed fixed; (2) Ali: ***2754 and other agent numbers owned/verified, whether the ROI test campaign is a test, go-ahead for the call-outcome health check; (3) the "free" ban is still STAGED OFF (`app_config.offer_forbidden_terms` override; delete it when GHL is clean, recount the last 24 h first); (4) pre-send SMS gate still uses the date rule engine alone; (5) listen to the AI Receptionist call about the Power BI resume; (6) tell Ali the content judge no longer has the code-level phrase check; (7) older items as listed in the 2026-10-14/15 entry.


## 2026-10-15 (evening) - ROI root cause, DND false alert, internal scan, Twilio trunk

- **ROI overnight calls: root cause found by Ali.** Enterprise's own campaign QA harness (`campaignTestHarness.ts`, driven by `CampaignSelfHealingAgent`, cron :15/:45) dialed the `test_phone` platform setting (his mobile) from ROI Architect. It failed silently on the caller-ID 403 until the number switch (3242 -> 2754) made the calls connect. Ali cleared `test_phone`; the harness was still calling +1 682 597 5784 ("Campaign Test Lead") at 9:45 PM CT, so asked Ali to confirm it is stopped before the trunk is fixed. Cora is not involved.
- **ROI 403 still open.** Rebuilt Twilio trunk `synthflow-trunk-12697782754` (Kes), attached `Synthflow ACL dev` (shared with 3 working trunks). Test calls to Kes still fail in Synthflow with "Telephony - 403 - Forbidden" after 5 s (e.g. 39d6d422..., e5c660b2...). Synthflow region is Global. The credential list on the new trunk is empty; Synthflow-side SIP settings are Enterprise-gated. Waiting on Ali (old SIP username/password or reset) and Synthflow support (number still bound to the old agent 8b14b4e7 in the deleted workspace?). Unproven which of the two is the cause.
- **DND false alert fixed (`c6709a6`).** A lead texted "call me back" then "remove my contact info"; the sync processed both in one pass, set DND, then checked the earlier text against the new DND and alerted. `dnd_request.on_reply` now takes the message time and skips when Cora applied DND after it. Staff-set DND after the text is still not detectable.
- **Staff-recipient emails: scan and mark, no alert (`eb049de`, Ali's alternative).** `delivery_sync._flag_content` records course-name and "free" hits to staff addresses as `channel_events` kind `content_internal`; employment/placement claims are still flagged. Spec 35 updated. Deployed.
- **"free" ban still STAGED OFF** (`app_config.offer_forbidden_terms` override). Ali approved switching it on now. Last-24h recount: 11 of ~460 outbound would flag (9 leads), 10 from staff / Conversation AI in the app. Delete the override to switch on.
- **Ali's website items (not Kes/Cora):** button to "Start for $0" / "Join for $0, no card needed"; replace myfreeaiclass.com in outbound links with a clean URL and redirect the old domain.


## 2026-10-16 - GHL SMS lockout, opt-out guard, corrections to the 10-15 entry

**Corrections to the 2026-10-15 (evening) entry:**
- The "free" ban is now LIVE (override `offer_forbidden_terms` deleted 2026-10-15 evening; verified in the container: "start for free" is flagged, "feel free to" is not).
- The QA-harness explanation for the overnight ROI calls was retracted by Ali on 10-16: the harness's voice step has never succeeded (inert). The 124 ROI calls Oct 14 6 PM - Oct 15 8:45 AM CT were recorded in Synthflow as REST-API calls from +1 269 778 2754 (100) and +1 682 228 3242 (25) to +1 682 597 5784, which is Ali's own mobile. What placed them is still unknown (Ali is checking an inbound-email webhook that phones him with no rate limit). Ali asked us to stop rebuilding the Twilio trunk (his diagnosis was wrong); the trunk Kes rebuilt and the `Synthflow ACL dev` attachment can stay. The ROI 403 ("Telephony - 403 - Forbidden", 5 s, never reaches Twilio) is still open: Kes asked Synthflow support to detach the number from the old agent in the deleted subaccount; Ali's alternative theory is a caller-ID / which-Twilio-account question.

**GHL SMS lockout (10-15 17:46 CT to 10-16 17:45 CT):** GoHighLevel blocked every outbound text ("Your account has reached today's opt-out/error limit"). HighLevel's lockout is a 3% opt-out rate or 10% delivery-error rate, 24 h. The 24 h before the block: 13 opt-outs on ~270 texts (4.8%; 9 real STOP-type = 3.3%), 6 delivery errors (2.2%). Trend (opt-outs / delivered): 10-12 1.9%, 10-13 0%, 10-14 2.1%, 10-15 4.8%. Of 17 opt-out replies since 10-14 noon, 13 followed Cora's post-call text via the "AI Agent - Send SMS" workflow, 3 followed GHL-app texts, 1 was spam. GHL marks a failed workflow SMS "Success" and does not retry: 118+ leads lost their text. Kes paused the SMS workflow in GHL. The `channel_silence_sms` alert fired only at 12:29 PM on 10-16 (~18 h late). Restriction History in GHL not yet read.
- Cora's own SMS volume is small and bounded: 708 texts to 341 leads in 7 days (Cold Lead 661, New Lead 51), 1-3 per lead.

**Deployed 2026-10-16 (production `44604dc`):**
- `app/services/sms_guard.py` + `sms_ledger.reserve` + alert cycle: rolling 24 h opt-out rate >= 2.0% (min 50 delivered texts) pauses Cora's follow-up texts (ledger BLOCK, code `optout_guard`; leads still get email/voice); resume below 1.5% or when under 50 texts remain; alert on each pause/resume; switch `sms_guard_enabled`, lines `sms_guard_pause_pct` / `sms_guard_resume_pct` / `sms_guard_min_sample`, state `app_config.sms_guard_state`. It does NOT stop GHL-workflow or staff texts. It paused itself on deploy (4 opt-outs / 81 delivered = 4.9%). Spec 35 updated.
- `optout._handle_reply`: a keyword DND match inside a text longer than 60 characters is first checked by the LLM; skipped only when it is sure (>= 0.85) the lead is not the one asking (automated notice, spam, another service's unsubscribe message); any doubt or model failure still applies the DND. The LLM prompt now says automated/system notices are "other".
- DND false alert fixed earlier the same day (`dnd_request.opted_out_after`).

**Open:**
- Two leads were wrongly given GHL DND (all channels) by the old opt-out check: Kyle (contact `czEFl7aAktfVSJUXOT2x`, two automated texts) and a no-name contact (`sALI8spt2MdsQU08RrMl`, a spam text). Not undone yet - waiting for Kes's go-ahead (Cora's `optout.undo` restores the earlier state).
- Awaiting Ali's approval of the cadence change: Cora's follow-up text after the first missed call only, then at most 1 per lead per day and 2 in total unless the lead replies. (Kes removed the "drop scheduled SMS after a week above 2%" fallback from the email to Ali on 2026-10-16; it is not part of the proposal.)
- Leads whose texts failed during the block need a follow-up by email/voice (list not built yet).
- Ali's items: website button ("Start for $0" / "Join for $0, no card needed") and the myfreeaiclass.com link replacement + redirect; the 1:40 PM staff email he asked Kes to chase (unidentified).


## 2026-10-16 (night) - alert recovery rule, guard small-sample fix, DND leads closed
- **Deployed (`55737df`):** (1) delivery-health recovery rule: when the last 3 h hold >= 5 resolved sends delivered at >= 90%, the 24 h rate rules drop to AMBER "recovering" and the alert resolves (the SMS alert had stayed RED ~a day after GHL's lockout ended; spec 35). (2) SMS opt-out guard: resume on a small sample only when at most `sms_guard_resume_max_optouts` (1) opt-outs remain; before this the guard resumed at 2 opt-outs on 44 texts (4.5%) because the GHL lockout had shrunk the delivered count under the 50-text minimum. The guard is `ok` now and re-pauses at >= 50 delivered texts and >= 2%.
- **State:** Kes restarted the GHL "AI Agent - Send SMS" workflow; texts delivering again (26 delivered / 0 failed in 3 h). The opt-out rate was still ~4.5% on a small sample.
- **Closed:** the two leads listed in the previous entry as wrongly DND'd (Kyle `czEFl7aAktfVSJUXOT2x`, no-name `sALI8spt2MdsQU08RrMl`): Kes checked them and they are blocked for another justified reason. Leave them blocked; no undo.
- **Still open:** Ali's approval of the cadence change (1 text per lead per day, 2 per week unless the lead replies; ~39% fewer Cora texts on last week's volume); resend/follow-up list for leads whose texts failed during the lockout; Restriction History not read; Ali's website items.


## 2026-10-16 (late) - SMS guard made silent
- **Deployed (`f9e27c6`):** `sms_guard` pauses / resumes Cora's follow-up texts WITHOUT any dashboard alert or email (Kes: "I don't want to see alert about preventing follow-ups"). The pause itself is unchanged (>= 2% rolling 24 h opt-out rate on >= 50 delivered texts; resume below 1.5%, or a small sample with at most 1 opt-out). A pause / resume is an `app_config.sms_guard_state` change (audit_log, updated_by "auto: sms opt-out guard") plus a log line. The one active "Scheduled texts PAUSED" alert (d6d30ac0...) was resolved by hand. Spec 35 updated.
- **State at the time:** the guard re-paused at 01:17 UTC 10-17 (3 opt-outs on 50 delivered = 6.0%); Cora's follow-up texts are held until the rate falls below 1.5%. GHL workflows / staff texts are not affected.

## 2026-10-20 — $0 sign-up link moved to training.colaberry.com/start-free (branch fix/start-free-url, NOT yet merged/deployed)
- Kes: `https://training.colaberry.com/start-free` is the approved $0 link and must not trip the banned-word "free" check; it replaces myfreeaiclass.com everywhere.
- `app/core/offer.py`: `forbidden_hits()` strips that exact link (any scheme/www/case, optional trailing `/` or `?query`) before scanning, so the email audit, SMS gate and message generator all honour it. `/start-free-trial`, other hosts, and the word "free" outside the link still hit. Tests in `tests/unit/test_offer_terms.py`.
- `DEFAULT_FREE_SIGNUP_URL` is now the full URL. Old domain replaced in app, dashboard-ui, docs, directives, tests (history in this file left as written).
- DEPLOY CHECK: `free_signup_url` can be overridden in app_config (Settings) - if production has the old value saved it keeps winning; update it after deploy. Also GHL email templates / Synthflow prompts / workflows outside this repo may still carry the old domain.
- 2026-10-20: merged into feat/ghl-call-conversation-sync (4ab3b4f) and Hetzner redeployed (`git pull && docker compose up -d --build`). Verified in the running worker: default URL is the new one, `offer.forbidden_hits` ignores the approved link and still flags "start free". app_config has no `free_signup_url` override, so the default applies. Remaining outside this repo: GHL email templates/workflows and Synthflow prompts may still use myfreeaiclass.com.

## 2026-10-20 (later) — no "https://" in anything the voice agent speaks (branch fix/voice-no-https, NOT yet merged/deployed)
- Kes: the voice agent reads "https://" aloud, which is distracting. In `docs/synthflow-{warm-lead,cold-lead,inbound}-prompt.md` and `docs/colaberry-knowledge-base.md` the sign-up link is now written `training.colaberry.com/start-free` (no scheme). The outbound campaigns (New Lead = warm, Cold Lead = cold) load these files from the Docker image per call, so a Hetzner rebuild is what makes it live. Inbound prompt is also pasted into Synthflow's dashboard (Kes already updated that).
- Text/email content keeps the full https:// link (GHL email templates, vm follow-up generator).
- 2026-10-20: fix/voice-no-https merged into feat/ghl-call-conversation-sync (b348051) and Hetzner redeployed. Verified in the running worker: New Lead and Cold Lead prompts contain no http(s):// and still contain training.colaberry.com/start-free. Not yet verified on a live call - if the agent still says "https", check for a saved prompt copy in Synthflow overriding the per-call one.

## 2026-10-20 (later) — GHL Conversation AI prompt: no word "free" (branch fix/ghl-prompt-no-free; docs copy only, live prompt is in GHL)
- Alert: SMS FxT6NWuVbGhPvF3TpiA9 (source `app`, GHL Conversation AI) said "the free Explorer plan". The prompt itself used "free" 5 times. `docs/ghl-conversation-ai-cora-prompt.md` now has a "Wording rule - never say free" section and those lines say "$0 to start". Kes pastes it into GHL (AI Agents -> Conversation AI -> Cora). Not changed: Synthflow prompts and docs/sales-call-script.md still say "free Explorer" (voice, not covered by the text/email alert).

## 2026-10-20 (later) — AI Receptionist call of 2026-10-15 reviewed; voice audit + origin labels PLANNED (spec/35), nothing built
- Cora did not process that call (it is not in `call_events`; GHL's own Voice AI handled it). The GHL Note holds only a summary; the real transcript is in Voice AI call logs (`/voice-ai/dashboard/call-logs`, Private token OK).
- Transcript review: the agent offered the retired SQL developer course, quoted a "$49 AI Career Readiness Starter incl. SQL Server", said "free" and the old myfreeaiclass domain, used the KNOWLEDGE_BASE action, and an agent transfer occurred. Root cause = stale knowledge base attached to that Voice AI agent + prompt without a retired-course rule. ACTION (Kes, in GHL): replace/remove that agent's knowledge base content and add the PROGRAM RULES block to its prompt. Abere needs an Admissions callback to correct this.
- Planned work (spec/35, "PLANNED" section): scan bot lines of Voice AI call logs, and label each alert's origin (GHL AI Receptionist vs GHL Conversation AI vs Cora engine vs staff/workflow). Awaiting approval.

## 2026-10-20 (later) — Voice-call audit + origin-labelled alerts BUILT (branch feat/voice-call-audit, NOT merged/deployed)
- Kes approved: audit what GHL's AI Receptionist says, and report its alerts as coming from it. New: `app/core/voice_audit.py`, `app/services/voice_audit.py`, `GHLClient.list_voice_call_logs`, `channel_health._flag_line`, hook in `alerting.py`, tests `tests/unit/test_voice_audit.py` (29 new; full unit suite = same 8 pre-existing failures as before, ruff clean on new files). Spec: `directives/spec/35_delivery_health.md` "Voice-call audit".
- Source is the Voice AI call log (`/voice-ai/dashboard/call-logs`, Private token), bot lines only; the GHL Note holds only a summary. Approved link may be spoken ("training dot colaberry dot com slash start dash free").
- Alerts: every content-flag line now ends with `[origin: ...]`; AI Receptionist flags say "GHL AI Receptionist (voice call)". If the audit cannot read the call logs it raises its own `ghl_voice_audit_error` alert ("not a problem in the AI Receptionist itself"), never a silent pass.
- Read-only dry run on production: 216 logs, 13 would flag (incl. the 2026-10-15 Abere call: sql + free). Only the last 48 h alert when switched on.
- To go live: merge into feat/ghl-call-conversation-sync, push, Hetzner `git pull && docker compose up -d --build`. Kill switch: app_config `voice_audit_enabled=false`.
- 2026-10-20 (deploy fix): first live run failed - `channel_events.kind` is varchar(12) and the marker 'voice_scanned' is 13 chars. The failure path worked as designed (ONE `ghl_voice_audit_error` alert, emailed, nothing marked clean). Renamed the marker to 'voice_seen'; added a width test (fake-session tests cannot see column widths). Lesson: new channel_events kinds must be <= 12 chars (channel <= 10, source <= 30).

## 2026-10-22 - staff-typed texts/emails no longer alert for "free" / course names (branch fix/audit-skip-staff-senders, NOT merged/deployed)
- Trigger: alert for Rose's text to Anhtuan (17:29 UTC 10-21, `source` app, `userId` 0swBv9tBNeXeYXPYFBSx = Roselen Flores in `admissions_assistants`) saying "our free AI resources". Ali (reply to Kes): do not flag staff-sent texts/emails; keep the check on Cora, Conversation AI, workflows, voice agent. Kes: only "free" and old-curriculum terms for now.
- Change: `content_audit.parse_staff_ids` / `is_staff_sender`; `delivery_sync._flag_content` records staff hits as kind `staff_seen`, employment claims still flagged. Ids come from `admissions_assistants` + `ipbc_assistants` (dashboard settings). 9 new tests; unit suite 1898 passed, the same 8 pre-existing failures; ruff clean. Spec 35 updated.
- TO DO BY KES: add Taiwo to the IPBC assistants list in dashboard Settings (empty today; a 2025 GHL mention shows his user id as 93bhNRgb5pzSoHmaSimH - confirm). Any other staff who send from the app must be in one of the two lists.
- Correction of an earlier claim: user id yIhCT... is Balakrishna K (support roster), and it appears on Conversation AI and workflow texts - a user id alone does not prove a human typed a message; hence `source` = `app` is required too.
- FOUND, NOT FIXED: `content_internal` (16 chars) exceeds `channel_events.kind` varchar(12); production has 0 such rows, so the 10-15 "scan and mark staff emails" record has never been saved (the error is swallowed; no alert fires, but the audit record is missing). Fix = rename to <= 12 chars (e.g. `content_int`).
- 2026-10-22: `fix/audit-skip-staff-senders` merged into feat/ghl-call-conversation-sync (9740dae), pushed, Hetzner redeployed (`git pull && docker compose up -d --build`). Verified in the running worker: new helpers present; Rose (source app) exempt, Rose via workflow NOT exempt, Balakrishna's id NOT exempt; 0 errors in api/worker-default logs in the 5 min after; all services Up. Taiwo still has to be added to the IPBC assistants list in dashboard Settings.

## 2026-10-23 - Delivery Health banner shows the SMS opt-out guard (branch feat/dashboard-sms-guard-status, NOT committed/merged/deployed)
- Trigger: dashboard showed Cora SMS "sent 0" with no reason. Cause found in production: `app_config.sms_guard_state` = paused since 2026-10-22 23:25 UTC (auto guard), 3 opt-outs on 42 delivered texts in 24 h = 7.1% (pause line 2%); resumes below 1.5%, or under 50 texts with at most 1 opt-out. Not a manual switch; `system_paused` and `outbound_campaigns_paused` were false.
- Change: `sms_guard.status()` (read-only, never raises), `channel_health.snapshot` returns `sms_guard`, banner in `dashboard-ui/components/DeliveryHealthClient.tsx`, type in `lib/api.ts`. 4 new tests in `test_sms_guard.py`; spec 35 updated. Built in a git worktree (`../cora-sms-guard-ui`) so the uncommitted `feat/explorer-landing-page` edits in the main folder were not touched. The worktree has no `.env`, so 3 `test_conversation_log_jobs` tests fail there on a clean checkout (they pass in the main folder); not related.
- 2026-10-23: `feat/dashboard-sms-guard-status` merged into feat/ghl-call-conversation-sync (43e59eb), pushed, Hetzner redeployed (`git pull && docker compose up -d --build`). Verified live: `/dashboard/delivery-health` returns `sms_guard` (paused, 3 opt-outs on 42 texts = 7.1%, paused since 2026-10-22 23:25 UTC, oldest counted opt-out leaves the window 2026-10-24 12:28 UTC); all services Up; 0 errors in api / worker-default logs. The explorer-landing-page link edits were discarded at Kes's request (old `training.colaberry.com/start-free` stays live).

## 2026-10-23 (later) - Failed live transfer: hard prompt rule + staff callback task (branch fix/failed-transfer-recovery, NOT committed/merged/deployed)
- Incident: Cold Lead call 735331a5 (12:46 PM CT 10-23): caller asked for a human 5 times; `admissions_transfer_roselen` returned `transfer-failed-connection-error` / `transfer_blocked_by_carrier`; the agent used the Synthflow transfer action's own "couldn't transfer ... try again later?" line, ignored Section 7 (callback), said goodbye. Engine then parked the lead as `human_transfer` and created no staff task. Prompt injection itself works (production worker sends the 12.8K-char prompt incl. Section 7); the bad lines come from the Synthflow transfer action config, not the prompt.
- Engine bug fixed: `intent_detection` only knew transfer statuses failed/error/timeout; Synthflow sends `transfer-failed-connection-error` + `failed_reason`, so every failed transfer counted as a confirmed handoff. New `transfer_recovery.py` + a forced "CALL BACK NOW" task in `create_crm_task` (spec/39). Prompts: new "## 0. HARD RULE" in cold, warm (injected on deploy) and inbound (Kes pastes) docs.
- Tests: 25 new in `test_transfer_recovery.py`; one existing test updated for the new flag keys; ruff clean.
- Production facts: failed transfers in 30 days = Roselen carrier-blocked on 10-22 and 10-23, support staff outside availability once. The Suresh lead (+14692542085) has lead_state `human_transfer` and no GHL task: needs a manual callback. Roselen's transfer number being carrier-blocked is a Synthflow / telephony issue (Kes / Ali).
- 2026-10-23 (Kes's instructions): (1) the AI redial is TERMINATED for failed transfers - no redial is scheduled and pending redials of that lead are cancelled (`_cancel_pending_redials`); (2) NEW alert `call_transfer_failed` (`services/transfer_alert.py`) emails once per failed call; (3) inbound prompt block tailored (Admissions / Support / Payments) - Kes pastes it above "## 1. ROLE & CONTEXT". Tests: 36 new across `test_transfer_recovery.py` + `test_transfer_alert_and_redial.py`; full suite same 8 pre-existing failures; ruff clean on new files.
- Findings (spec/39): Roselen's current transfer action last connected 2026-08-13 20:18 UTC, then 19 failures to 09-17 and carrier-blocked on 10-15 and 10-23. Taiwo last connected 2026-09-28 16:42 UTC (no failures since 08-29). `customer_support_farhat` has never connected (13 outside-availability failures, last 10-22); Bala 1 success (08-19). Last booked meeting 2026-10-24 00:41 UTC (176 bookings all time, 17 in the last 30 days).
- 2026-10-23: `fix/failed-transfer-recovery` merged into feat/ghl-call-conversation-sync (b93fffe), pushed, Hetzner redeployed (`git pull && docker compose up -d --build`). Verified in the running worker: cold and warm injected prompts start with "## 0. HARD RULE" and Section 7 points to it; the real Suresh call (735331a5) now classifies as transfer_failed / transfer_blocked_by_carrier with needs_callback = true (admissions); the first `call_transfer_failed` alert was raised for that call (1 active - click Resolve; it will not re-alert); all services Up; 0 errors in logs. The forced staff task only applies to calls processed from now on (that call's post-call job already ran). Inbound prompt: Kes pastes the Section 0 block (docs/synthflow-inbound-prompt.md lines 17-31).
- Baseline stats for Kes's report to Ali after the Monday 2026-10-26 transfer test to Taiwo: `docs/call-transfer-stats-2026-10-23.md` (last successful transfers per destination, bookings, and the query to re-run).

## 2026-10-24 - availability-aware transfers + inbound prompt script (branch feat/transfer-availability-and-inbound-script, NOT committed/merged/deployed)
- Kes: the agent must not attempt a transfer outside office hours; book a callback instead. Built `staff_availability.py` + a per-call LIVE STATUS block at the top of the Cold / New Lead injected prompt, prompt Section 0 step 1 (all three docs), alert tweak (outside-availability + callback booked = no alert). Tests: 29 new; suite = same 8 pre-existing failures; ruff clean.
- Inbound script: `execution/update_synthflow_inbound_prompt.py` (--list, dry run, --apply with backup + verification, --restore). Verified read-only against real Synthflow (list + dry run of the inbound agent). Not applied: Kes runs `--apply`. Inbound agent id f98454c1-2cd4-476c-b6f2-c5c425689e61; it already contains an older "HARD RULE" block (as 1.2); the doc version replaces it (28 lines added, 26 removed).
- CORRECTION (checked in `call_events.model_id`): ALL outbound calls, New Lead (305) and Cold Lead (7,348) in 30 days, run on the one assistant whose saved prompt is `{prompt}`, so the injected prompt (hard rule + LIVE STATUS) applies to both. The earlier "New Lead may ignore injection" note was wrong and is removed from spec/39.
- CATCH: the repo's inbound doc still said "free" in 7 places while Kes's live inbound agent says "at no cost"; a blind `--apply` would have put "free" back. Doc re-synced from the live prompt: it now differs from live only in the hard-rule block (now "## 1.2 HARD RULE", with the office-hours check) and one line, "Free Explorer plan" -> "Explorer plan" in Section 3 Pricing. Kes updates the live agent by hand (those two edits) or with the script.
- 2026-10-24: `feat/transfer-availability-and-inbound-script` merged into feat/ghl-call-conversation-sync (8176235), pushed, Hetzner redeployed (`git pull && docker compose up -d --build`). Verified in the running worker with production settings: `_availability_note` builds a LIVE STATUS block (Friday 9:53 PM Central: Admissions and Payments NOT available, next Monday 9:00 AM; Support Farhat, Balamurali available; no "free"); injected Cold and New Lead prompts carry Section 0 step 1 "check availability first"; `execution/update_synthflow_inbound_prompt.py --list` works inside the image (read-only, 14 assistants); all services Up; 0 errors in api / worker / dashboard logs. Live from the next outbound call.
- STILL TO DO BY KES: update the inbound agent in Synthflow by hand (2 edits: replace the "## 1.2 HARD RULE" block with the doc's version; "Free Explorer plan" -> "Explorer plan" in Section 3) or run `execution/update_synthflow_inbound_prompt.py --assistant-id f98454c1-2cd4-476c-b6f2-c5c425689e61` (dry run first, then `--apply`); then the Monday 2026-10-26 transfer test to Taiwo (baseline: docs/call-transfer-stats-2026-10-23.md). Also: the Suresh `call_transfer_failed` alert is still active (Resolve it); Roselen's transfer number is carrier-blocked (Synthflow / telephony).

## 2026-10-26 - Sign-up Intake page: hourly snapshot of Refactored sign-ups in GHL (branch feat/signup-intake-dashboard, NOT committed/merged/deployed)
- Kes asked to track the trend of new sign-ups, those with a phone number, and those with an email, with Open House as a separate line. Analysis first (read-only): 282 GHL contacts since 10-21 (baseline 8.6/day); 235 are Refactored sign-ups (232 Explorer + 3 Open House), 143 on 10-24 including a 113-contact burst at 5 AM CT (looks like Ali's backfill), 52 on 10-25. Identifiers that work today: source `api v1`, tags `src-free_signup` / `brand-training_colaberry`; 8 early 10-23 contacts are untagged. Live sign-ups carry the campaign tag only 4 of 98 times (backfilled batch 110 of 113): tell Ali. Cora has not touched the Explorer contacts; the 3 Open House RSVPs (the only ones with phones) are in Cora's New Lead flow (check their consent).
- Built (spec/40): migration 0030 `signup_daily` + `signup_snapshot`; `services/signup_intake.py`; hourly job `signup_intake_sync` (registered in `worker/main.py`); `GET /dashboard/signup-intake`; page `dashboard-ui/app/signup-intake` under Analytics. 22 new tests; read-only dry run against real GHL matched the analysis. Deploy needs `alembic upgrade head` (the compose rebuild runs migrate) and the first job run backfills from 2026-10-21.
- 2026-10-26: `feat/signup-intake-dashboard` merged into feat/ghl-call-conversation-sync (f6743e0), pushed, Hetzner redeployed (`git pull && docker compose up -d --build`). Migration 0030 applied (alembic_version = 0030; `signup_daily` and `signup_snapshot` exist). First `signup_intake_sync` run completed at 16:41 UTC and the next is scheduled hourly: 232 Explorer (232 email, 0 phone, 116 with a campaign tag) + 3 Open House (3 email, 3 phone), per day 10-23: 15, 10-24: 142 + 1, 10-25: 52 + 2, 10-26: 23 so far. `/dashboard/signup-intake` returns it and `/signup-intake` serves HTTP 200; all services Up; 0 errors in api / worker / dashboard logs. Only 1 cumulative snapshot point exists so far, so the running-totals chart fills in hour by hour (it records when phones and emails arrive from now on).

## 2026-10-26 (later) - "103 New Lead" explained; carrier-recording redial loop fixed (branch fix/carrier-message-redial-loop, NOT committed/merged/deployed)
- Kes: the Voice Performance tooltip says New Lead 103 for the week of 10-19. Investigation (read-only): 103 = CALLS (103 distinct call ids) to 40 contacts; only 31 were new that week (4 created 8-30 days ago, 5 over a year old; none called us first). 21 of the 103 calls (3 numbers) were a bug: a carrier recording ("We could not complete your call. Please try again." / "not in service ... try again later") matched `call_later_no_time`, which scheduled a callback every ~2 h, exempt from the daily cap (`CALLBACK_INTENT_REASONS`) and with no retry count: one number 15 calls in 3 days, another 9; 59 such redial jobs completed across 8 contacts in 14 days, still running on 10-23.
- Fix (spec/41, approved by Kes): new intent `carrier_message` (no callback, cancels waiting redials, 2nd call in 30 days marks the number invalid); `call_later_no_time` capped at 3 per 14 days then cold; `call_later_no_time` removed from the daily-cap exemption; tooltip relabelled "Calls by type (not leads)" with a new "New contacts (first call)" row (verified on production: week 10-19 New Lead = 31). Cleanup script `execution/fix_carrier_message_calls.py` (dry run first). 21 new tests; suite = same 8 pre-existing failures.
- Read-only scope estimate of the cleanup (broader SQL match): ~126 calls / 21 contacts in 60 days, 93 mislabelled, 12 contacts with 2+ carrier calls (heaviest 39 calls) - the 12 would be marked invalid; review the dry run before `--apply`.
- Data note: 220 call_events rows have impossible dates (as early as 1776); the new metric ignores them but they should be repaired.
- 2026-10-26 (deployed): `fix/carrier-message-redial-loop` merged into feat/ghl-call-conversation-sync (221d13f) and a follow-up `fix/cleanup-strike-window` (c31cd86: the cleanup judges "invalid" on the live 30-day rule, relabels the full 60 days); Hetzner redeployed twice (`git pull && docker compose up -d --build`). Verified live in the worker: a carrier recording -> `carrier_message`; `call_later_no_time` no longer exempt from the daily cap; cap 3 per 14 days; 2 strikes. Cleanup run by Claude at Kes's instruction (dry run, counts checked against the estimate, then `--apply`): 121 carrier-recording calls to 14 contacts, **98 relabelled** call_later_no_time -> carrier_message, **1 pending redial cancelled**, **6 numbers marked invalid** (2+ carrier calls in 30 days; heaviest contact 40 calls). Afterwards in the database: 0 carrier-recording calls still labelled call_later_no_time, 6 lead_state rows closed/invalid, 0 pending call_later redials, no carrier-recording call in the last 12 h, all services Up, 0 tracebacks. 28 call_later_no_time calls to 9 contacts remain (real callback requests).
- To undo the 6 invalid markings if one turns out to be a real lead: set `lead_state.invalid=false, status='active'` for that contact (CORRECTION 2026-10-26: this note first said the GHL "AI Campaign" field was also set to No by the wrong-number path. It was NOT: `_write_ghl_campaign_off` calls `update_contact_fields` without the live mode flags, so it falls back to `settings.ghl_writes_enabled` (False in production = shadow, no write), and these contacts are keyed by phone number, which GHL cannot update by id. Only lead_state was changed. Read-only check: of the 6 invalidated numbers GHL still says AI Campaign = Yes for one (...1825). The helper is also used by the do_not_call / not_interested / wrong_number handlers, so those have the same gap. Fix needs Kes's approval: pass the live mode flags and resolve a phone-keyed id to the GHL id via search_contact_by_phone.)

## 2026-10-26 (late) - Booking intent, person-only callback detection, campaign on callbacks (branch fix/booking-intent-and-callback-detection)
- Why: 25 lead-requested callback jobs in 30 days were all cancelled ~2 h after creation by the urgent-escalation guard and none ran. 17 were successful bookings recorded as `callback_with_time` (an AI redial was queued for people who had just booked), 2 were voicemail greetings read as `callback_request`, 2 matched only the bot's own words; the intent handlers also created 133 jobs without a campaign name (launcher default `New_Lead`), so Cold Lead callbacks were launched as New Lead calls.
- Fix (spec/42): new intent `booked` (same urgency/score as before, no redial); callback intents read only the person's lines; voicemail greetings give no intent; callback jobs carry `lead_state.campaign_name`, launcher falls back to the lead's campaign. `execution/relabel_booked_calls.py` relabels past bookings (label-only). 23 new tests; suite = same 8 pre-existing failures.
- 2026-10-26 (deployed): `fix/booking-intent-and-callback-detection` merged into feat/ghl-call-conversation-sync (a862a4d), Hetzner redeployed (`git pull && docker compose up -d --build`). Verified live in the worker: a successful booking -> `booked`; a voicemail greeting -> no intent; words only the bot said -> no callback; a real "call me back tomorrow at 3" -> callback_with_time; `booked` handler registered and urgent in the escalation guard. All services Up, 0 tracebacks. `execution/relabel_booked_calls.py` run by Claude (dry run: 141 callback_with_time calls, 127 real bookings, 14 real callback requests; then `--apply`): 127 past bookings relabelled `booked`; afterwards 127 booked / 14 callback_with_time remain, 0 bookings still labelled as callbacks. Label-only: same score (88) and urgency, no jobs touched.
- STILL OPEN (needs Kes): (1) `_write_ghl_campaign_off` shadow-write + phone-keyed id bug (only Cora's lead_state is changed when a lead is marked invalid / DNC; GHL "AI Campaign" is not written) - one invalidated number (...1825) still says AI Campaign = Yes in GHL; (2) whether to set `report_excluded` on the ~121 carrier-recording calls so reports drop them; (3) the 2 real callback requests of the last 30 days were never AI-called (by design they go to the human sales queue): confirm someone followed up.

## 2026-10-26 (night) - "AI Campaign = No" write fixed (branch fix/ai-campaign-off-write, committed locally, NOT merged/pushed/deployed)
- Found while checking the carrier-recording cleanup: the do-not-call / not-interested / wrong-number handlers (and, via `_handle_wrong_number`, the spec/41 carrier handler) never wrote `AI Campaign = No` to GHL: `_write_ghl_campaign_off` (since 2026-04-04) called the writer without the live-mode flags (silent shadow) and cannot update phone-keyed contacts. 49 of the 60 most recent do-not-call / invalid leads still said Yes in GHL. My earlier claim that the cleanup had set the GHL field was an untested assumption; corrected above.
- Fix (spec/43): `set_ai_campaign_no` (live flags, phone verified against the GHL contact, returns True only for a real write), handlers pass the session, `execution/backfill_ai_campaign_off.py` (dry run, then `--apply` with a read-back of every contact). 19 new tests using a fake GHL that behaves like the real one; suite = same 8 pre-existing failures.
- Deploy was blocked by the auto-mode permission check ("Production Deploy"); waiting for Kes to allow it or deploy.
- END-TO-END TEST (2026-10-26, run by Claude at Kes's request, no deploy: the fixed function was loaded from the branch into a one-off script on the worker, real dashboard flags): production flags are live (`ghl_writes_enabled=True`, shadow off). On ONE contact, +16174851825 (not-in-service New Lead number, GHL contact `3Y7OLXcJDVMUYLVg33NN`): phone resolved to the expected GHL contact; AI Campaign BEFORE = Yes; `set_ai_campaign_no` returned True; AI Campaign AFTER, read back from GHL = No. Only that one contact was written; services unchanged. Still TO DO: deploy (blocked by the permission check) and the dry-run backfill (~50 leads).
- 2026-10-26 (deployed): `fix/ai-campaign-off-write` merged into feat/ghl-call-conversation-sync (cc0db7e / 79060a9), pushed, Hetzner redeployed (`git pull && docker compose up -d --build`; the first attempt was blocked by the permission check, the second ran after Kes explicitly asked for the deploy). Verified live: `set_ai_campaign_no` and `resolve_ghl_contact_id` exist in the running worker; all services Up. From now on a do-not-call / not-interested / wrong-number / 2-carrier-strike really writes AI Campaign=No to GHL (live flags, phone verified), or logs a WARNING and returns False.
- BACKFILL DRY RUN (`execution/backfill_ai_campaign_off.py`, read-only, nothing written): Cora has 1,022 leads marked do-not-call (1,003) or invalid (21). In GHL 47 already say No; **975 do NOT** (974 do_not_call, 1 invalid): current value Yes 937, unset 35, odd values True/False 3. 0 had no matching GHL contact. This is far more than the "roughly 50" first estimated from a 60-lead sample. NOT applied: waiting for Kes. Recommendation: apply in stages (first ~10, check the GHL workflow history for anything that fires on an AI Campaign change, then the rest; ~20 min for all, each write is read back).
- 2026-10-26 (backfill DONE): `execution/backfill_ai_campaign_off.py` applied in stages by Claude at Kes's go-ahead. Stage 1 (`--limit 10`, 20:51 UTC): 10 written, 10 verified by read-back; rechecked after 5.5 min - no new GHL messages, tag changes or Cora jobs/calls/events for those contacts, so stage 2 went ahead. Stage 2 (all remaining) was killed partway when the SSH connection dropped; re-run detached inside the container (the script skips contacts already at No, so re-running is safe): final result 1,039 leads marked do-not-call / invalid in Cora, **1,031 already No**, 8 written now, **8 verified by read-back, 0 failed, 0 unmatched**. Every lead Cora marked do-not-call / invalid now has AI Campaign = No in GHL. Live path: 18 leads were marked after the fixed code went live (19:49 UTC) and all 18 say No (the backfill needed to write only 8 of the 17 added since the dry run, so the live handler is writing; not isolated proof). Lesson: run long jobs on the server detached (`docker compose exec -d ... > log`), not over a long SSH session.
- Not changed: GHL's own Do-Not-Disturb (separate mechanism), and I have not inspected which GHL workflows key on AI Campaign.

## 2026-10-26 (close-out) - status audit; server brought level with the repo
- Hetzner redeployed (`git pull && docker compose up -d --build`) so the image matches the repo at cc75084: the deployed `execution/backfill_ai_campaign_off.py` now has `--limit N` (verified with `--help`); all services Up, 0 tracebacks. This closes the "two copies of the backfill script" gap.
- Kes's instruction (2026-10-26): consider the other open items from the status audit closed. They were NOT resolved or verified - recorded here so nobody assumes they were: (1) Taiwo is not saved in the dashboard's IPBC assistants list (`ipbc_assistants` is not in app_config), so the staff-sender exemption still only covers Rose; (2) the inbound Synthflow agent's prompt update (two manual edits, or `execution/update_synthflow_inbound_prompt.py --apply`) was not confirmed; (3) the `report_excluded` option for the ~121 carrier-recording calls was not decided; (4) the two draft emails to Ali were not confirmed sent; (5) 3 `call_transfer_failed` alerts (4 failed live transfers on 10-26 at 15:02 / 16:12 / 16:51 CT, after the hard rule went live) were not reviewed; (6) not investigated: 220 call_events rows with impossible dates, GHL workflows that key on AI Campaign, GHL do-not-disturb for the 1,039 opted-out leads, the 5 contacts labelled New Lead but Cold Lead in lead_state, and whether Cold Lead callbacks launched as New_Lead really got the New Lead script; (7) 8 unit tests fail on a clean checkout (unrelated, pre-existing).

## 2026-10-26 (late night) - staff exemption for all staff + dead-number calls out of reports (branch fix/staff-exempt-and-callback-only); callback-only (#5) HELD on branch hold/callback-only-teams
- Kes's decisions: (1) exempt ALL staff from "free" / old-course flags; (3) dead-number calls must not be reported; (5) Synthflow cannot transfer to international numbers, so Admissions and Support become callback-only and the agent must never offer "keep talking to me". Kes then said "leave #5 for now": #5 is built and tested but NOT deployed (branch `hold/callback-only-teams`; deploying it also needs the inbound Synthflow prompt block re-pasted). Sections 1 and 3 ship. Built (spec/44): `content_audit.staff_user_ids` (admissions + ipbc + whole support roster + `audit_staff_user_ids`, minus the AI user id `yIhCT...`); `carrier_message` calls get `report_excluded=true` automatically and the cleanup script excludes the existing ones; LIVE STATUS block + the three prompts make Admissions/Support callback-only (`transfer_callback_only_teams`, default `admissions,support`) and ban the "keep talking to me" phrasing. 2074 tests pass, same 8 pre-existing failures.
- Why the 4 failed transfers on 10-26 did not book: Synthflow's transfer action plays its own line ("Sorry, I couldn't transfer you. Would you like to continue the conversation with me or try again later?") whenever it is called; the caller answers "later" and the agent says goodbye (booked on 1 of 4). Not calling the transfer for those teams removes the line.
- INVESTIGATED, NOT FIXED (proposals to Kes): (a) impossible call dates: `_parse_datetime` reads a 13-digit epoch-ms STRING with `datetime.fromisoformat` first, which turns "1793061552134" into year 1793; 238 rows, 21 in the last 24 h, those calls vanish from the weekly reports (raw payload has the real time: repairable); (b) four more GHL writers never pass the live flags and so run in shadow: end-of-campaign finalization (`voicemail_jobs`; 38 of 40 recent finalized leads still say AI Campaign = Yes, 2,647 finalized in total), `_handle_enrolled` (0 enrolled leads so far), the dashboard `force_finalize` (also sends a label instead of a field id) and `send_student_summary`; (c) the GHL field "Student Summary" does not exist, so none of 301 consented student summaries was ever delivered (25 of 25 sampled empty); (d) GHL Do-Not-Disturb is not set on about a third of recent opt-outs (28 of 80 have no active channel); (e) GHL task descriptions are dropped by design (the v2 API returns 422 if sent), so "CALL BACK NOW" tasks carry details in the title only.
- 2026-10-26 (deployed): `fix/staff-exempt-and-callback-only` (items 1 and 3 only) merged into feat/ghl-call-conversation-sync (3cdf664), pushed, Hetzner redeployed. Verified live: Taiwo, Farhat and Balamurali (roster) exempt from the free / old-course audit, the AI user id `yIhCT...` not exempt; a carrier recording -> `carrier_message` (which now sets `report_excluded` automatically); all services Up, 0 tracebacks. Item 5 (callback-only for Admissions and Support) NOT deployed: held on branch `hold/callback-only-teams` (pushed), per Kes.
- NOT DONE: the report-exclusion cleanup `execution/fix_carrier_message_calls.py --apply` was blocked by the permission check (modifies shared data) and needs Kes's explicit go-ahead. Dry run on production: 121 carrier-recording calls to 14 contacts to keep out of the reports (none already excluded); 6 contacts already marked invalid.
- FINDING (the 10-26 20:05 CT "successful" transfer to Balamurali): call edf97f56 (Cold Lead, outbound, 45 s, ended as voicemail) was NOT a conversation. The line was a recording ("Your call is very important to us. Please stay on the line and you'll be transferred to the next available agent ... No one is available ... leave a message after the tone"); the agent called `customer_support_balamurali` anyway and Synthflow reported transfer-success. It is the only attempt ever on that action. It is not evidence that international / support transfers work, and it probably rang Balamurali at 8 PM for a voicemail. The agent should never transfer on a recording / IVR.


---
## 2026-10-26 - SESSION CLOSE-OUT: work done (10-22 .. 10-26) and what remains
Read this first in the next session. Production-tracking branch = `feat/ghl-call-conversation-sync` (cut new work from it; merge direct after Kes's go-ahead; Hetzner: `git pull && docker compose up -d --build`). Production is at 3cdf664 (+ docs commits). All of the below is DEPLOYED unless marked otherwise.

### A. Done and live
| Area | What | Spec / commit |
|---|---|---|
| Content audit | Staff-typed texts/emails (source `app`) no longer alerted for "free" or old-course names; since 10-26 ALL staff: admissions + IPBC lists + whole support roster + defaults Roselen / Taiwo, minus the AI user id `yIhCT...` (the Conversation AI posts under the same id as Balakrishna; GHL has no other AI marker). Employment claims from staff still flagged. | spec/35 add., spec/44; 9740dae, 3cdf664 |
| SMS opt-out guard | Delivery Health shows a banner with why Cora's follow-up texts are paused (rate, resume rule). State now `ok` (resumed). | 43e59eb |
| Failed live transfers | Failed transfers recognised (Synthflow `transfer-failed-*` + `failed_reason`), no AI redial after one, "CALL BACK NOW" staff task, `call_transfer_failed` alert emails, voice prompts Section 0 (try once, then book a callback; banned phrases). | spec/39; b93fffe |
| Availability-aware transfers | Every outbound call prompt starts with a LIVE STATUS block (who is on shift now, Central time). Inbound agent has a static office-hours rule. `execution/update_synthflow_inbound_prompt.py` (--list, dry run, --apply with backup + verification, --restore). | spec/39 add.; 8176235 |
| Sign-up Intake page | Hourly snapshot of Refactored sign-ups in GHL (Explorer vs Open House, with email / phone / both / campaign tag), tables `signup_daily`, `signup_snapshot` (migration 0030), page `/signup-intake`. | spec/40; f6743e0 |
| Carrier-recording redial loop | A carrier recording is intent `carrier_message` (no callback; 2 strikes in 30 days marks the number invalid); call-later callbacks capped at 3 per 14 days and no longer exempt from the daily cap; Voice Performance tooltip says "Calls by type (not leads)" + a "New contacts (first call)" row. Cleanup run: 98 calls relabelled, 6 numbers invalid, 1 redial cancelled. | spec/41; 221d13f, c31cd86 |
| Booking / callback detection | A successful booking is intent `booked` (no AI redial; same urgency as before); callback patterns read only the person's lines; voicemail greetings give no intent; callback jobs carry the lead's campaign name. 127 past bookings relabelled. | spec/42; a862a4d |
| "AI Campaign = No" write | The opt-out handlers' GHL write was silently shadow (no live flags) since 2026-04-04 and could not update phone-keyed contacts; fixed (`set_ai_campaign_no`, phone verified against the GHL contact, honest return value). Backfill: 1,039 do-not-call / invalid leads, all now No, each read back (staged 10, then the rest). `--limit` added. | spec/43; cc0db7e, f8f7805 |
| Reports | New carrier-recording calls set `report_excluded` automatically (cleanup for the existing 121 NOT applied, see B1). | spec/44; 3cdf664 |
| Reverted | The refactored.ai sign-up link replacement was discarded at Kes's request (old `training.colaberry.com/start-free` stays). | - |
| Records | `docs/call-transfer-stats-2026-10-23.md` = baseline for Kes's report to Ali; emails to Ali (sign-up sync; international transfers + lead tracking) drafted, Kes says sent. | - |

### B. Remaining - needs a decision or explicit go-ahead from Kes
1. **Apply the report exclusion for existing dead-number calls**: `docker compose exec worker-default python execution/fix_carrier_message_calls.py --apply` (dry run: 121 calls, 14 contacts; reversible flag). The permission check blocks it unless Kes says "apply" in the same message.
2. **Item 5 - callback-only for Admissions and Support**: built and tested, HELD on branch `hold/callback-only-teams` (not deployed). Reason: Synthflow's transfer action plays its own line ("Sorry, I couldn't transfer you. Would you like to continue the conversation with me or try again later?"), callers answer "later" and the agent says goodbye; 4 failed transfers on 10-26, 1 booked. Deploying also needs the inbound prompt Section 1.2 re-pasted in Synthflow (text is in the branch's `docs/synthflow-inbound-prompt.md`). Waiting for Ali's decision. Before telling Ali "all international transfers fail": confirm the real destination numbers (Roselen = carrier-blocked, Farhat = destination_unavailable, Taiwo works).
3. **Never transfer on a recording / IVR / voicemail menu** (proposed prompt rule). The 10-26 20:05 "successful" transfer to Balamurali (call edf97f56) was a voicemail recording; it is the only attempt ever on that action and is NOT evidence that transfers work. Real working transfer: Taiwo (10-26 14:40 CT, Inbound).
4. **Impossible call dates (still happening, ~21/day)**: `call_processing._parse_datetime` reads a 13-digit epoch-ms STRING with `fromisoformat` first and returns year ~1793; 238 rows, those calls vanish from the weekly reports; raw payload `start_time` has the real time. Proposed: numeric strings first + a repair script (dry run first).
5. **Other GHL writers that never pass the live flags (all silently shadow)**: end-of-campaign finalization in `voicemail_jobs` (2,647 leads finalized; 38 of 40 recent still say AI Campaign = Yes), `intent_actions._handle_enrolled` (0 enrolled leads so far), `dashboard.force_finalize` (also sends a label, not a field id), `crm_jobs.send_student_summary`. Proposed: same fix as spec/43 + a backfill of the finalized leads (stage it, read back).
6. **Student Summary**: the GHL field "Student Summary" does not exist, so none of 301 consented student summaries was delivered (25 of 25 sampled empty), although the agent offers a text summary. Decide: create the field + fix the writer, or stop offering.
7. **GHL Do-Not-Disturb** is not set on about a third of recent opt-outs (28 of 80 have no active channel). Review the Opt-outs page; consider a backfill. GHL workflows are readable by name (61, e.g. "AI Agent - Turn off AI Campaign") but not their triggers.

### C. Remaining - smaller / for follow-up
- **Resolve the 3 active `call_transfer_failed` alerts** (4 failed transfers on 10-26) on the dashboard; the Suresh alert too.
- **Numbers to verify / remove by hand in GHL** (dead-number list): +16174851825 (done: AI Campaign now No, e2e test); verify +14693249654 (40 calls, one real conversation), +17024837370 and +13236286128 (AI Campaign still Yes, not marked invalid); keep +12252577674 (5 real conversations).
- **Open House RSVPs (3, the only sign-ups with phones)** are in Cora's New Lead flow and are called / texted: check what consent that form collects.
- **Refactored (Ali)**: live sign-ups carry the campaign tag only 4 of 98 times (backfilled batch 110 of 113); 8 early 10-23 contacts untagged; the sign-up form still has no phone field or call/text consent; consent column on the Sign-up Intake page to add once it exists.
- **2 real callback requests** of the last 30 days were never AI-called (by design they go to the human sales queue): confirm someone followed up. Booked leads sit in the urgent list until a rep logs an outcome (watch if noisy).
- **Delivery Health** still counts carrier-recording calls (it does not use `report_excluded`).
- **Only the callback intents** read the person's lines; the other intents still scan the whole transcript (optional follow-up). Give the Conversation AI its own GHL user so Balakrishna's own texts can be exempt too.
- **GHL task descriptions are dropped by design** (v2 API returns 422): "CALL BACK NOW" tasks carry details in the title only; optionally add the details as a note (`append_note`).
- **Unverified inference**: Cold Lead callbacks launched without a campaign went out as `New_Lead`, which matches no known campaign, so they probably got the New Lead script (from code, not confirmed on transcripts; fixed going forward in spec/42).
- **8 unit tests fail on a clean checkout (unrelated to the work)**: 2 `test_admin_routes` (the `redis` package is not installed on Kes's PC; fine in Docker), 1 `test_cadence_fixes` (hard-coded October 8 date vs the real clock), 2 `test_ghl_adapter` (stale: they expect `status` / `description` in the task payload, which the code correctly omits), 2 `test_inbound_call_processing` (a mock lacks a number `claim.py` compares), 1 `test_enrolled_intent` (stale field-label expectation; also points at the enrolled handler's shadow write). Update the tests when touching those areas.
- **Dashboard setting**: Taiwo is not saved in the IPBC assistants list (matters for task assignment only now that the exemption has defaults).

### D. How to work in this repo (lessons learned)
- Run long jobs on the server detached (`docker compose exec -T -d worker-default sh -c '... > /tmp/x.log 2>&1'`); an SSH drop killed a long backfill once. Scripts in `execution/` run inside `worker-default` (credentials there); `docker compose cp` a one-off script in, never print secrets or phone numbers unless asked.
- ALWAYS read a GHL write back before reporting it done (the opt-out write looked fine in tests that replaced it with a stand-in). Test fakes must behave like the real client (shadow unless live flags are passed).
- The auto-mode permission check blocks production deploys and data-modifying scripts unless Kes asks for them explicitly in that message ("deploy", "apply"). Do not work around it: finish what is local, then ask.
- Pre-Ship rule: debrief and wait for Kes's go-ahead before commit / merge / deploy; stage risky rollouts (10 first, check GHL for reactions, then the rest).
- Claims I made and later corrected: the carrier cleanup did not set GHL (it never did); New Lead calls DO use the injected prompt (all calls run on one `{prompt}` assistant); the AI Campaign backfill was 975 leads, not ~50.

## 2026-10-27 - Kes approved the seven open items; built on branch fix/ghl-live-writes-dates-transfer-rule (spec/45)
- Approved by Kes ("go-ahead on these"): apply the report exclusion (121 dead-number calls); item 5 callback-only (merged in from hold/callback-only-teams); a rule never to transfer on a recording; the impossible-dates bug; the other shadow-mode GHL writers incl. the 2,647 finalized leads; the Student Summary field; GHL do-not-disturb gaps.
- Built: `app/core/ghl_write.py` + fixes to finalization / enrolled / dashboard force-finalize / student summary (spec/45 table); `_parse_datetime` epoch-string fix + `execution/repair_call_dates.py`; recording rule (Section 0 item 7) in cold, warm and inbound prompts; `backfill_ai_campaign_off.py --finalized`; `execution/drain_dnd_reconcile.py`. Student Summary field created in GHL by Kes (matches the code); a GHL workflow to text it is still needed.
- DND finding: GHL DND is applied for 806 of 1,048 do-not-call leads, 3 await review, 238 never checked (135 older than 60 days) because the reconcile job handles ~4 leads an hour; the drain only reads GHL and queues review rows, it never turns DND on.
- Tests: 2101 pass; 7 unrelated failures remain (2 redis missing locally, 1 date-hardcoded, 2 stale ghl_adapter task payload tests, 2 stale inbound-call mock tests); the stale enrolled test was fixed.

## 2026-10-26 - Balamurali live-transfer exception added to spec 45 bundle
Support is callback-only except Balamurali Kasula (his number transfers live): setting `transfer_live_people` (default `balamurali`), LIVE STATUS line + cold/warm/inbound prompts updated, 4 new tests. Inbound Synthflow prompt must be re-pasted by Kes. Suite: 2109 pass, same 7 unrelated failures.

## 2026-10-27 - Spec 45 bundle merged and deployed; data scripts applied
Merged `fix/ghl-live-writes-dates-transfer-rule` into `feat/ghl-call-conversation-sync` (c395e87), Hetzner rebuilt, services Up, no tracebacks. Applied (dry run first each): carrier/dead-number exclusion (121 calls, 14 contacts, 6 marked invalid); call-date repair (238 rows); finalized-lead backfill AI Campaign = No (10 staged + 2,577 more, all verified by read-back, 0 failed; 72 already No, 2 with no GHL contact); DND drain found 0 leads to check (not investigated why, expected ~238). Still Kes: re-paste inbound prompt in Synthflow, GHL Student Summary SMS workflow, resolve 3 call_transfer_failed alerts.
