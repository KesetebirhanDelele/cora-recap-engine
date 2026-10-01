# Spec 32 — Wrong Date Monitor (next class start / next open house)

| Area | Status |
|---|---|
| `next_open_house_date` setting (Settings page + Streamlit + prompts) | **BUILT, local-tested.** Not deployed. |
| Detector (`app/core/wrong_date_guard.py`) | **BUILT.** Pure logic, 30 unit tests. |
| Scanner + alert email + correction SMS (`app/services/wrong_date_monitor.py`) | **BUILT.** DB-backed tests against real Postgres. |
| Dashboard tile + page (`/wrong-dates`) | **BUILT.** Badge on the home tile = open incidents. |
| Hetzner deploy (`alembic upgrade head` = migration 0024, rebuild) | **NOT DONE** — awaiting Kes's local-test sign-off. |

## Problem statement
Leads were being told the wrong next-class-start / next-open-house date
(stale dashboard value, or an AI-invented date — the prompts never carried an
open house date before this spec). Kes needs (1) an email the moment it
happens, (2) a dashboard tile listing affected leads, (3) a one-click
correction SMS.

Source of truth is **dashboard Settings** (`app_config.next_class_start`,
`app_config.next_open_house_date`) — not hard-coded — so next term needs no
code change. As of 2026-09-30: class start `November 12, 2026`, open house
`October 29, 2026`.

## How it works
1. Every 60s metrics cycle (`alerting.evaluate_alerts`) → `scan_wrong_dates`.
2. Scans `outbound_messages` (sms + email, status not `shadow`/`failed`) from the
   last 24h that have no incident yet.
3. Each date in subject+body is classified by the **nearest keyword in its
   sentence**: class (class, cohort, program starts…), open house (open house,
   info session, RSVP, webinar, Eventbrite), appointment (call you, appointment,
   scheduled, follow up, chat…), else other. **Only class/open-house dates are
   compared; appointment/other dates are never flagged.** Tie → class/open house wins.
4. A mismatch → one `wrong_date_incidents` row (UNIQUE per message → idempotent
   under retries/concurrent workers), one email (`wrong_date_message`, to
   `ALERT_EMAIL_TO`), and one aggregate `alert_events` row while any incident is open.
5. Tile `/wrong-dates`: **Send correction SMS** / **Dismiss**.

## Bulk actions (added 2026-09-30)
- **One correction per lead.** Sending a correction closes every other open
  incident for the same lead (they were told wrong things in several messages;
  one corrected SMS covers all), so a lead is never texted twice.
- **Send correction SMS to all** - one click; the UI calls
  `POST /dashboard/actions/send-date-correction-all` in batches of
  `BULK_SEND_MAX_LEADS` (25) until `remaining` is 0. One SMS per lead. Each lead
  commits independently (failures keep what was already sent and stay open);
  3 consecutive failures stop the run; the field id is resolved once per run.
  Shadow mode sends nothing. Confirm dialog shows the exact text and lead count.
- **Dismiss all older incidents** - `POST /dashboard/actions/dismiss-wrong-dates-bulk`
  dismisses open incidents sent before the date settings were last saved
  (`MAX(updated_at)` of next_class_start / next_open_house_date / live_open_house_link),
  since those messages used the old values. No messages sent; audited.

## Date expiry + free-signup fallback (added 2026-09-30)
- **Auto-clear:** every metrics cycle, `app/services/date_expiry.py` sets
  `next_class_start` / `next_open_house_date` to `""` the day AFTER the date
  (in `default_timezone`, America/Chicago). One email (`date_setting_expired`)
  per clear; audited (`config_expired`). Conditional UPDATE, so a date an
  operator saved in the meantime is never overwritten. **Year-less values
  ("Oct 29") are never auto-cleared** - always include the year in Settings.
- **Unset** = blank, or the legacy words upcoming/tbd/tba/none/n-a/null. It does
  NOT include free text like "Q3 2026".
- **Prompts** (`app/core/schedule_context.py::build_schedule_block`, injected as
  `{schedule_block}` in every VM follow-up tier): when a date is unset the model
  gets a SCHEDULE OVERRIDE - never write a date for it, ignore guidelines asking
  for it/urgency/RSVP, and invite the lead to start learning for free at
  `www.myfreeaiclass.com` (override with app_config `free_signup_url`). The
  Open House RSVP link is withheld whenever the open house is unset.
