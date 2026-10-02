# GHL integration kit (portable)

Everything a new app needs to integrate with GoHighLevel (GHL / LeadConnector) the way Cora does: **write a contact custom
field, a GHL workflow reacts**. Distilled from cora-recap-engine (verified in production through 2026-10-02). Where something is
**UNVERIFIED** it says so - do not treat it as fact; verify first.

Deeper background in the source repo: `docs/ghl-integration-pattern-reference.md` (Jul 31: auth, lookup, create, conversation
logging, retries), `directives/spec/16_ghl_integration.md`, `35_delivery_health.md`, `36_optout_dnd.md`, `34_sms_gate_and_daily_budget.md`.
Those predate several lessons below; **this kit wins on conflicts**.

## 1. The pattern: "update a contact field -> a workflow acts"
- The app never calls a messaging endpoint. It writes custom fields on the contact; a GHL workflow (trigger: custom field changed)
  sends the SMS / email / starts the next step. In Cora: Support Ticket #4 = SMS text, Support Ticket #2 + Message = email subject/body.
  Writing the field **is** the send. Name your own trigger fields in a spec and keep them in env (section 3).
- Consequences: (a) the write succeeding means "handed to GHL", **not** delivered; track delivery separately (section 6);
  (b) a bad write sends a real message, so gate it (section 7); (c) the workflow, not your code, enforces GHL DND.
- Writes are by field **UUID**. GET /contacts returns custom fields as `{id, value}` only (no names), so resolve label -> UUID once from
  `GET /locations/{locationId}/customFields` and cache. Env holds field **names** (labels), never values.
- Multi-select fields come back as arrays (`["Yes"]`); unwrap.

