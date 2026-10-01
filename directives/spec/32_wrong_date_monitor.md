# Spec 32 — Wrong Date Monitor (next class start / next open house)

| Area | Status (as of 2026-10-01) |
|---|---|
| `next_open_house_date` setting (Settings page, Streamlit, AI prompts) | **LIVE** |
| Detector `app/core/wrong_date_guard.py` + scanner/alert/incidents `app/services/wrong_date_monitor.py` | **LIVE** (migration 0024) |
| Dashboard tile + page `/wrong-dates`, 24h open/closed metrics, bulk dismiss | **LIVE** |
| Date auto-expiry + free-signup fallback (`app/services/date_expiry.py`, `app/core/schedule_context.py`) | **LIVE** |
| Correction by **email** (send one / send to all), safeguards, pacing, daily cap | **LIVE** (migration 0025). 219 incidents / 137 leads reopened, **0 sent yet** — awaiting Kes's click |
| Correction by **SMS** | **BUILT, switched OFF** (`sms_corrections_enabled`). SMS channel verified working end-to-end 2026-10-01 |
| Test send to the operator's own contact (email + SMS) | **LIVE** (needs `correction_test_contacts`, set in prod DB) |

## Problem statement
Leads were told the wrong next-class-start / next-open-house date (stale dashboard value, or an AI-invented date — the prompts had no open-house date before this spec). Kes needs: (1) an email the moment it happens, (2) a dashboard tile listing affected leads with 24h metrics, (3) a safe one-click correction, (4) past dates never told again — once a date passes the setting clears and leads are invited to start free at `www.myfreeaiclass.com`.

Source of truth is **dashboard Settings** (`app_config.next_class_start`, `next_open_house_date`) — nothing is hard-coded. As of 2026-10-01: class start November 12, 2026; open house October 29, 2026.

## Detection
- Every 60 s metrics cycle (`alerting.evaluate_alerts`) → `scan_wrong_dates`: scans `outbound_messages` (sms + email, status not shadow/failed) from the last 24 h with no incident yet. One `wrong_date_incidents` row per message (UNIQUE `outbound_message_id`, idempotent under retries / concurrent workers), one alert email (`wrong_date_message`) per incident, max 25 new incidents per cycle, one aggregate `alert_events` row while any incident is open.
- Each date in subject+body is classified by the **nearest keyword in its sentence**: class (class, cohort, program starts…), open house (open house, info session, RSVP, webinar, Eventbrite), appointment (call you, appointment, scheduled, follow up…), else other. Only class / open-house dates are compared; **appointment dates are never flagged**. Tie → class/open house wins.
- No year in the message → match on month+day; with a year, the year must match. Formats: `Oct 29`, `October 29th, 2026`, `29 October`, `10/29`, `10/29/2026`.
- Configured value: real date → compare; free text ("Q3 2026") → check disabled; **blank = nothing scheduled** → any dated class/open-house mention is wrong, but only for messages sent *after* the setting was cleared (earlier ones were correct when sent).

