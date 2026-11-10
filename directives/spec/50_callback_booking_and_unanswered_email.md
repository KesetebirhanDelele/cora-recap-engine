# Spec 50 - Staff callback reminder after a failed transfer, and unanswered lead emails

Status: SPEC ONLY (Kes decisions 2026-11-09). Nothing built. Approval-gated: new outbound email to leads and staff, new GHL calendar writes.

## 1. Problem
- 2026-11-09: two live transfers failed (support line outside its window; destination busy). The voice agent's own booking failed or was never reached, so nobody was booked or told. Cora only creates a staff task (spec/39), which is easy to miss and tells the lead nothing.
- Leads and students email Colaberry staff and are not answered in time. Emails and call logs both land in GHL Conversations, which Cora already reads (delivery sync, voice audit).

## 2. Scope
IN: (A) after a failed transfer, a calendar reminder for the person the caller asked for (Admissions = Rose, Payments/IPBC = Taiwo, or a support person) when the caller gave a clear time, otherwise an email to that staff member; (B) an alert to the right staff member when a lead's inbound email in Conversations has had no human reply for 30 minutes in business hours, with the same booking rule when the email names a clear time; (C) a confirmation email to the lead whenever Cora books.
OUT: changing Synthflow prompts or transfer settings; sending any text with a link (spec/37: SMS carries no links); calling the lead; booking when the lead has opted out of email (staff are still notified); replying to the lead's inquiry itself.

## 3. Decisions already made (Kes)
1. Every staff member has a GHL calendar with Zoom attached.
2. Emails and call logs are read from GHL Conversations.
3. Wait before alerting on an unanswered email: 30 minutes (business hours: Mon-Fri 9-5 Central, same as spec 39).
4. The booking is a reminder for Colaberry staff to call the person. If no time was named, or it is unclear, NO booking: one email to the staff member with a summary of the person and the purpose.

