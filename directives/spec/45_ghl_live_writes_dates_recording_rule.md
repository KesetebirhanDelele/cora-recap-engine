# 45 - GHL writers use the live flags; call-date parser fix; never transfer on a recording; callback-only goes live; DND backlog drain

**Status:** built and tested 2026-10-26 on `fix/ghl-live-writes-dates-transfer-rule`. Kes approved all seven items ("go-ahead on these"), including item 5 (callback-only, spec/44 section 5).

## 1. Four GHL writers ran in shadow mode (and sent the wrong thing)
`GHLClient.update_contact_fields` without `mode_flags` falls back to the env `ghl_writes_enabled` (False in production), so the write is a no-op. Same class as spec/43.
New `app/core/ghl_write.write_label_fields(ghl, contact_id, {label: value}, settings, flags, reason)`: verified contact match (phone-keyed ids checked against the contact's
phone), label -> field id, live flags, returns True ONLY for a real write.
| Writer | Before | Now |
|---|---|---|
| `voicemail_jobs._finalize_campaign` (end of the voicemail tiers) | shadow; 38 of 40 recent finalized leads (2,647 in all) still AI Campaign = Yes | `AI Campaign = No` through the helper. **"Mark as Lead = Yes" is no longer written here**: the post-call analysis sets it per lead and forcing Yes on every finalized lead would overwrite that and could start GHL workflows that were never live. |
| `intent_actions._handle_enrolled` | shadow (0 enrolled leads so far) | `_write_ghl_campaign_off` (spec/43 helper) |
| `dashboard._finalize_lead` (force finalize) | shadow, and sent the field LABEL | `_write_ghl_campaign_off` |
| `crm_jobs.send_student_summary` | shadow, sent the label, logged "delivered" and wrote the audit anyway; 301 consented summaries never reached GHL (25 of 25 sampled empty) | helper; if the write is not real it raises, the job fails, an exception is recorded and **no delivery audit is written** |

**Student Summary field:** Kes created it in GHL on 2026-10-26 (name `Student Summary`, key `contact.student_summary`, multi-line / LARGE_TEXT, object Contact, folder Support).
That matches the code (label "Student Summary", summaries average 235 characters, longest 466). Filling the field texts nobody by itself: **a GHL workflow is still needed**
("Contact changed: Student Summary" -> Send SMS `{{contact.student_summary}}`; GHL's SMS action respects Do-Not-Disturb). None exists today. The 301 old summaries are NOT
backfilled (stale).

**Backfill of finalized leads:** `execution/backfill_ai_campaign_off.py --finalized [--limit N]` (dry run by default, read-back on apply): leads with `ai_campaign_value = 3`.

## 2. Impossible call dates
`call_processing._parse_datetime` tried `datetime.fromisoformat` BEFORE the numeric epoch, and Python reads the string "1793061552134" as a compact date (year 1793). Synthflow sometimes
sends the epoch-ms as a string. 238 rows (21 in the last 24 h) sat in the 18th century and vanished from every weekly report. Now: a long all-digit string is an epoch first
(ms if > 1e11); a result outside 2000-2100 is None (the row falls back to created_at) instead of a wrong date; out-of-range numbers return None instead of raising.
`execution/repair_call_dates.py` (dry run by default) repairs existing rows from the payload start time, else call_started_at, else created_at (approximate).

## 3. Never transfer, book or keep talking on a recording (all three prompts, Section 0 item 7)
A recording, automated menu or answering machine ("your call is very important to us", "press 1", "stay on the line", "leave a message after the tone") is not a person: no transfer,
no booking, end the call. Found on the 10-26 20:05 "successful" transfer to Balamurali: a Cold Lead call, 45 s, answered by a call-centre recording; the agent transferred anyway.

## 4. Callback-only for Admissions and Support goes live (spec/44 section 5)
Merged from `hold/callback-only-teams`. The inbound Synthflow agent's prompt must be re-pasted by hand (the cold / warm prompts are injected per call).

## 5. Do-not-disturb backlog
GHL DND is applied for 806 of 1,048 do-not-call leads, 3 await operator review, and **238 had never been checked** (135 older than 60 days) because `optout.reconcile_step` handles
~4 leads an hour. `execution/drain_dnd_reconcile.py` loops it (20 leads / 60 s per step, 25 minute cap). It only READS GHL and writes `optout_actions` rows: 'ok' where DND is already set,
'review' where it is missing. It never turns DND on: that stays an operator decision on the Opt-outs page (the do-not-call intent can be a false positive).

## Acceptance
- A shadow, unmatched or missing-field write is False and writes nothing; live flags + matched contact write the FIELD ID; the summary job fails loudly instead of recording a delivery.
- Epoch strings and ints of every common shape parse to 2026; ISO strings are unchanged; impossible years are None.
- Finalized-lead backfill selects only `ai_campaign_value = 3` and is idempotent (read-back).
- Prompts carry the recording rule; the drain stops when nothing is left or at the time limit.

## Tests
`tests/unit/test_ghl_live_writes_and_dates.py` (27), `test_crm_jobs.py` (+1, 1 updated), `test_enrolled_intent.py` (1 updated). Suite: same unrelated failures as before (7).