## Date auto-expiry + free-signup fallback
- Each cycle `expire_past_dates` sets `next_class_start` / `next_open_house_date` to `""` the day AFTER the date (in `default_timezone`, America/Chicago). Conditional UPDATE (an operator's newer value is never overwritten), audited (`config_expired`), one email (`date_setting_expired`). **Year-less values ("Oct 29") are never auto-cleared — always type the year.**
- "Unset" = blank or upcoming/tbd/tba/none/n-a/null (not free text like "Q3 2026").
- Prompts get `{schedule_block}`: both set → dates + RSVP; any unset → a SCHEDULE OVERRIDE (never write a date for it, ignore guidelines asking for it, invite the lead to start free at `www.myfreeaiclass.com`; RSVP link withheld when the open house is unset). Override the address with `free_signup_url`.

## How corrections are delivered (verified against live GHL, 2026-10-01)
- Cora's follow-ups — **including the ones Cora records as "SMS"** — reach leads as outbound **emails** through GHL workflow **"AI Agent - Send Email"**: trigger *Support Issue Ticket #2 has changed*, 5 s wait, then the email. Evidence: GHL conversations for 6 leads show only TYPE_EMAIL from Cora; a recorded SMS text arrived as an email a minute later. **Mapping (corrected in GHL 2026-10-01):** Subject = `{{contact.support_issue_ticket_2}}`, Body = `{{contact.message}}`, From Name = `Colaberry Admissions Team` (or default). Before that fix the two were SWAPPED (subject = the whole message, body = only the short subject) — the engine itself always wrote them correctly (Ticket #2 = subject, Message = body; verifiable in `scheduled_jobs.payload_json` of `update_ghl_after_vm_message`).
- GHL workflow **"AI Agent - Send SMS"**: trigger *Contact Changed → Support issue Ticket #4 has changed*, 5 s wait, SMS body = `{{contact.support_issue_ticket_4}}`. Do NOT confuse with the NUMERICAL field "Support Ticket #4" (nothing uses it). It never matched for a month because the trigger watched the wrong field; fixed in GHL 2026-10-01 and re-verified (API write → SMS in 6 s).
- **Correction writes (one update):** email → Ticket #2 = short SUBJECT (originates from the engine: `app_config correction_email_subject`, default "Correction: our class and Open House dates") **and** Message = HTML BODY (`build_correction_email`); sms → Ticket #4 = text. New text differs from the lead's previous value (that is what makes "has changed" fire); identical text sent twice changes nothing, so GHL cannot double-send it. If the workflow's Subject/Body mapping is ever changed again the correction format breaks — re-run the tile's "Send test email to me" after any GHL change.
- **If someone edits a workflow trigger/field, corrections silently stop.** Use "Send test email/SMS to me" on the tile after any GHL workflow change.

## Correction flow and safeguards (per lead, nothing written until all pass)
0. SMS only: off unless `sms_corrections_enabled` (HTTP 403 / `ChannelDisabled`).
1. Shadow mode / GHL writes off → nothing sent, incident stays open.
2. **BLOCK** (incident dismissed as `auto-skip: <reason>`, never retried): Cora `lead_state` do_not_call / invalid / closed / sales outcome not_interested or wrong_number; SMS-only: inbound STOP/UNSUBSCRIBE/etc.; GHL contact: `dnd` (all channels), `dndSettings.<channel>` active/permanent (Email DND blocks email only; SMS DND blocks SMS only), opt-out tag (DND, unsubscribed, do not text/contact, opted out…), **no email address / no phone**.
3. **HOLD** (stays open): lead replied (a person follows up); outside the lead's campaign window (New Lead / Cold Lead hours+days from Settings; unknown campaign must satisfy both) in the lead's timezone; SMS additionally needs the TCPA floor 08:00–21:00 (email does not).
4. **Fail closed:** a GHL contact that cannot be read is not contacted (counted failed, stays open).
5. Atomic claim `open → corrected` before the write (no double-send on double-click), rolled back if the write fails; sibling incidents of the same lead close with it (one message per lead). Logged to `outbound_messages` + `audit_log`.
- **Pacing/caps (real sends only):** email 3 s between sends, 100 distinct leads per rolling 24 h; SMS 5 s, 300. 10 sends AND an 18 s wall-clock budget per HTTP request (`BULK_TIME_BUDGET_SECONDS`; the Next.js rewrite proxy drops requests open > ~30 s and the browser then shows "Internal Server Error" while the API keeps working) — the tile loops until `remaining` is 0, stopping only when a pass makes no progress (nothing sent, failed or skipped), on repeated failures, or at the daily cap; 3 consecutive failures stop a run.
- **Text:** built from CURRENT Settings. Email: dates + full RSVP link + free-signup address, no STOP line (GHL adds the unsubscribe footer). SMS: no long third-party links (carrier filtering), free-signup address, ends with the STOP line. Nothing scheduled → "we don't have a class or Open House scheduled right now…".

## Operator actions (tile `/wrong-dates`, all `POST /dashboard/actions/…`)
`send-date-correction {incident_id, channel=email}` · `send-date-correction-all {channel=email}` · `send-test-correction {channel}` · `dismiss-wrong-date` · `dismiss-wrong-dates-bulk` (open incidents sent before the date settings were last saved). `GET /dashboard/wrong-dates` returns incidents, `stats` (open, closed_24h, corrected_24h, dismissed_24h, new_24h), previews, `sms_enabled`, `test_available`, email cap/usage.
**Test send:** only to a contact whose exact email is in `correction_test_contacts`, exactly one GHL match, DND respected, labelled `TEST [time] - please ignore`, no incident or counter touched, audited. Works for SMS even while SMS corrections are off.

## Configuration (`app_config`, editable without a deploy)
| Key | Default | Meaning |
|---|---|---|
| `next_class_start`, `next_open_house_date`, `live_open_house_link` | — | Settings page |
| `free_signup_url` | `www.myfreeaiclass.com` | fallback invitation |
| `correction_email_subject` | `Correction: our class and Open House dates` | subject of the correction email (written to Ticket #2) |
| `sms_corrections_enabled` | `false` | allow SMS corrections |
| `correction_test_contacts` | empty (feature off) | comma-separated allowed test emails |
| `correction_email_delay_seconds` / `correction_email_daily_cap` | 3 / 100 | email pacing / cap |
| `correction_send_delay_seconds` / `correction_daily_cap` | 5 / 300 | SMS pacing / cap |

## Migrations
- **0024** `wrong_date_incidents` + seed `next_open_house_date`.
- **0025** `sms_triggered_at`, `email_triggered_at`, `correction_channel`; reopened every incident that had been marked "corrected" by the first (Message-field-only) version, because nothing was ever delivered.

## Tests / evals
`tests/unit/test_wrong_date_guard.py`, `test_schedule_context.py`, `test_sms_eligibility.py` (pure, always run) and the DB-backed `test_wrong_date_monitor.py`, `test_wrong_date_bulk.py`, `test_wrong_date_safeguards.py`, `test_wrong_date_channels.py`, `test_date_expiry.py` (opt-in: `WRONG_DATE_TEST_DATABASE_URL=… pytest …`, throwaway Postgres only). Covers: detection incl. appointment dates, blank/expired semantics, channel gating, per-channel ledgers/caps, DND/STOP/no-address blocks, replied/window holds, TCPA vs email windows, pacing only between real sends, fail-closed, claim/rollback, test-send allow-list, routes.

## Known limits / open items
- Only messages in `outbound_messages` are checked. GHL-native automations (e.g. the "Exciting news! Colaberry's AI Systems Architect Accelerator…" SMS) are invisible to this monitor.
- First correction run (86 emails, 2026-10-01) went out under the swapped workflow: subject AND body both carried the full correction text (dates correct, subject long). Not re-sent. Regular Cora follow-ups were affected by the same swap until the GHL fix.
- `Mark as Lead` does not resolve to a GHL field id (label mismatch) — pre-existing, separate.
- Classification is keyword-heuristic; a false positive costs one Dismiss click.
- Year-less configured dates are never auto-cleared.
