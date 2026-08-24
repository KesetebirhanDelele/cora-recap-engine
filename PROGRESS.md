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
