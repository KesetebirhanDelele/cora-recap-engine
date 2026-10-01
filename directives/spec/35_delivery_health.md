# 35 — Delivery health: sent vs delivered, last activity per channel, silence alert

**Status:** built and tested locally 2026-10-01; deploy pending Kes's go-ahead.

## Problem (Ali, 2026-10-01)
The SMS workflow matched nothing for a month while "attempted" looked healthy and email kept flowing. Controls must be: per channel (not one number), on **delivered** (not attempted), and a **scheduled check for silence** (a dead channel throws no error). Alert Kes, copy Ali, after 2.5 days with no delivery.

## Sources of truth
| Channel | "Sent" (hand-off) | "Delivered" |
|---|---|---|
| SMS | Cora wrote `Support issue Ticket #4` (recorded in `channel_events` kind `handoff`) | GHL message status `delivered` (Twilio status); `undelivered`/`failed` = failed, with the Twilio error (e.g. 30003 unreachable) |
| Email | Cora wrote `Support Issue Ticket #2` + Message | GHL email status behind `meta.email.messageIds[0]` (`GET /conversations/messages/email/{id}`, header 2021-04-15): delivered / opened / clicked = delivered; bounced / failed = failed |
| Calls | completed `launch_outbound_call` job | matching `call_events` row (Synthflow logs reach Cora 10–15 min later): voicemail, human_goodbye, human_pick_up_cut_off, agent_goodbye, or "undefined" with ≥5 s = connected; anything else = failed |

A hand-off matches a delivery for the same contact + channel within [-2 min, +60 min] (calls: +90 min). **Not confirmed** = hand-off older than 15 min (calls 25 min) with no delivery/failure record — the dead-workflow signature. Hand-offs are recorded for follow-ups and corrections (not counted before 2026-10-01 deploy; GHL-side deliveries are backfilled 72 h).

## Replies (inbound)
`delivery_sync` also reads inbound SMS / email / call messages from GHL conversations: logged in `channel_events` kind `reply`, and SMS/email replies are stored in `inbound_messages` (previously never written — so a STOP reply was invisible to Cora's own `_has_stop_reply` check). `lead_state.last_replied_at` is intentionally NOT changed (spec/06: reply handling is owned by GHL automations); only logged. Inbound calls are logged as reply events (they arrive via GHL, not Synthflow).

## Rules (`app/core/channel_health.py`, same for tile colour and alert)
* RED: no delivery for ≥ `channel_silence_hours` (60 = 2.5 days), or ≥20 sent in 24 h with <50 % delivered, or ≥20 sent, >10 % unconfirmed and nothing delivered/failed.
* AMBER: silent ≥ 36 h, delivered rate < 90 %, or >10 % unconfirmed.
* GREY: muted on purpose (`channel_silence_muted`, system paused, calls with outbound campaigns paused) — shown, never alerts.
* A brand-new tracker is not judged until its data covers the silence window (backfill 72 h satisfies this).

## Scheduled check
`alerting.evaluate_alerts` (60 s metrics cycle) calls `delivery_sync.sync` (throttled 5 min, time-boxed 20 s, resumable backfill) and `channel_health.run_silence_check` (hourly). A RED channel raises one `alert_events` row (`channel_silence_<channel>`) and ONE email to `alert_email_to` cc `channel_silence_cc` (default ali@colaberry.com); a recovery email when it clears. Read-only against GHL.

## Dashboard
Tile **Delivery Health** (`/delivery-health`, API `GET /dashboard/delivery-health[/{channel}]`): per-channel sent / delivered (%) / not confirmed / failed / last delivered / 7-day trend, Replies row, drill-down of failed + unconfirmed (contact ids, reasons). Home tile shows the delivered % per channel and a RED count.

## app_config keys
`channel_silence_hours` (60) · `channel_silence_amber_hours` (36) · `channel_silence_cc` · `channel_silence_muted` (comma list: email,sms,call) · `delivery_confirm_minutes` (15) · `delivery_sync_interval_seconds` (300) · `delivery_sync_backfill_hours` (72).

## Limits
* Email "delivered" = provider accepted/delivered, not read. Call "delivered" = connected, not conversation quality.
* Native GHL automations' emails/SMS count toward "last delivered" (the channel works) but not "sent" (no Cora hand-off).
* Needs `ghl_conversations_api_key` (already configured).

## Verification
```sql
SELECT kind, channel, outcome, count(*), max(event_at) FROM channel_events GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT value FROM channel_sync_state WHERE key='delivery_sync';
SELECT alert_type, status, message FROM alert_events WHERE alert_type LIKE 'channel_silence_%' ORDER BY created_at DESC LIMIT 5;
```
Tests: `tests/unit/test_delivery_health.py` (pure + opt-in Postgres: sync, matching, calls, replies, silence alert + cc).