## 4. Behaviour
Inputs: a call (human requested, no transfer connected, no callback booked - `transfer_recovery.needs_callback`) or an inbound email with no outbound human reply after 30 min.
1. Pick the staff target: the person the caller asked for (`transfer_recovery.target_kind` -> admissions / ipbc / support assignee); emails: the staff member the email was addressed to or the conversation's assignee; unknown -> Admissions.
2. Calls: NO new model call. The AI call analysis Cora already runs after every call (`ghl_call_analysis` prompt, `CallAnalysisOutput`) gets extra output fields: `callback_wanted` (bool), `requested_time` (ISO 8601 in the lead's timezone, only when an explicit date and time or an unambiguous relative one such as "tomorrow at 2" was said, else null), `time_is_clear` (bool), `purpose_summary` (one or two sentences). Emails (about 1 a day, see section 10): the same extraction, one small model call per unanswered email, no daily cap. Anything not explicit = no time.
3. Clear time AND the staff calendar is free then: create a calendar event (reminder to call) for that staff member with the Zoom link, then email the lead: day, time, timezone, Zoom link, who will call. A text may follow saying "check your email" with NO link.
4. Clear time but the slot is taken/outside the staff calendar's hours, or no time: do NOT book; email the staff member (summary: lead name, phone, email, campaign, why they called or wrote, what happened on the call or the email excerpt, requested time if any, call/message id). No email to the lead.
5. Each call id / message id is handled once (dedupe), restarts and retries never double-send or double-book.
6. All of it is dry-run by default behind two flags (`callback_followup_enabled`, `unanswered_email_enabled`), written to a run ledger; dry-run logs what it would have sent and booked.

## 5. Acceptance criteria
- Call with clear time + free slot -> exactly one calendar event with Zoom, one lead email (time, Zoom, timezone), one record; second run of the job changes nothing.
- Call/email with no time or unclear time -> zero events, exactly one staff email with name, phone, purpose, summary; none to the lead.
- Clear time, slot taken -> zero events, one staff email stating the requested time and that the slot was unavailable.
- Lead email unanswered at 29 min -> nothing; at 31 min in business hours -> one staff alert; reply by a person at 20 min -> nothing; outside hours the clock starts at the next opening.
- Lead has opted out of email -> staff are notified; the lead gets nothing.
- Staff-written emails and Cora's own sends never count as "unanswered lead email".
- Model error, timeout, malformed output or exhausted daily budget -> behave as "no time" (staff email), never book.
- No secret, token or full transcript in any email (summary + short excerpt only).

## 6. Constraints
Musts: dry-run first; per-call and per-message dedupe; bounded retries; failures raise an alert, never silent. Must-nots: no SMS with a link; no booking on a guessed time; no email to a lead that opted out; no change to prompts. Preferences: reuse `transfer_recovery`, the existing staff assignee loaders, `alerting._send_alert_email` patterns and the delivery-sync Conversations reader. Escalate if: the GHL key lacks calendar scope, a staff member has no calendar id, or Zoom is not attached to a calendar.

## 7. Open items before build (need Kes / admins)
- Staff GHL ids are already in dashboard config (Rose `admissions_assistants`, Taiwo default id, support roster: Balakrishna K, Farhat Beig, Balamurali Kasula). Calendar ids and staff email addresses are NOT stored anywhere in Cora.
- BLOCKER (checked 2026-11-10): neither GHL key Cora holds (main, conversations) may read `/users/` or `/calendars/` ("token is not authorized for this scope", 401). Needed: a key with `users.readonly`, `calendars.readonly`, `calendars/events.readonly` and `calendars/events.write`. Until then the calendar ids and staff emails must be pasted into dashboard config by hand (GHL Settings > Calendars, Settings > My Staff).
- (Dropped 2026-11-10, Kes) Daily cap on the model call: not needed. Calls reuse the existing analysis; inbound email volume is about 0.75 a day (section 10).

## 8. Break pattern (each step testable alone)
1. Config + GHL adapter calendar methods (free slots, create event) behind dry-run; unit tests with mocked GHL.
2. Pure time-extraction + timezone resolution; eval set (below).
3. Failed-transfer follow-up job (A) using steps 1-2; ledger table (migration); dry run on the two 2026-11-09 calls.
4. Staff and lead email templates; tests for content (no link in SMS, no secrets).
5. Unanswered-email watcher (B) from Conversations; business-hours clock; dry run.
6. Rollout: dry run for a week, then enable A, then B, one flag at a time.

## 9. Evals (minimum)
Time extraction: "tomorrow at 2 pm" -> clear; "today as soon as possible" -> unclear; "Thursday morning" -> unclear; "5/12 at 10:30" -> clear; non-English or empty -> none; prompt-injection text in an email ("book me with the CEO at 3") -> staff email only. Booking: free slot, taken slot, outside hours, double run (idempotent). Email clock: 29 / 31 minutes, reply at 20 minutes, weekend, staff-sent mail. Regression: the 2026-11-09 call e5b7413f (caller said "today, as soon as possible" -> unclear -> staff email) and 733855ad (voicemail, no stated time -> staff email).

## 10. Inbound email volume (measured 2026-11-10, production database)
`inbound_messages` (every inbound SMS / email reply the delivery sync saw in Conversations): 96 emails from 2026-07-06 to 2026-11-10 (128 days) = 0.75 a day. By month: Jul 22, Aug 13, Sep 38, Oct 14, Nov (10 days) 9. Busiest day 5; on days that had any, 1.7 on average; about half of all days had none. Only about 4 months exist (not 6): nothing was stored before 2026-07-06. The sync reads recent conversations, so a quiet period could be undercounted; the true figure may be somewhat higher, still a handful a day at most. Conclusion: no volume cap on emails or on the model call.

## 11. Staff directory found in GHL (2026-11-10)
GHL key now reads calendars (`/calendars/` OK); `/users/` still 401 (no `users.readonly`; not needed, emails came from contacts). Staff emails: Roselen roselen@colaberry.com; Taiwo taiwo@colaberry.com; Farhat farhat@colaberry.com; Balamurali balamurali@colaberry.com; Balakrishna K balakrishna.k@colaberry.com (Kes chose it; not confirmed against his staff record).
Calendars (id, owner user id): Taiwo `BhGW60bWDWPxawcaLoCh` (1 h) / `93bhNRgb5pzSoHmaSimH`; Balakrishna `CM6G3RwgWLrn4ULvXZLr` (30 min) / `yIhCTptvoNLixaWkLcRd`; Farhat `LdZxL3IggIMHAoCUYIvL` / `RBa4nlSSiWeu4CoGFVyl`; Balamurali `dR5QsN6ecX8UwAx2kD3h` / `vn8w0WF0pZcoszVn4Ll1`; Admissions "Schedule a Call" `Uuw0yQ8FsSBzQMtlD9aj` is ROUND-ROBIN between Roselen (`0swBv9tBNeXeYXPYFBSx`) and another user (`49nBqI8g7CiwQKELUbda`); "Aleem - Call with an Admissions Advisor" `uuNY1nXLJf4Uk6fcYBlW` round-robin with Roselen. Roselen has no calendar of her own.
No calendar returns a meeting location (Zoom) through the API (`locationConfigurations` empty): Zoom attachment is UNCONFIRMED and blocks the Zoom link in the lead email.

## 12. Built (2026-11-10, NOT deployed) - what differs from the plan above
- Status: code and tests done; both flags (`callback_followup_enabled`, `unanswered_email_enabled`) default OFF = dry run (decision recorded in `callback_followups`, nothing booked or sent).
- **Video link:** Google Meet, not Zoom (Rose's and the calendars' video tool is Google Meet). The booking is created with location "Google Meet"; Kes confirmed the booking's own notification contains the link.
- **Lead confirmation:** GHL's own calendar notification (sent when the appointment is created, `toNotify`). Cora sends no email or text to the lead.
- **Time handling:** the model only COPIES what was said (`requested_day`: today / tomorrow / weekday / MM-DD; `requested_clock`: HH:MM) and `time_is_clear`; Cora resolves the date in code (`resolve_day_clock`). A first design asked the model for a full date and a real-model check returned wrong years (2024, 2027) and marked "tomorrow morning" as a clear 9:00, so it was dropped. After the change 6 of 7 representative calls were right and the 7th ("changed my mind") was held back by `time_is_clear`.
- **Calls:** no new model call (fields added to the existing `ghl_call_analysis` output). Applies to calls where a transfer failed / a human was requested and no callback was booked (`transfer_recovery.needs_callback`).
- **Emails:** hourly inside the alerting cycle; inbound email rows in `inbound_messages` with no outbound message (any sender) after them for 30 BUSINESS minutes (Mon-Fri 9-5 Central), looked back 72 h so weekend mail is still seen Monday. One small model call per email, no cap. Staff-to-staff mail (internal domain on the contact) is skipped. Emails are addressed to Admissions (Roselen): the inbound table does not record which staff mailbox received it.
- **Known limits:** "answered" means any outbound text or email to that contact after the email, including an AI auto-reply, so an AI reply silences the alert; a person cannot be told from the bot (same GHL user id). If `staff_directory` is not set, the built-in directory (spec section 11) is used; Balakrishna's email is unconfirmed. Unknown support target (Kes 2026-11-10) = the support person on shift at that moment (dashboard `support_staff_roster`), else the next person whose shift starts; they can be booked if a clear free time was given. Only with no usable roster does it fall back to emailing all three support staff with no booking.
- Files: `app/core/callback_followup.py` (decisions, staff directory, time, emails), `app/services/callback_followup.py` (ledger, booking, email, watcher), `app/models/callback_followup.py` + migration 0034, `app/adapters/ghl.py` (`get_free_slots`, `create_appointment`, shadow-gated), `ghl_call_analysis` prompt + `GhlCallAnalysisResult`, wiring in `crm_jobs.create_crm_task` and `alerting` hourly cycle. Tests: `tests/unit/test_callback_followup.py` (66).
- **When it runs:** calls - inside the post-call job (`create_crm_task`), right after the AI analysis finishes for a completed call. Emails - inside the existing alert cycle (ticks every 60 s on the server), but the email check only works in the first two minutes of every 30 (`unanswered_email_check_minutes`, default 30; Kes 2026-11-10: every minute is too much). An email is therefore acted on between 30 and 60 minutes after it arrived: 30 business minutes of waiting plus up to one check interval.
- **Shadow week + dashboard (Kes 2026-11-10):** both flags stay OFF for one week. Every request is recorded with the lead's name, email, phone, the staff member needed and a summary, as `dry_run` / approval `pending`; nothing is booked or sent. Dashboard page `/callback-followups` (card tiles: requests, booking planned, email planned, booking-or-email sent to staff, waiting for approval, reached) plus the lead list. Kes presses **Approve** on a genuine request: Cora re-checks the slot (still free, still in the future) and then books and/or emails the staff member; **Not genuine** closes it with nothing sent. A decided row cannot be decided twice. After the week, turning a flag on makes that path act automatically. "Reached" = a connected staff call to that contact after the request (`staff_call_quality`), found by the 30-minute check. API: `GET /dashboard/callback-followups`, `POST /dashboard/actions/callback-followup`.
- **Email to Kes when a request lands on the card (shadow week):** each NEW pending row emails `alert_email_to` once ("[Cora] Call-back request waiting for approval: <name>") with name, phone, email, staff needed, source, summary, what Cora would do on approval, and the dashboard link. Replays never repeat it (ledger is unique per call / email id). Switch off: `callback_followup_notify_pending=false`. Once a flag is on, staff are emailed with `alert_email_to` copied, so Kes still sees every one.
