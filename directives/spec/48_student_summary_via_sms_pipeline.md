# 48 - Student Summary text goes through the existing SMS pipeline (Ticket #4)

**Decision (Kes, 2026-10-28):** the call-summary text uses the same path as every other Cora SMS, not a separate GHL field and workflow. This replaces the spec 45 plan ("create the Student Summary field + build a GHL workflow"); no new GHL workflow is needed. The GHL custom field `Student Summary` exists but nothing writes or reads it any more.

## Behaviour
1. After a call, the summary job runs as before (`summary_consent` must be `YES` and the summary text non-empty; UNKNOWN / NO send nothing).
2. `send_student_summary` (`app/worker/jobs/crm_jobs.py`) no longer writes a GHL field. It schedules ONE `update_ghl_after_vm_message` job with `channel=sms`, `summary_only=true`, `summary_call_event_id=<call event>`, body = the summary text.
3. That job is the existing Ticket #4 pipeline (spec 33 / 34 / 36 / 37): it reads the live GHL contact and withholds the text for do-not-disturb (all channels or SMS), opt-out tags, no phone, an unreadable record, or a Cora-side opt-out; it defers to the next legal texting hour (TCPA) or day-budget slot; it reserves the SMS ledger; then writes **Support Issue Ticket #4**, which makes GHL's "AI Agent - Send SMS" workflow text `{{contact.support_issue_ticket_4}}` (5 s wait).
4. A summary whose text carries a link other than the allow-listed `https://training.colaberry.com/start-free` is delivered by **email** instead (Ticket #2 subject + Message body -> "AI Agent - Send Email"), same rule as voicemail follow-ups (`sms_needs_email`; app_config `sms_allowed_link_domains`). About 15% of existing summaries contain a link.
5. `summary_only` limits the write to the send-trigger field(s) only (Ticket #4 for SMS; Ticket #2 + Message for the email route). It never sets `Mark as Lead = Yes` or `AI Campaign = Yes` and never touches the voicemail follow-up status.
6. Idempotent: a re-run of `send_student_summary` for the same call finds the existing hand-off job (matched on `summary_call_event_id`) and schedules nothing. A retried hand-off keeps `summary_only` (it is in the failure context).

## Musts / Must-nots
- **Must** keep the YES-consent gate; no consent, no text.
- **Must not** write anything to Ticket #4 that is not meant to be texted (it is the SMS body).
- **Must not** bulk-write or backfill summaries: the 301 older consented summaries were never delivered and stay unsent (spec 45).
- **Escalate** to Kes if a text goes to someone who did not say YES, or two texts go for one call.

## Volume and size
- Summaries: 1,055 on file, median 208 characters, 90th percentile 295, longest 466 (1-2 text segments; the pipeline caps SMS text at 640).
- Consent YES is rare (1 call in the 5 days to 2026-10-28), so only a few texts a week are expected. Why consent is rarely YES (agent not asking, or the answer not recorded) is not investigated yet.

## Tests
`tests/unit/test_crm_jobs.py`: consent YES hands off exactly one summary-only SMS job and audits; a re-run never duplicates; no contact id fails and sends nothing; a summary hand-off writes only the Ticket #4 field (no Mark as Lead / AI Campaign / Message); consent NO / UNKNOWN / missing / empty still send nothing. Routing, DND, opt-out and link rules are covered by the existing follow-up routing tests.

## Verify after deploy
Call the inbound or an outbound number from a test contact whose SMS is allowed, say yes to the text summary, and expect the text within a few minutes of the call (inside texting hours). In the dashboard the hand-off appears as an `update_ghl_after_vm_message` job with `summary_only`.
