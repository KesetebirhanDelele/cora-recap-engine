# GHL integration kit (project-neutral)

How to integrate an application with GoHighLevel (GHL / LeadConnector) using the pattern **"the app writes a contact custom field; a GHL
workflow reacts"**. Contains verified API behaviour, environment variables, and safety standards. It deliberately contains **no
project-specific business rules** (no message cadences, timings, caps, wording, offers or campaign names): those are decisions for each project's own
spec and configuration. Anything marked **UNVERIFIED** must be confirmed against the live account or GHL docs before it is relied on.

## 1. The pattern
- The app does not call a messaging endpoint. It writes custom fields on a contact; a GHL workflow (trigger: custom field changed) sends the SMS /
  email / starts the next step. Writing the field **is** the send. Define your own trigger fields in your spec, one per action
  (for example: an SMS-text field, an email-subject field, an email-body field), and keep their labels in env.
- Consequences: (a) a successful write means "handed to GHL", **not** delivered, so track delivery separately (section 6); (b) a wrong write
  sends a real message, so gate it (section 7); (c) the workflow, not your code, enforces GHL DND.
- Writes are by custom-field **UUID**. `GET /contacts/{id}` returns custom fields as `{id, value}` only (no names), so resolve label -> UUID once
  from `GET /locations/{locationId}/customFields` and cache. Env holds field **labels**, never values.
- Multi-select fields come back as arrays; unwrap them.

## 2. Auth and tokens (separate mechanisms, do not conflate)
| Mechanism | Typical use | Notes |
|---|---|---|
| Private Integration token (contacts) | contacts read/write, custom fields, tasks | scopes: contacts.readonly, contacts.write, locations/tasks.write, locations/customFields.readonly |
| Second Private Integration token (conversations) | conversations search, messages, recordings, transcriptions, internal-comment notes, email status | scopes: conversations.readonly, conversations/message.readonly, conversations/message.write. It does not write contacts, and the contacts token gets 401 "token is not authorized for this scope" on `/conversations/*`. Use one client per token (an `api_key_override`). |
| OAuth Marketplace app (Conversation Provider) | writing call-type messages (recording/transcript) into conversations | only if needed; internal-comment notes via the second token is simpler |
- Headers: `Authorization: Bearer <token>`, `Version: 2021-07-28` by default. The email-status endpoint needs `Version: 2021-04-15`.
- Base URL `https://services.leadconnectorhq.com`; scope every call by the location id.
- Resilience: retry 429, 500/502/503/504, timeouts **and** GHL's disguised timeout (HTTP 401 with body "Command timed out"); bounded exponential
  backoff. Other 4xx raise immediately.
- **Rate limit:** bursts return 429. Put a shared limiter in front of all GHL calls (about 5 per second was safe) and keep parallelism modest behind it.

## 3. Environment variables
Required to read: `GHL_BASE_URL`, `GHL_API_KEY`, `GHL_LOCATION_ID`. Required for conversations: `GHL_CONVERSATIONS_API_KEY`.
| Variable | Purpose | Default / rule |
|---|---|---|
| GHL_BASE_URL | API host | https://services.leadconnectorhq.com |
| GHL_API_KEY | contacts / fields / tasks token | secret, never logged |
| GHL_LOCATION_ID | sub-account (location) id | required |
| GHL_CONVERSATIONS_API_KEY | conversations token | secret; different scopes |
| GHL_TIMEOUT_SECONDS / GHL_RETRY_MAX | transport | e.g. 30 / 3 |
| GHL_WRITE_MODE | `shadow` or `live` | **shadow** by default |
| GHL_WRITE_SHADOW_LOG_ONLY | log payloads, make no API call | **true** by default |
| GHL_WRITE_<AREA> | one boolean switch per write area (contact fields, notes, tasks, ...) | false until a human approves |
| GHL_WRITE_INTERNAL_COMMENT, GHL_WRITE_CONVERSATION_LOG | independent gates for the conversation write paths | false |
| GHL_FIELD_<NAME> | custom field **label** for each field the app reads or writes, including each trigger field | labels, not values |
| GHL_TASK_PIPELINE_ID, GHL_TASK_DEFAULT_OWNER_ID | tasks | optional |
| GHL_FETCH_CONVERSATION_HISTORY, GHL_CONVERSATION_HISTORY_LIMIT | read the thread before generating a message | optional |
| GHL_MARKETPLACE_CLIENT_ID/_SECRET/_SHARED_SECRET, GHL_OAUTH_REDIRECT_URI, GHL_CONVERSATION_PROVIDER_ID, GHL_OAUTH_TARGET_LOCATION_ID | OAuth Conversation Provider app | only if used |
- Live requires **both** `GHL_WRITE_MODE=live` and `GHL_WRITE_SHADOW_LOG_ONLY=false`.
- Operational values that an operator may change at runtime (caps, delays, thresholds, alert recipients) belong in a DB-backed config table read through one helper
  (DB -> env -> default), not in env or read straight from the settings object. A Settings page that shows one value while the code reads another is a real, previously hit bug.

## 4. Contacts
- Find by phone with `search_contact_by_phone` (E.164). If your app keeps its own ids (e.g. phone-derived), resolve to the real GHL id before writing. "Not found" is a normal result
  (one-off callers, wrong numbers): record a no-op, do not fail the job.