- **Monitor:** a blank setting means "nothing scheduled" - any dated class/open-house
  mention in a message sent AFTER the setting was cleared is flagged
  (expected shown as "none scheduled"). Messages sent before the clear are not
  (they were correct when sent). A never-configured key is not checked.
- **Correction SMS** with nothing scheduled: "...we don't have a class or Open
  House scheduled right now. You can start learning for free at
  www.myfreeaiclass.com. Sorry for any confusion!" (no RSVP link).
- **Tile metrics:** `GET /dashboard/wrong-dates` returns `stats`
  {open, closed_24h, corrected_24h, dismissed_24h, new_24h}; shown on the
  page and in the home tile description ("Open N - Closed M in the last 24h").

## Comparison rules
- Mention with no year → match on month+day. With a year → year must match too
  (when the configured value has one).
- A configured value that isn't a concrete date ("upcoming", "Q3 2026") disables
  that one check — never guess.
- Formats: `Oct 29`, `October 29th, 2026`, `29 October`, `10/29`, `10/29/2026`.

## Correction SMS
Built from the **current** dashboard values (never the stale ones), e.g.
`Quick correction from Cora from Colaberry: our next class starts November 12,
2026 and our next Open House is October 29, 2026. RSVP: <link>. Sorry for any
confusion! Text STOP to stop alerts`.
Sent by writing the GHL **Message** custom field (`GHL_FIELD_MESSAGE`) — the same
path VM follow-ups use; a GHL workflow does the actual send.
- **Shadow / GHL writes off:** nothing written, incident stays open, UI says so.
- **Live:** incident is claimed atomically (open→corrected) *before* the write, so
  a double-click/second operator can't double-send (409). GHL failure → claim
  rolled back, HTTP 502, incident stays open.
- Logged to `outbound_messages` + `audit_log`. The correction has correct dates, so
  the scanner does not re-flag it.

## Musts / Must-nots
- MUST NOT compare against hard-coded dates. MUST NOT flag appointment dates.
- MUST NOT email twice for one message. MUST NOT send a correction outside live mode.
- Caps: 25 new incidents (emails) per cycle; 2000 rows scanned per cycle.

## Known limits / escalation
- Only messages recorded in `outbound_messages` are checked. Messages sent by GHL
  workflows/templates directly (not generated by this system) are invisible to it.
- The correction only fires if the GHL "Message" workflow triggers on that field
  change. Verify once in live mode with a test contact. If the field value equals
  the previous value GHL may not fire a change trigger.
- Keyword classification is heuristic; a false positive is one click to Dismiss.
- On first deploy, messages from the prior 24h with stale dates WILL alert (capped at 25).

## Evals
- `tests/unit/test_wrong_date_guard.py` — correct dates, stale class, wrong open house,
  swapped dates, year mismatch, numeric/day-first/HTML, appointment dates (ignored),
  mixed sentences, unparseable config, invalid dates.
- `tests/unit/test_wrong_date_monitor.py` (opt-in, needs Postgres:
  `WRONG_DATE_TEST_DATABASE_URL=...`) — scan filters, idempotency, cap, config change,
  send/double-send/failure-rollback/shadow/phone-contact, dismiss, HTTP routes.

## How the correction SMS is actually delivered (verified 2026-09-30)
- GHL workflow **"AI Agent - Send SMS"**: trigger *Contact Changed -> "Support issue Ticket #4" has changed* (no value filter); action SMS whose body is just `{{contact.support_issue_ticket_4}}`.
- So the SMS text must be written into **Support issue Ticket #4** (long text, id OYgTlVij..., fieldKey `contact.support_issue_ticket_4`). Do not confuse it with the NUMERICAL field "Support Ticket #4" (`contact.support_ticket_4`), which nothing uses.
- Before this fix the correction wrote only `Message:`, so the workflow never ran (workflow stats: 0 of 15,722 attempts matched, 2026-08-31 -> 2026-10-01). Those leads are the ones shown by "Send the missing SMS" (`sms_triggered_at IS NULL`).
- Ticket #4 on many leads still holds old SMS text with stale dates (1/31, 3/28, 5/30...). It is overwritten by a correction. If anything else ever changes Ticket #4 the workflow will text its value.
- Sending byte-identical text to the same lead twice changes nothing, so GHL cannot double-send it.
- Cora's regular follow-ups write `Message:` / `Support Issue Ticket #2`, not Ticket #4; how they are delivered is NOT established here.
- `Mark as Lead` does not resolve to any GHL field id (label mismatch) - pre-existing, separate issue.

