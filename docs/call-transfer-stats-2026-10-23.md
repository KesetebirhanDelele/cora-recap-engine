# Call transfer and booking baseline - 2026-10-23

Saved for Kes's report to Ali after the call-transfer test to Taiwo on Monday 2026-10-26. Source: `call_events.raw_payload_json.executed_actions`
(Synthflow), read from production on 2026-10-23. Times are UTC. Engineering context: `directives/spec/39_failed_transfer_recovery.md`.

## Why this exists
Cold Lead call `735331a5-49ee-4fd8-9f19-012d3a954ffe` (Fri 2026-10-23, 12:46 PM Central): the caller asked for a human five times. The transfer
action `admissions_transfer_roselen` returned `transfer-failed-connection-error` / `transfer_blocked_by_carrier`. The voice agent did not book a
callback and ended the call. The engine read the failed transfer as a successful handoff and created no staff task.

## Last successful transfer, by destination

| Destination (Synthflow action) | Last successful transfer | Since then |
|---|---|---|
| Roselen - `admissions_transfer_roselen` (current) | **2026-08-13 20:18 UTC** (8 successes 08-07 to 08-13) | 19 failures to 09-17 (10 no answer, 9 outside availability); carrier-blocked on **10-15** and **10-23** |
| Roselen - `admission_roselen` (older action) | never | 36 failures, 07-09 to 08-07 |
| Roselen - direct number action `16825975784` | 2026-10-15 14:48 UTC (6 successes) | 6 "403" and 1 no-answer failures in July-August |
| Taiwo - `taiwo_ipbc_payment` | **2026-09-28 16:42 UTC** (15 successes) | 26 failures, all 07-09 to 08-29 (21 no answer, 5 outside availability); none since |
| Support - `customer_support_farhat` | **never** | 13 outside availability (last 10-22 17:16 UTC), 2 no answer |
| Support - `customer_support_bala` | 2026-08-19 13:47 UTC (1 success) | 1 outside availability on 10-15 |

Reading it: Roselen's current transfer stopped connecting after 08-13 and is now carrier-blocked (a Synthflow / telephony problem outside Cora's code).
Taiwo's route works. Farhat's failures are all "outside availability" (a shift / availability setting), not a carrier block.

## Bookings (`action_ghl_create_booking`)
- Last successful booking: **2026-10-24 00:41 UTC** (Cold Lead call).
- All time: 176 successes, 8 errors (latest error 2026-10-01).
- Last 30 days: 17 successes - Cold Lead 13, New Lead 4.
- Per week (successes): 09-07: 4, 09-14: 6, 09-21: 5, 09-28: 8, 10-05: 1, 10-12: 2, 10-19: 5.

## What changed after this baseline (deployed 2026-10-23)
- Voice prompts: hard rule "if the transfer fails, book a callback inside the call" (cold / warm injected per call; inbound pasted by Kes).
- Engine: a failed transfer is recognised as a failure, no AI redial is scheduled, a staff "CALL BACK NOW" task is created when no callback was booked.
- New alert `call_transfer_failed` (one email per new failed call).

## Re-run on Monday after the Taiwo test
Run on the Hetzner server (`docker compose exec postgres psql ...`), read-only. Outcome of every transfer attempt in the last 3 days:

```sql
select c.start_time_utc, k as action,
       (v->>'return_value')::jsonb->>'status' as status,
       coalesce(nullif((v->>'return_value')::jsonb->>'failed_reason',''),'-') as reason
from call_events c,
     jsonb_each(case when jsonb_typeof(c.raw_payload_json::jsonb->'executed_actions')='object'
                     then c.raw_payload_json::jsonb->'executed_actions' else '{}'::jsonb end) e(k,v)
where v->>'action_type' ilike '%transfer%' and c.start_time_utc > now() - interval '3 days'
order by 1 desc;
```

A connected transfer shows `status = transfer-success` and reason `-`. A failure shows `transfer-failed-connection-error` with a reason.
Failures also arrive as a `call_transfer_failed` alert email, and a "CALL BACK NOW" GHL task appears for any caller left unconnected without a booked callback.