- Tasks accept only `title`, `dueDate`, `completed` (bool), `assignedTo`; `status` / `description` return 422.
- Never assume a contact has an email or a phone. A hand-off to a contact with no address is a data gap, not a delivery failure: classify it separately so it cannot trigger delivery alarms.

## 5. Do-Not-Disturb (DND) and opt-outs
- Set with `PUT /contacts/{id}` and `{"dndSettings": {"Call"|"SMS"|"Email": {"status": "active"|"inactive", "message": "...", "code": "..."}}, "dnd": true}`
  (`dnd: true` only when all three channels are set). GHL workflows and send actions honour DND, so **this, not your database, stops GHL-side sends**.
- GHL will not overwrite a **permanent** DND (for example one created by a STOP reply): read first, add only the missing channels, and verify by reading the contact back (do not trust the 200).
- Opt-outs can also exist as **tags** ("do not contact", "do not call again", "not interested") with no DND set. Check tags **and** `dndSettings` **and** your own flags before any call, text or email.
- Read inbound replies and call transcripts for opt-out wording (rules, plus an LLM judge for unclear cases, with a human review queue). Filter echoes: GHL logs your own outbound emails
  and unsubscribe boilerplate as inbound.

## 6. Reading what actually happened
- SMS: message list -> `status` (delivered / undelivered / sent ...); provider error codes in `meta.error`.
- Email: the list does not show delivery. Take `meta.email.messageIds[0]` and call `GET /conversations/messages/email/{id}` with `Version: 2021-04-15` (conversations token).
- Useful message fields: `direction`, `messageType` (TYPE_SMS / TYPE_EMAIL / TYPE_CALL), `source` (`workflow` = a GHL workflow, e.g. one your field write triggered; `app` = sent from the GHL app/UI
  or an AI responder), `userId`, `dateAdded`, `status`.
- Recordings: `GET /conversations/messages/{id}/locations/{loc}/recording`. Transcription: `GET /conversations/locations/{loc}/messages/{id}/transcription` (empty if transcription is not enabled).
- Match your hand-offs to GHL deliveries per contact inside a time window you choose, call an unmatched hand-off "unconfirmed" after a grace period you choose, and judge health **per channel on
  delivered, not attempted**. Alert on silence and on a low delivery rate; send alerts with fixed subjects and hidden headers so a mail rule can match them exactly.
- Messages GHL sends on its own (auto-responders, STOP confirmations, staff replies) are outside your app's controls; detect them as "delivered with no hand-off".

## 7. Safety standards (recommended for any project; the numbers are yours to set)
1. Shadow by default; live behind two switches; per-area write flags; every write idempotent (dedupe record) and safe to retry.
2. A pre-send gate for every text: legal quiet hours in the lead's timezone (US TCPA: confirm with counsel), a hard daily cap, pacing, a segment limit, a forbidden-content check, a ledger row
   per text written under a lock, and a rule for what happens to overflow. Count segments correctly (GSM-7 vs UCS-2) and normalise curly quotes, which silently inflate segment counts.
3. Delivery monitoring plus a silence alert to a named owner; alert emails carry hidden headers and fixed subjects; real alerts never contain the word TEST; run a drill.
4. Record the real outcome of every generated message (sent / skipped with a reason), not just "pending".
5. Keep one source of truth for anything customer-facing that appears in several places (offer, prices, links, terms) and test that prompts and docs agree.
6. Tests never send real messages; DB-specific SQL tests are opt-in via an env var; lint and type-check clean; update docs/specs in the same change.

## 8. Using GHL's internal Voice AI agent  (**UNVERIFIED - research before designing**)
Carries over unchanged: the field-write -> workflow pattern, contact lookup, DND/opt-out checks, the text gate, delivery monitoring, shadow gating.
Verify, then write a spec before code:
1. **How a call starts** - expect a workflow action fired by a field/tag change rather than an API launch. Who owns timing, concurrency and calling-hours limits?
2. **How results return** - (a) a workflow webhook action, (b) a Voice AI / call webhook event, or (c) a `TYPE_CALL` conversation message you read (recording and transcription endpoints above exist
   for native calls). This decides how call outcomes are stored and how soon after the call they are known.
3. **Outcome vocabulary** - map GHL's status / end reason / voicemail detection to your own outcomes.
4. **Agent prompt and knowledge base live inside GHL** (AI Agents), not in your repo: treat them as external places to keep consistent.
5. **Call DND and consent** - confirm whether Voice AI honours Call DND and calling hours itself; keep your own gate anyway.
6. **Transfer / callback / booking actions** and their events.

## 9. Start-up checklist for a new repo
1. Copy this folder to `.claude/skills/ghl-integration/`.
2. Create the two tokens with the scopes in section 2; note the location id; create the custom fields and the workflow(s); note field labels.
3. Add the env block from section 3; keep `GHL_WRITE_MODE=shadow` until a human approves live.
4. Build the adapter: auth headers, retry incl. the disguised 401, a shared rate limiter, per-token clients, shadow gate, label->UUID cache, `search_contact_by_phone`, `set_dnd`, `get_email_status`.
5. Before the first live send: pre-send gate and ledger, opt-out checks (flags, DND, tags), delivery monitoring, a silence alert.
6. Decide your own business rules (cadence, timing, wording, caps, thresholds) and put them in the project spec and runtime config, not in this kit.
7. Write acceptance criteria and evals (happy / edge / failure / replay).
