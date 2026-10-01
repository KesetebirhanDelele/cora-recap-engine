# 34 — Pre-send SMS gate and daily SMS budget

**Status:** built and tested locally 2026-10-01; deploy pending Kes's go-ahead.

## Problem
Twilio sole-proprietor A2P 10DLC brands are limited to 1,000 message segments/day to T-Mobile (day resets 00:00 US Pacific; over the cap Twilio rejects with error 30023 and never retries), 1 message/second per carrier and ~15 messages/minute to AT&T. In GHL the write of `Support issue Ticket #4` is what sends a text, so Cora must check every text BEFORE that write. Kes (2026-10-01): hard cap of **999** per day; when reached, move to the next day.

## Behaviour
Every SMS Cora originates (voicemail follow-ups, date corrections, test sends) calls `sms_ledger.reserve()` right before the GHL write. `app/core/sms_gate.py` (pure) decides, in order:

| Step | Rule | Outcome |
|---|---|---|
| 1 Content | non-empty, ≥3 words (blocks stray tags like `warm_lead`), no `{{ }}`/None/markup, **no link** (unless domain re-allowed in `sms_allowed_link_domains`), ≤ `sms_max_segments_per_message` (4) segments, no wrong class/open-house date vs Settings | BLOCK: not sent, logged |
| 2 Budget | segments sent/reserved since 00:00 Pacific + this text ≤ `min(sms_daily_segment_cap, 999)` | DEFER to next Pacific day (+5 min), moved to 08:05+ in the lead's own timezone (TCPA 8–21) |
| 3 Pacing | ≥ `sms_min_gap_seconds` (5) since previous SMS; ≤ `sms_per_minute_cap` (12) in the last minute | DEFER seconds |
| 4 TCPA (follow-ups) | lead-local hour inside 08:00–21:00 | DEFER to next legal time |

Segments: GSM-7 160/153, UCS-2 70/67 (emoji ⇒ costlier). A correction SMS is 2 segments.
A check-and-insert under a Postgres advisory lock stops two workers spending the last of the budget. A failed GHL write releases its segments (`failed`); shadow mode never counts.

Follow-ups: the `update_ghl_after_vm_message` job is released back to `pending` with a new `run_at` (the same retry mechanism as pauses). Corrections: the incident stays open; a bulk run stops with `daily_cap_reached` and the operator clicks again after the reset.
On retry the content rules run again, so a text that became stale (dates changed or expired overnight) is blocked rather than sent.

## Monitoring
* Table `sms_send_ledger` (migration 0026): one row per attempt — source, status (`reserved|sent|failed|blocked|deferred`), code, reason, segments, body, Pacific day.
* Dashboard tile **SMS Monitor** (`/sms-monitor`, API `GET /dashboard/sms-monitor`): budget bar, sent/blocked/deferred/failed, segments per Pacific hour, latest 25 texts with reasons.
* Email + audit row once per Pacific day at 80% (`sms_budget_warning`) and at exhaustion (`sms_budget_exhausted`).

## Known limits (be honest)
* Only texts Cora originates are counted. GHL-native SMS automations, manual texts from the GHL inbox and Synthflow texts that bypass Ticket #4 share the same T-Mobile cap but are invisible to this ledger — keep headroom or audit them.
* The budget counts all carriers (conservative); Twilio's cap applies to T-Mobile only.
* Queued texts deferred to tomorrow all become due at about the same minute; pacing (5 s gap, 12/min) spreads them.

## app_config keys
`sms_daily_segment_cap` (999, hard ceiling 999) · `sms_min_gap_seconds` (5) · `sms_per_minute_cap` (12) · `sms_max_segments_per_message` (4) · `sms_allowed_link_domains` (empty).

## Verification SQL
```sql
-- today's budget (Pacific day)
SELECT status, count(*), sum(segments) FROM sms_send_ledger
WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles') AT TIME ZONE 'America/Los_Angeles'
GROUP BY 1;
-- blocked / deferred with reasons
SELECT created_at, source, status, code, reason, left(body,60) FROM sms_send_ledger WHERE status IN ('blocked','deferred') ORDER BY created_at DESC LIMIT 20;
```

## Tests
`tests/unit/test_sms_gate.py` (pure + sqlite ledger), DB-backed additions in `tests/unit/test_wrong_date_channels.py` (opt-in `WRONG_DATE_TEST_DATABASE_URL`).
