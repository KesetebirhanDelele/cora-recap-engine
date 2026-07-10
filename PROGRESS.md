# PROGRESS.md

Running log of work across sessions on `feat/ghl-call-conversation-sync`, to allow any
session (human or agent) to pick up where the last one left off. Update this file at the
end of a substantial work session — append, don't rewrite history.

Read this alongside `CLAUDE.md` at session start.

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
