# spec/19_ghl_marketplace_conversation_provider.md

## Implementation status

| Area | Status |
|---|---|
| Marketplace app created (Private, Sub-Account, Agency-only, White-label) | IMPLEMENTED |
| OAuth Auth config (redirect URI + scopes) | IMPLEMENTED |
| Client ID / Secret / Shared Secret generated | IMPLEMENTED (stored in `.env`, not committed) |
| App Profile (description, preview image) | IMPLEMENTED |
| Pricing (Free) | IMPLEMENTED |
| App published (v1.0.0, Live) | IMPLEMENTED |
| Conversation Provider registered (`Cora Voice Calls`, type `Call`, custom provider) | IMPLEMENTED |
| OAuth authorization flow verified against GHL sandbox test account | IMPLEMENTED (manual, browser-based) |
| Backend OAuth callback route (`/oauth/callback`) | PLANNED — not yet built |
| Per-location token storage + refresh handling | PLANNED — not yet built |
| Real install against production Colaberry GHL location | PLANNED — deferred until backend exists |
| Call-log write method (recording + transcript → Conversations) in `app/adapters/ghl.py` | PLANNED — not yet built |

---

## Purpose

This documents a **second, separate GHL connection mechanism** from the one described in
[`spec/16_ghl_integration.md`](16_ghl_integration.md). That existing integration uses a **Private
Integration JWT token** (`GHL_API_KEY`) for contacts, custom fields, tasks, and notes — a
Private Integration token **cannot** register or use a Conversation Provider.

Writing call recordings and transcripts into a GHL contact's **Conversations** activity timeline
requires a **Marketplace OAuth app** with a registered **Conversation Provider**. This doc covers
the dashboard-side setup of that app. The Python backend work to actually consume it (OAuth
callback, token storage, call-log writes) is tracked here as PLANNED and specced separately before
implementation, per this repo's spec-first rule for high-stakes/production-impacting changes.

---

## Why a new mechanism was needed

| | Private Integration (`spec/16`) | Marketplace OAuth app (this doc) |
|---|---|---|
| Auth | Static JWT token, single key | OAuth 2.0, per-location access + refresh tokens |
| Scope | Contacts, custom fields, tasks, notes | Same, plus Conversations/Conversation Provider |
| Can write to Conversations activity? | No | Yes — via registered Conversation Provider |
| Token lifecycle | Manual regeneration on 401 | Access token ~24h, refresh token ~1yr, auto-refresh required |
| Setup location | GHL location → Settings → Private Integrations | GHL Marketplace developer portal (`marketplace.gohighlevel.com`) |

---

## Marketplace app configuration (as built)

**App name:** Cora