## 2. Auth and tokens (three mechanisms, do not conflate)
| Mechanism | Used for | Notes |
|---|---|---|
| Private Integration token (`GHL_API_KEY`) | contacts read/write, custom fields, tasks | scopes: contacts.readonly, contacts.write, locations/tasks.write, locations/customFields.readonly |
| Second Private Integration token (`GHL_CONVERSATIONS_API_KEY`) | conversations search, messages, recordings, transcriptions, InternalComment notes, email status | conversations.readonly, conversations/message.readonly, conversations/message.write. **It cannot write contacts; the contacts token gets 401 "token is not authorized for this scope" on /conversations/*.** Use one client per token (`api_key_override`). |
| OAuth Marketplace app (Conversation Provider) | writing call-type messages (recording/transcript) into conversations | only if you need `type="Call"` messages; InternalComment notes via the second token is the simpler route |
- Headers: `Authorization: Bearer <token>`, `Version: 2021-07-28` (default). The email-status endpoint needs `Version: 2021-04-15`.
- Base URL `https://services.leadconnectorhq.com`; always scope by `GHL_LOCATION_ID` (`locationId` query param on searches).
- Resilience: retry 429, 500/502/503/504, timeouts **and** GHL's disguised timeout (HTTP 401 with body "Command timed out"); exponential
  backoff, bounded (`GHL_RETRY_MAX=3`). Other 4xx raise immediately.
- **Rate limit:** bursts get 429. Cap total GHL calls to about 5/s across threads (a shared limiter); parallelism of 6-8 workers is fine behind it.

## 3. Environment variables (copy to the new app's `.env.example`)
Required to read: `GHL_BASE_URL`, `GHL_API_KEY`, `GHL_LOCATION_ID`. Required for conversations: `GHL_CONVERSATIONS_API_KEY`.
| Variable | Purpose | Default / rule |
|---|---|---|
| GHL_BASE_URL | API host | https://services.leadconnectorhq.com |
| GHL_API_KEY | contacts/fields/tasks token | secret, never logged |
| GHL_LOCATION_ID | sub-account (location) id | required |
| GHL_CONVERSATIONS_API_KEY | conversations token | secret; separate scopes |
| GHL_TIMEOUT_SECONDS / GHL_RETRY_MAX | transport | 30 / 3 |
| GHL_WRITE_MODE | `shadow` or `live` | **shadow** by default |
| GHL_WRITE_SHADOW_LOG_ONLY | log payloads, make no API call | **true** by default |
| GHL_WRITE_CONTACT_FIELDS, _NOTES, _TASKS, _SUMMARY, _CAMPAIGN_STATE, _FINALIZATION | per-area write switches | false until approved |
| GHL_WRITE_INTERNAL_COMMENT, GHL_WRITE_CONVERSATION_LOG | independent gates for the conversation paths | false |
| GHL_FIELD_<NAME> | custom field **label** per field the app writes/reads (e.g. SUPPORT_TICKET_2, SUPPORT_TICKET_4, MESSAGE, AI_CAMPAIGN, MARK_AS_LEAD) | names, not values |
| GHL_TASK_PIPELINE_ID, GHL_TASK_DEFAULT_OWNER_ID | tasks | optional |
| GHL_FETCH_CONVERSATION_HISTORY, GHL_CONVERSATION_HISTORY_LIMIT | read thread history before generating a message | false / 15 |
| GHL_MARKETPLACE_CLIENT_ID/_SECRET/_SHARED_SECRET, GHL_OAUTH_REDIRECT_URI, GHL_CONVERSATION_PROVIDER_ID, GHL_OAUTH_TARGET_LOCATION_ID | OAuth Conversation Provider app | only if used |
Live requires **both** `GHL_WRITE_MODE=live` and `GHL_WRITE_SHADOW_LOG_ONLY=false`; mode comes from settings with a DB override layer (`app_config`) so ops can flip it without a deploy.
Runtime knobs that belong in a DB/config table, not env: SMS daily segment cap (hard max 999), min gap between texts, per-minute cap, max segments,
offer name/facts/forbidden terms, delivery-health thresholds, alert recipients. **Rule: a value that appears on a Settings page must be read through
the config helper (DB -> env -> default); reading the settings object directly caused a live bug (UI said 30, env said 2).**

## 4. Contacts
- Find by phone: `search_contact_by_phone` (E.164). Cora's own ids may be phone-derived; resolve to the real GHL id before writing. Handle "not found" as
  a first-class result (a one-off caller or robocall has no contact): record a no-op, do not fail the job.
- Custom-field and task quirks: tasks accept only `title`, `dueDate`, `completed` (bool), `assignedTo` (status/description -> 422).
- Never assume a contact has an email or phone: a hand-off to a contact with no email is **not a delivery failure** (Cora tracks it as "no email on file").

## 5. Do-Not-Disturb (DND) - opt-outs must reach GHL
- Set via `PUT /contacts/{id}` with `{"dndSettings": {"Call"|"SMS"|"Email": {"status": "active"|"inactive", "message": "...", "code": "..."}}, "dnd": true}`
  (`dnd: true` only when all three channels are set). GHL workflows and send actions honour DND, so **this, not your database, stops GHL-side sends**.
- GHL will not overwrite a **permanent** DND (e.g. a STOP reply): read first, only add missing channels; verify by reading the contact back (do not trust the 200).
- Opt-outs also appear as **tags** ("do not contact", "do not call again", "not interested") with no DND set. Check tags **and** `dndSettings` **and** your own flags
  before any call, text or email. (Cora's text check does; its call gate did not - open gap as of 2026-10-02.)
- Read inbound replies and call transcripts for opt-out wording (classifier + LLM judge, confidence-gated, review queue for unclear). Filter echoes: GHL logs your own
  outbound emails (and unsubscribe boilerplate) as inbound.

## 6. Reading what actually happened (delivery, replies, calls)
- SMS: message list -> `status` (delivered / undelivered / sent...), error code in `meta.error` (Twilio 30003, 30023 ...).
- Email: the list does not show it; take `meta.email.messageIds[0]` and `GET /conversations/messages/email/{id}` with `Version: 2021-04-15` (conversations token).
- Message fields worth keeping: `direction`, `messageType` (TYPE_SMS / TYPE_EMAIL / TYPE_CALL), `source` (`workflow` = a GHL workflow, e.g. triggered by your field write; `app` = sent from the
  GHL app/UI or an AI responder; plus `userId`), `dateAdded`, `status`.
- Match hand-offs to deliveries per contact in a time window (hand-off -2 .. +60 min; calls +90). "Unconfirmed" after 15 minutes. Judge health **per channel on delivered**, never on attempted;
  alert on silence (no confirmed delivery for 2.5 days) and on low delivery rate. Spec: `35_delivery_health.md`.
- Recordings: `GET /conversations/messages/{id}/locations/{loc}/recording`; transcription: `GET /conversations/locations/{loc}/messages/{id}/transcription` (404/empty if transcription is not enabled).
- Texts that GHL sends on its own (auto-responders, STOP confirmations, staff replies) are **outside your gate and cap**; detect them as "delivered with no hand-off".

## 7. Safety standards to copy (these prevented or caught real incidents)
1. Shadow by default; live behind two switches; per-area write flags; every write idempotent (dedupe table) and safe to retry.
2. Pre-send SMS gate for every text: TCPA hours 8-21 in the lead's timezone, Pacific-day segment budget with a hard maximum (999), pacing (5 s gap, 12/min), <=2 segments,
   forbidden-content check, reservation ledger under an advisory lock, overflow deferred to the next legal time. Ledger row per text; mark sent/failed.
3. Deliver-status monitoring and a silence alert that emails an owner (+ cc). Alert emails carry hidden headers (`X-Cora-Alert: health`, `-Channel`, `-State`) and fixed subjects so
   a mail rule can match them; never put the word TEST in a real alert; run a drill.
4. Record the real outcome of every generated message (`sent` / `skipped` with a reason), not just "pending".
5. One source of truth for the offer (course name, facts, price, links, retired terms) read by every generator, plus a test that fails when prompts/docs disagree (spec 38).
6. Tests never send real comms; Postgres-only SQL tests are opt-in via an env var; ruff + type-check clean; docs/specs updated in the same change.

## 8. Using GHL's internal Voice AI agent instead of Synthflow  (**UNVERIFIED - research before building**)
What carries over unchanged: the field-write -> workflow pattern, contact lookup, DND/opt-out checks, SMS gate, delivery-health, offer consistency, shadow gating.
What changes and must be verified in the GHL account/API docs before designing:
1. **How a call is started.** Synthflow was launched by API with a payload. For GHL Voice AI expect a workflow action ("make Voice AI call") fired by a contact field/tag change.
   Confirm the trigger, who owns call timing (your scheduler vs a workflow Wait step), concurrency and calling-hours limits.
2. **How results come back.** Synthflow posted a webhook. For GHL, determine whether results arrive as (a) a workflow webhook action, (b) a Voice AI/call webhook event, or (c) a
   `TYPE_CALL` conversation message you poll (recording + transcription endpoints above exist for native calls). This decides how the call-event table is fed and how long after a
   call the outcome is known (Synthflow logs reached Cora 10-15 minutes later).
3. **Outcome vocabulary.** Map GHL's call status / end reason / voicemail detection to your outcomes (answered, voicemail, no answer, failed) and to "delivered" for health tracking.
4. **Agent prompt and knowledge base live in GHL** (AI Agents -> Voice AI / Knowledge Base), not in your repo. Treat them as external places to keep consistent (offer record + checklist).
5. **Call DND/consent.** Confirm whether GHL Voice AI honours the contact's Call DND and calling-hours rules itself; keep your own gate regardless.
6. **Transfer/callback/booking actions** and their webhooks.
Output of that research should be a short spec in `directives/spec/` before code.

## 9. Start-up checklist for the new repo
1. Copy this skill folder to `.claude/skills/ghl-integration/` (or the user-level skills folder).
2. Create the two Private Integration tokens with the scopes in section 2; record location id; create the custom fields and the workflow(s); note field labels.
3. Add the env block from section 3; keep `GHL_WRITE_MODE=shadow` until a human approves live.
4. Build/copy the adapter: auth headers, retry (incl. disguised 401), shared 5/s limiter, `api_key_override`, shadow gate, label->UUID cache, `search_contact_by_phone`, `set_dnd`, `get_email_status`.
5. Before the first live send: SMS gate + ledger, opt-out checks (flags, DND, tags), delivery-health, silence alert with headers, offer record.
6. Write acceptance criteria and evals (happy / edge / failure / replay) per the repo's spec standard.