## SMS safeguards (added 2026-09-30) - apply to single send, send-all and "send the missing SMS"
Checked per lead, in this order, BEFORE anything is written to GHL (`app/core/sms_eligibility.py`, `wrong_date_monitor.screen_lead`):
1. **BLOCK** (incident dismissed as `auto-skip: <reason>`, never retried): Cora `lead_state` do_not_call / invalid / status closed / sales outcome not_interested or wrong_number; an inbound message that is just STOP / UNSUBSCRIBE / CANCEL / END / QUIT / OPT OUT; then, after reading the live GHL contact: `dnd` true (all channels), `dndSettings.SMS.status` active or permanent (GHL sets this itself on a STOP reply), or an opt-out tag (DND, unsubscribed, do not text, opted out, stop...). Email-only or call-only DND does NOT block an SMS.
2. **HOLD** (incident stays open, shown to the operator): the lead replied (Cora's rule: a person follows up, not automation); or outside the sending window = the lead's campaign hours/days (New Lead / Cold Lead, Settings) AND the TCPA floor 08:00-21:00, in the lead's own timezone. Lead with no known campaign must satisfy BOTH campaigns' windows. The run reports `held` and `next_window_opens`.
3. **Fail closed**: if the GHL contact cannot be read, the lead is not texted (counted as failed, stays open).
4. **Pacing**: `correction_send_delay_seconds` (app_config, default 5) between REAL sends only; skipped/held leads never wait.
5. **Daily cap**: `correction_daily_cap` (app_config, default 300) distinct leads per rolling 24h across corrections; the run stops and reports `daily_cap_reached`.
Results show sent / failed / blocked / held / cap. Not covered: Cora-regular follow-ups and any GHL-native workflow (their DND handling is GHL's).

## Channels (decided 2026-10-01) - supersedes the SMS-first sections above
**Evidence:** GHL conversations show Cora's follow-ups (including the ones Cora records as "SMS") are delivered as outbound EMAILS: writing Support Issue Ticket #2 starts the GHL workflow "AI Agent - Send Email" (body = Ticket #2). The SMS workflow ("AI Agent - Send SMS", body = Ticket #4) never matched in a month. Tested live 2026-10-01 on the operator's own contact: a Ticket #2 write produced an email within 1 s; a Ticket #4 write produced no SMS.
- **Email is the default and only enabled correction channel.** It writes ONLY `Support Issue Ticket #2`. One email per lead (siblings closed). Text: corrected dates + full RSVP link + free-signup address; no STOP line (GHL appends the unsubscribe footer).
- **SMS corrections are built but OFF** (`app_config sms_corrections_enabled`, default false): `send_correction`/bulk raise `ChannelDisabled` (HTTP 403). When enabled, SMS writes ONLY `Support issue Ticket #4`, text has no long third-party link (carrier filtering) and ends with the STOP line.
- **Per-channel ledgers:** `email_triggered_at` / `sms_triggered_at` + `correction_channel` on `wrong_date_incidents`; independent pacing and daily caps - email 3 s / 100 per rolling 24h (`correction_email_delay_seconds`, `correction_email_daily_cap`), SMS 5 s / 300 (`correction_send_delay_seconds`, `correction_daily_cap`).
- **Channel-specific safeguards:** GHL DND blocks only the matching channel (Email DND blocks email, SMS DND blocks text; global `dnd` blocks both); no email address / no phone on the contact -> closed with that reason; an SMS STOP reply blocks SMS only; the TCPA 08:00-21:00 floor applies to SMS only (email still follows the campaign window). Everything else (Cora flags, replied -> hold, fail closed) applies to both.
- **Test send:** `POST /dashboard/actions/send-test-correction {channel}` and the tile's "Send test email/SMS to me". Works for SMS even while SMS corrections are off (to prove the channel is restored). Only to a contact whose exact email is in `app_config correction_test_contacts` (comma-separated; empty = feature off), exactly one GHL match required, DND respected, labelled `TEST [time] - please ignore`, no incident or counter touched, audited.
- **Migration 0025** reopened the 219 incidents that had been marked "corrected" by the Message-only write (nothing was ever delivered), so the normal "Send correction email to all" delivers them. The separate "send the missing SMS" path was removed.
- Not covered: GHL-native SMS automations (e.g. "Exciting news! Colaberry's AI Systems Architect Accelerator...") - separate system, invisible to this monitor.
- Open: Cora's real email follow-ups use Ticket #2 as the body, so leads appear to get only the subject line as the email body - verify in GHL.
