# Spec 30 — Per-Exception Email + Sales-Queue Urgent-Notice Routing

| Area | Status |
|---|---|
| Per-exception email (`new_exception`) | **DONE.** One email per newly-opened `exceptions` row, any type/severity, capped at 20/cycle. To Kes. |
| Sales-queue urgent-notice email (`sales_queue_urgent`) | **DONE, enabled.** One email per lead entering "urgent" sales priority, routed to Rose and/or Taiwo by topic keyword or by name, using a friendly human-facing template (not the generic system-alert one). Fires exactly once per lead, ever — not tracked against resolution (revised 2026-09-11, see below). Gated by `ALERT_SALES_QUEUE_ENABLED` (default `true` as of 2026-09-11, Kes confirmed both routing and template) — the flag exists so it *can* be switched off without a redeploy if needed; this is the one alert type in the system that emails people other than Kes. |
| `ALERT_EMAIL_TO` multi-recipient bug | **FIXED.** Was documented as comma-separated but never split before handing to `smtplib`. |
| Resolution emails (all alert types) | **REMOVED then PARTIALLY RESTORED, 2026-09-11.** First removed entirely (every alert type sent a second `[RESOLVED]` email on clearing; Kes found that noisy). Once the delay mechanism below meant some alerts might sit active for 15 min before ever notifying, Kes asked: bring resolution emails back, but *only* for alerts that actually sent an active-alert email — one that self-resolved before ever crossing its delay stays completely silent, both ways. `is_resolution` restored on `_send_alert_email`; every resolve branch now checks `email_sent_at IS NOT NULL` on the existing row before sending. Status transitions in `alert_events` always happen regardless — only the email send is conditional. |
| Delayed email for self-healing alert types | **ADDED, 2026-09-11.** `queue_lag_exceeded`, `webhook_drop_detected`, `error_rate_spike`, `exception_spike` now wait (15 min / 15 min / 15 min / 5 min respectively, calibrated against real `alert_events` history) before emailing — most of these clear on their own, so Kes only wants an email if the problem persists. `outbound_calls_stalled`, `worker_offline`, `ghl_auth_failure`, `intake_auth_failure` stay immediate (deliberately — see `_ALERT_EMAIL_DELAY_SECONDS`'s docstring for why). |

## Self-contained problem statement

### Business goal
Kes currently only finds out about operational problems (exceptions, queue
lag) and sales-critical leads (urgent sales-queue items) by opening the
dashboard. Two gaps: (1) the existing `exception_spike` alert only fires once
10 open exceptions accumulate, not on each individual failure; (2) nothing
emails anyone when a lead enters "urgent" sales-queue priority — Rose
(admissions) and Taiwo (payment/IPBC) have no way to know a hot lead needs
them without watching the dashboard live.

### Relevant systems and files
- `app/services/alerting.py` — existing SMTP alerting service, called once
  per 60s metrics cycle from `app/worker/jobs/metrics_jobs.py`. Already live
  in prod (`queue_lag_exceeded`, `exception_spike`, `ghl_auth_failure`,
  `intake_auth_failure`, `outbound_calls_stalled`, `webhook_drop_detected`,
  `worker_offline`, `error_rate_spike`).
- `app/models/alert_event.py` (`alert_events` table) — reused as a
  per-entity notified-ledger for both new alert types (see Constraints —
  no schema change).
- `app/models/exception.py` (`exceptions` table) — source for `new_exception`.
- `app/services/dashboard_metrics.py::_compute_sales_priority` /
  `_INTENT_SCORES` — the existing "urgent"/"review"/"none" sales-queue
  classification shown in the dashboard UI; `sales_queue_urgent` reuses its
  intent set (the always-urgent subset — see Constraints) rather than
  reimplementing it.
- `app/config/settings.py` — new `alert_email_rose`, `alert_email_taiwo`
  fields.

### Input data
- `exceptions` rows (`status='open'`) for `new_exception`.
- `call_events.detected_intent` + `call_events.transcript` +
  `lead_state.sales_outcome` for `sales_queue_urgent` (same tables
  `escalation_guard.py` and `dashboard_metrics.py` already read).
- `scheduled_jobs` (`launch_outbound_call`, pending/claimed/running,
  `payload_json->>'contact_id'`) for the already-booked-callback note — see
  below.

### Expected outputs
- `new_exception`: one email to `ALERT_EMAIL_TO` (Kes) per newly-opened
  exception, subject `[<severity>] Cora Alert: new_exception`, body includes
  type, entity, and up to 5 context fields.
- `sales_queue_urgent`: one email per lead, routed per the rules below, to
  `alert_email_rose` and/or `alert_email_taiwo` only — **not** to
  `ALERT_EMAIL_TO`/Kes (he asked to be CC'd, then minutes later asked not to
  be, both 2026-09-11 — the CC mechanism stays in `_smtp_send` /
  `_send_sales_queue_urgent_email` for if that preference changes again, but
  the evaluator always passes `cc_addrs=None`). Sent from `ALERT_EMAIL_FROM`
  (prod: `kes@colaberry.com` as of 2026-09-11, changed from
  `asnakebekele2024@gmail.com`), same as every other alert type. When a
  callback is
  already scheduled, the message names the time and reason. Uses a
  dedicated friendly template (`_send_sales_queue_urgent_email`), not the
  generic `[severity] Cora Alert: <type>` / "Alert ID" / "log in to the
  dashboard" framing every other alert type uses — Rose and Taiwo aren't
  Cora operators. Subject: `Urgent lead needs follow-up - <name or phone>`.
  Body: who needs a follow-up, phone, when they called, why it reached this
  person, the already-booked-callback line (if any), a transcript excerpt
  (400 chars), and an instruction to check the lead's GHL account by phone
  number — **no dashboard link** (revised 2026-09-11, see incident note
  below).

### Routing rules (per Kes, 2026-09-11, revised same day)
1. If the caller's own words name "Rose" or "Taiwo" explicitly
   (word-boundary match, case-insensitive), route to that person —
   overrides topic matching entirely.
2. Else, keyword-match the caller's own words: admissions-topic keywords →
   Rose; payment/IPBC-topic keywords → Taiwo; both hit → both (a genuine
   dual-topic call).
3. If no signal at all, default to **Rose only** — strictly either/or per
   Kes, not "send to both." Sales Queue leads are inherently admissions-track
   calls (New/Cold Lead campaigns); Taiwo's payment/IPBC domain is the
   narrower exception that only applies when the caller's words raise it.

### Revision — classify on the caller's words only, not Cora's own script (2026-09-11)
Real-world bug, caught from an actual live send: contact `+15082722326`'s
email said "the call touched both admissions and payment/IPBC topics" and
went to both Rose and Taiwo. The transcript's only "payment" mention was
Cora's own scripted line — *"no payment, no pressure"*, describing the free
Explorer preview — which is boilerplate present on **nearly every call,
both campaigns** (see `docs/synthflow-cold-lead-prompt.md` /
`synthflow-warm-lead-prompt.md`). The caller never said anything about
payment; "admissions" also only appeared in Cora's own line ("a follow-up
with Admissions"), not the caller's words. New `_extract_caller_turns()`
isolates `"human:"`-prefixed transcript lines before any keyword/name
matching; falls back to the raw transcript if no such lines are found
(unexpected format — fail open rather than classify on nothing). Combined
with the "default to Rose, not both" change above: this same transcript
now correctly routes to Rose only, reason "the topic wasn't clear from
what the caller said, defaulting to admissions."

### Revision — fire-once, no resolution *tracking* (2026-09-11)
Originally `sales_queue_urgent` tracked active/resolved state against
`lead_state.sales_outcome`: skip while a rep hasn't logged an outcome yet,
auto-resolve (silently, no email) once they do, and allow a *new* urgent
episode for the same contact to re-alert after that. Kes's call: drop the
*ongoing tracking* — the Sales Queue itself is already the mechanism for
knowing whether a lead has been addressed, so this alert doesn't need to
re-check `sales_outcome` on every 60s cycle to decide whether a resend is
allowed.

It still checks `sales_outcome` exactly once, at detection time
(`ls.sales_outcome IS NULL` in the query's WHERE clause) — a lead already
resolved before this cycle even runs is excluded from consideration
entirely, never emailed. That's a one-time gate, not tracking: once an
email sends, the contact is permanently done (fire-once tombstone, any
existing `alert_events` row for `sales_queue_urgent:{contact_id}` blocks
forever, written `status='resolved'` at creation — a tombstone, not a
lifecycle state, matching `_evaluate_new_exceptions`'
`exception_notified:{id}` pattern). Kes's exact ask, reconciled: "one time
email, don't check if addressed [afterward]" + "don't alert me about
things already resolved [before you'd even send]" are two different
moments, both now satisfied without contradiction.

**Consequence:** the stale `+15082722326` row from the dead-link incident
below now needs a `DELETE`, not an `UPDATE ... status='resolved'` — under
this simpler dedup, any existing row (any status) blocks a resend, so
resolving in place no longer un-blocks it the way it would have under the
old active/resolved design.

### Incident note — SMTP credentials broken (2026-09-11, unresolved)
While verifying the `ALERT_EMAIL_FROM` change, a live test send failed with
`535 5.7.8 Username and Password not accepted` — and failed identically
when retried with the original From address, proving it's not a From-address
mismatch but the Gmail App Password itself being rejected. **This is
unrelated to any change in this spec** — it means no alert email of any
type (queue lag, exception spike, new_exception, sales_queue_urgent, etc.)
can currently send, silently (non-fatal by design — see module docstring).
Needs a fresh Gmail App Password for whichever account now authenticates
SMTP, set as `SMTP_PASSWORD` on the server. Until fixed, alerts still
evaluate and log correctly (`alert_events` rows are still created) — only
the email step fails.

### Incident note — first live send had a dead link (2026-09-11)
The first deploy included a `dashboard_url` field (`{frontend_url}/lead/{contact_id}`)
in the friendly email. Prod's `FRONTEND_URL` env var was stale
(`http://localhost:3000`, apparently never corrected since nothing read it
before this feature) — the very first real send, for the genuinely-qualifying
lead `+15082722326`, went to Rose and Taiwo with a dead link before the env
var was caught and fixed. Kes's resolution: drop the dashboard link
entirely — Rose and Taiwo check GHL directly, not the Cora dashboard. The
template now ends with "Check this lead's GHL account for full details
(look up by phone: ...)" instead. `settings.frontend_url` is no longer read
anywhere in this module (it was otherwise unused in the codebase already).

### Already-booked callback/appointment handling
If Cora already has a pending `launch_outbound_call` job for the contact
(e.g. `callback_with_time` extracted a specific promised datetime and
scheduled a job for it — `app/core/intent_actions.py::_handle_callback_with_time`
— or any of the other urgent intents fell back to a scheduled retry), the
email includes that `run_at` and the scheduling reason, so Rose/Taiwo know a
follow-up is already booked and when, rather than duplicating outreach.
This only covers callbacks **Cora itself scheduled**. An appointment booked
directly in GHL's own calendar during a human conversation (not through a
Cora phone call) is invisible to Cora — GHL's Calendars/Appointments API
scope is unverified and ingestion is explicitly deferred (spec/22 "out of
scope"). A confirmed `human_transfer_request` (the live transfer already
happened) also has no future time to report — nothing is scheduled because
the handoff already occurred.

### Known edge cases
- A lead's urgent call fires the email, then a *new*, separate urgent call
  happens later for the same contact — does **not** re-alert. Fire-once is
  per contact_id, permanently, not per distinct urgent episode (revised
  2026-09-11 — see Revision note above).
- First-ever run of `new_exception` (empty ledger) seeds the ledger for
  every currently-open exception **without emailing** — avoids a backlog
  flood on first deploy. Verified: prod had 0 open exceptions at deploy
  time, so this path is exercised but doesn't change first-run behavior
  observably this time.
- `sales_queue_urgent` intentionally uses the *always*-urgent intent subset
  (`enrolled`, `callback_request`, `callback_with_time`, `re_engaged`,
  `human_transfer_request` — score ≥ 80 even with zero recency bonus), not
  the full recency-boosted definition `_compute_sales_priority` computes for
  the dashboard UI (e.g. a `failed_booking` call < 30 min old can also score
  ≥ 80 there). Deliberate simplification to avoid duplicating recency math
  in SQL — revisit if that gap turns out to matter.
- "Rosetta" or similar does not false-match "Rose" — word-boundary regex.

### Out of scope
- No resolution email for `sales_queue_urgent` (Kes didn't ask for one; a
  "this lead is no longer urgent" email isn't actionable for sales staff).
- No per-exception-type routing beyond the flat `new_exception` → Kes (all
  types, all severities) — if a specific exception type later needs its own
  routing, treat as a follow-up, not a retrofit here.
- Not a schema change — `alert_events` reused as a generic notified-ledger
  via synthesized `alert_type` keys (`exception_notified:{id}`,
  `sales_queue_urgent:{contact_id}`) rather than adding columns.

## Acceptance criteria
1. A new `exceptions` row (any type) → one email within one metrics cycle
   (≤60s), unless the per-cycle cap (20) is exceeded, in which case it's
   caught on a later cycle.
2. The same exception is never emailed twice.
3. A lead's most recent call has a detected_intent in the always-urgent set,
   duration ≥30s, has a transcript, and `lead_state.sales_outcome IS NULL`
   at detection time → one email to the routed recipient(s), exactly once
   ever for that contact_id. `sales_outcome` is checked once at detection
   (skip if already resolved), never re-checked afterward to gate a resend
   (revised 2026-09-11).
4. Transcript names Rose/Taiwo explicitly → routes to that person regardless
   of topic keywords also present.
5. `ALERT_EMAIL_TO="a@x.com, b@y.com"` → both addresses receive the email
   (previously: broken, single malformed RCPT TO).

## Constraint architecture
- **Must not** require a schema change — see Out of scope.
- **Must not** flood on first deploy — first-run ledger seed is silent.
- **Must not** duplicate-email the same exception or the same lead's active
  urgent episode.
- **Must** cap `new_exception` emails per cycle (20) — an outage that opens
  many exceptions at once must not become a mail-bomb on top of itself;
  `exception_spike` still fires immediately as the aggregate backstop.
- **Preference:** keyword lists (`_ADMISSIONS_KEYWORDS`, `_PAYMENT_KEYWORDS`)
  are plain tuples in `alerting.py`, not a settings-driven config — easy to
  extend by hand if a real transcript gets misrouted; not worth the
  indirection of a DB-backed config for a two-person routing table.

## Eval design
`tests/unit/test_alerting.py` (31 cases, current as of the fire-once +
caller-turns revisions): `_route_sales_queue_recipients` (admissions-only,
payment-only, both keywords hit in caller's words, name override, both
named, name-substring false-positive guard, no-signal defaults to Rose
only, `None` transcript, bot-script payment mention ignored) +
`_extract_caller_turns` (ignores bot lines including the "no payment, no
pressure" script line, falls back to raw text when no speaker prefixes
found, handles `None`) + `_sales_queue_routing_reason` (mirrors the above)
+ `_send_alert_email` (comma-split fix, `to_override` bypass, SMTP-disabled
no-op) + `_send_sales_queue_urgent_email` (friendly-not-generic template,
phone fallback when no lead name, CC mechanism works when given) +
`_evaluate_new_exceptions` (first-run silent seed, subsequent-run emails
once, no-open-rows no-op) + `_evaluate_sales_queue_urgent` (new urgent lead
emails + creates a fire-once tombstone, already-sent skips forever
regardless of status, no qualifying leads no-op, disabled-by-default-off
gate no-op, already-booked-callback time+reason included, query-text guard
for the `ls.sales_outcome IS NULL` filter).

No prior test coverage existed for `alerting.py` before this change — the
above covers only the spec/30 additions; the pre-existing evaluators
(`queue_lag_exceeded`, `exception_spike`, etc.) remain untested. Flagged as
pre-existing debt, not fixed here.