**Distribution settings:**
- App type: **Private**
- Target user: **Sub-Account** (not Agency — Cora writes to individual location contact records, not agency-level resources)
- Who can install: **Agency only** (restricts install to Colaberry's own agency, not a public install link)
- Listing type: **White-label** (cosmetic only for a Private app)
- Category: **Sales CRM** (closest available fit — no "Communication" category existed in the dropdown)

**App description** (300 chars, required field):
> Cora is an internal AI voice-call platform built for Colaberry's admissions and support teams. It places and receives calls, transcribes conversations, scores lead intent, and routes follow-ups, then logs recordings, transcripts, and call outcomes back into GoHighLevel contact activity in real time.

**Preview image:** 960×540 PNG (16:9), Colaberry logo canvas-padded (not stretched) to meet GHL's aspect ratio requirement.

**Pricing:** Free, no billing meters.

### Auth (BUILD → Advanced Settings → Auth)

- **Redirect URI:** `https://<cora-backend-domain>/oauth/callback`
  - Must NOT contain the substring `ghl` or `gohighlevel` anywhere in the URL — GHL's white-label validation rejects it (first attempt used `/ghl/oauth/callback` and was rejected; corrected to `/oauth/callback`).
  - This must exactly match whatever route the backend eventually implements.
- **Scopes selected** (minimal set for this use case):
  - `conversations.readonly`
  - `conversations/message.readonly`
  - `conversations/message.write` — the critical scope; covers outbound webhooks, inbound messages, call logs, attachments, and status updates
  - `contacts.readonly`
  - `contacts.write`
  - Deliberately **not** requesting `conversations.write` (create/update/delete conversation threads) or `conversations/reports.readonly` — not needed for the call-logging use case.

### Client keys (MANAGE → Secrets)

- Client ID, Client Secret, and Shared Secret generated and stored in `.env` as:
  - `ghl_marketplace_client_id`
  - `ghl_marketplace_client_secret`
  - `ghl_marketplace_shared_secret`
- **Client Secret is shown only once at generation time** — already captured. If lost, must be regenerated (invalidates the old one).
- These are distinct from `GHL_API_KEY` (the Private Integration token used by the existing `spec/16` integration) — both will coexist in `.env`.

### Conversation Provider (BUILD → Modules → Conversation Providers)

- **Name:** `Cora Voice Calls`
- **Type:** `Call`
- **Delivery URL:** webhook endpoint on the Cora backend that will receive provider events (e.g. `https://<cora-backend-domain>/conversations/webhook`) — **not yet built**, same status as the OAuth callback route.
- **Custom conversation provider:** checked (Yes). This is deliberate — leaving it unchecked marks the provider as a *default* provider, which can replace/override GHL's native calling channel for the location. Checking "custom" makes Cora an **additive** channel that logs calls alongside whatever native GHL phone/calling setup the location already uses, without taking it over.
- **Conversation Provider ID:** `6a4eebb1f41b5b39ff760caf` — required on every call-log write (`conversationProviderId` field).

### Publish

- App version `1.0.0` published to **Live** status. Private apps skip the Marketplace review queue and go live immediately.
- Versioning notes (from GHL's own guide, for future changes): only 1 draft/disapproved version at a time, max 8 versions per app, minimum 3-day notice before deprecating a version. The Conversation Provider module was editable in place on the already-Live version — no new draft version was required for this addition.

---

## OAuth flow verification (sandbox)

Before connecting to the real production Colaberry GHL account, the OAuth flow was verified end-to-end against a disposable GHL trial account, to avoid touching production per this repo's rule that integration tests must never touch production without explicit opt-in.

1. **Testing tab → Create app test account** → created account named `Cora Sandbox` (test account ID: `s1lFGXbTVHd8E7q6Q7Ay`).
2. Private apps do **not** appear in the in-account Marketplace search (confirmed — searching "Cora" inside the sandbox account's own Marketplace UI returned zero matches for this app). Private app installs must go through a manually constructed OAuth authorization URL instead.
3. **Authorization URL** (built manually, since there is no "Install" button surfaced anywhere in the dashboard for a Private app):

   ```
   https://marketplace.gohighlevel.com/oauth/chooselocation?response_type=code&client_id=<CLIENT_ID>&redirect_uri=<URL-ENCODED_REDIRECT_URI>&scope=<URL-ENCODED_SPACE-SEPARATED_SCOPES>
   ```

   Scopes, space-separated then URL-encoded: `conversations.readonly conversations/message.readonly conversations/message.write contacts.readonly contacts.write`

4. Opened in an **incognito window**, logged in with the `Cora Sandbox` test account credentials (not the real Colaberry agency session — the same browser was already authenticated into the real account, which caused an initial mix-up where a link opened the real account instead of the sandbox).
5. Consent screen appeared at `/oauth/chooselocation`, showing the requested scopes and a location picker ("Install under all locations in Cora Sandbox" — left "Enable automatic installation to future locations" unchecked).
6. Clicked Proceed → redirected to `https://<cora-backend-domain>/oauth/callback?code=<AUTH_CODE>`.
7. Browser showed `ERR_CONNECTION_REFUSED` (expected — no backend route exists yet), but the `code` query parameter was present in the address bar, confirming **the OAuth handshake itself completed successfully** on GHL's side. The code is single-use and short-lived; it was not captured or used further.

**Conclusion:** the Marketplace app, scopes, redirect URI, and Conversation Provider are all correctly configured and GHL will issue a valid authorization code. The only remaining gap is backend code to receive that code, exchange it for tokens, store them per-location, and use the Conversation Provider ID to write call messages.

---

## Common pitfalls hit during setup (for future reference)

- **"Agency" vs "Sub-Account" target user**: Agency grants agency-level access (sub-account management, reselling) — wrong for an app that writes to individual contact conversations. Use Sub-Account.
- **Redirect URL containing "ghl"/"gohighlevel"**: rejected by GHL's white-label validation even for Private apps. Applies to the App Profile "Website" field too per GHL's own guidance text.
- **Client keys ≠ Auth scopes/redirect URI**: they live on a separate page (MANAGE → Secrets), and the Publish confirmation dialog flags them separately with a warning icon if not yet generated.
- **Private apps are not searchable in a sub-account's in-app Marketplace**, even when logged into a test/sandbox account created specifically for this app. There is no visible "Install" action in the Testing tab's row-level `⋮` menu either (only Copy ID / Delete Account) — the install must be triggered via a manually constructed `/oauth/chooselocation` URL.
- **Browser session reuse**: clicking an "open account" link while already logged into a different GHL account (e.g. the real production agency) in the same browser will open the already-authenticated account instead of prompting a fresh login. Use an incognito window for any sandbox-account testing to avoid accidentally acting on the real account.
- **`ERR_CONNECTION_REFUSED` on the redirect URI is expected** at this stage of setup — it doesn't mean the OAuth flow failed, only that no backend server exists yet at that URL. Check for the `code` query parameter to confirm success independent of the connection error.

---

## Next steps (not yet started)

1. Spec (per this repo's spec-first rule) and implement:
   - `POST /oauth/callback` route — receives `code`, exchanges for access + refresh token via GHL's token endpoint, persists per-location tokens
   - Token storage table + refresh routine (access tokens expire ~24h; refresh tokens ~1yr, rotate on use)
   - `POST /conversations/webhook` route for the Conversation Provider's Delivery URL
   - New adapter method in `app/adapters/ghl.py` (or a new `app/adapters/ghl_conversations.py`) to write call messages (`POST /conversations/messages/outbound`) including `conversationProviderId=6a4eebb1f41b5b39ff760caf`, recording attachment, call duration/status
   - Open question: whether GHL supports writing a caller-supplied transcript at message-creation time, or only exposes transcripts it generates itself from the recording (read-only `Get/Download transcription by message ID` endpoints were the only ones confirmed during research — no write endpoint was found). Needs verification against live API responses before the transcript-write path is built.
2. Real install: repeat the sandbox OAuth flow against the actual production Colaberry GHL location, once the backend routes exist to receive it.
