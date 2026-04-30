# spec/dashboard/07_api_contracts.md

All endpoints return `Content-Type: application/json`. All timestamps are ISO 8601 UTC strings. Pagination uses `limit` / `offset` query params unless noted.

---

## GET /dashboard/health

Returns current system health snapshot.

**Auth**: Optional (controlled by `DASHBOARD_READ_AUTH_REQUIRED`)

**Response 200**
```json
{
  "queue_lag_seconds": 47,
  "active_workers": 2,
  "open_exception_count": 3,
  "stuck_job_count": 1,
  "expired_lease_count": 0,
  "jobs_completed_last_5m": 12,
  "jobs_failed_last_5m": 1,
  "error_rate": 0.083,
  "shadow_mode_enabled": true,
  "ghl_write_mode": "shadow",
  "app_env": "development",
  "recorded_at": "2026-04-05T14:00:00Z"
}
```

**Fields**:
- `queue_lag_seconds`: seconds since oldest past-due pending job's `run_at`. `0` if none.
- `active_workers`: distinct `claimed_by` count for running jobs with valid leases.
- `error_rate`: `jobs_failed_last_5m / jobs_completed_last_5m`. `null` if denominator is 0.
- `stuck_job_count`: `scheduled_jobs` rows `status='pending'` and `run_at < now() - 10 min`.
- `expired_lease_count`: `scheduled_jobs` rows `status='running'` and `lease_expires_at < now()`.

---

## GET /dashboard/metrics

Returns aggregated KPIs and queue metrics for a time window.

**Auth**: Optional

**Query params**:
- `campaign` (optional): `New Lead` | `Cold Lead` | `Unknown` — `Unknown` matches leads where neither `campaign_name` nor `lead_stage` resolves to a standard campaign
- `direction` (optional): `Inbound` | `Outbound` — Outbound is defined as NOT Inbound (`direction IS NULL OR LOWER(direction) != 'inbound'`); case-insensitive
- `voice_agent` (optional): `ColdLead` | `NewLead` | `Inbound` — exact match on `call_events.voice_agent` (Synthflow agent identifier)
- `from_date` (optional): ISO date, default `now() - 7 days`
- `to_date` (optional): ISO date, default `now()`

**Note on consent distribution**: the `ai.consent_distribution` values are computed from `summary_results` joined to `call_events`, meaning all active filters (campaign, direction, voice_agent, date range) apply to the consent counts as well.

**Response 200**
```json
{
  "period": {"from": "2026-03-29T00:00:00Z", "to": "2026-04-05T00:00:00Z"},
  "campaign_filter": "New Lead",
  "kpis": {
    "total_calls": 150,
    "pickup_rate": 0.32,
    "voicemail_rate": 0.58,
    "failed_rate": 0.10,
    "callback_completion_rate": 0.21,
    "campaign_exit_rate": 0.45,
    "do_not_call_rate": 0.03,
    "enrolled_count": 12
  },
  "ai": {
    "blank_transcript_rate": 0.18,
    "intent_distribution": {
      "re_engaged": 14,
      "callback_request": 22,
      "not_interested": 8,
      "enrolled": 12,
      "partial_engagement": 19
    },
    "consent_distribution": {
      "YES": 41,
      "NO": 67,
      "UNKNOWN": 42
    }
  },
  "queue": {
    "stuck_jobs": [
      {
        "job_id": "uuid",
        "job_type": "send_sms",
        "contact_id": "abc123",
        "run_at": "2026-04-05T13:45:00Z",
        "lag_seconds": 900
      }
    ],
    "expired_leases": []
  },
  "crm": {
    "ghl_task_success_rate": 0.94,
    "ghl_vm_update_success_rate": 0.98,
    "ghl_shadow_write_count": 204
  }
}
```

---

## GET /dashboard/events

Cursor-based event stream for polling fallback.

**Auth**: Optional

**Query params**:
- `since` (optional): ISO timestamp. Returns events with `created_at > since`. Default: last 100 events.
- `limit` (optional): integer, max 200, default 100.

**Response 200**
```json
{
  "events": [
    {
      "id": "uuid",
      "event_type": "job_completed",
      "entity_type": "call",
      "entity_id": "call-id-abc",
      "contact_id": "contact-123",
      "message": "process_call_event completed for contact-123",
      "payload": {"job_type": "process_call_event", "duration_ms": 342},
      "created_at": "2026-04-05T14:01:00Z"
    }
  ],
  "next_cursor": "2026-04-05T14:01:00Z"
}
```

---

## GET /dashboard/lead/{contact_id}/trace

Full pipeline trace for a single lead.

**Auth**: Optional

**Path params**:
- `contact_id`: GHL contact ID

**Query params**:
- `phone` (optional): E.164 phone number; used as fallback lookup when `contact_id` not found

**Response 200**
```json
{
  "contact_id": "abc123",
  "normalized_phone": "+16026975078",
  "campaign_name": "New Lead",
  "status": "active",
  "ai_campaign_value": "1",
  "steps": [
    {
      "job_type": "process_call_event",
      "started_at": "2026-04-05T14:00:00Z",
      "completed_at": "2026-04-05T14:00:01Z",
      "duration_ms": 820,
      "status": "completed",
      "is_shadow": false,
      "shadow_payload": null,
      "exception_id": null,
      "failure_reason": null,
      "payload_summary": {
        "call_id": "29332285-759d-4825-94d8-270498070a28",
        "campaign_name": "New Lead",
        "call_status": "no-answer",
        "duration_seconds": 0
      }
    },
    {
      "job_type": "send_sms",
      "started_at": "2026-04-05T16:02:00Z",
      "completed_at": "2026-04-05T16:02:03Z",
      "duration_ms": 2100,
      "status": "completed",
      "is_shadow": true,
      "shadow_payload": {
        "message_body": "Hi Steven, this is Cora from Colaberry..."
      },
      "exception_id": null,
      "failure_reason": null,
      "payload_summary": {
        "campaign_name": "New Lead",
        "attempt_number": 1
      }
    },
    {
      "job_type": "create_crm_task",
      "started_at": "2026-04-05T14:00:02Z",
      "completed_at": null,
      "duration_ms": null,
      "status": "failed",
      "is_shadow": false,
      "shadow_payload": null,
      "exception_id": "exc-uuid-123",
      "failure_reason": "GHL API 401 Unauthorized",
      "payload_summary": {}
    }
  ]
}
```

**Response 404**
```json
{"detail": "Lead not found for contact_id=abc123"}
```

---

## GET /dashboard/alerts

List active and recent alerts.

**Auth**: Optional

**Query params**:
- `status` (optional): `active` | `resolved` | `acknowledged`. Default: `active`.
- `limit`: default 50.

**Response 200**
```json
{
  "alerts": [
    {
      "id": "uuid",
      "alert_type": "queue_lag_exceeded",
      "severity": "critical",
      "status": "active",
      "current_value": 450.0,
      "threshold": 300.0,
      "message": "Queue lag is 450s, threshold is 300s",
      "email_sent_at": "2026-04-05T14:05:00Z",
      "last_seen_at": "2026-04-05T14:06:00Z",
      "resolved_at": null,
      "created_at": "2026-04-05T14:05:00Z"
    }
  ]
}
```

---

## POST /dashboard/actions/retry

Retry a failed job associated with an exception.

**Auth**: Required

**Request body**
```json
{
  "exception_id": "uuid",
  "delay_minutes": 0
}
```
- `delay_minutes = 0`: enqueue immediately (retry now).
- `delay_minutes > 0`: schedule at `now() + delay_minutes`.

**Response 200**
```json
{
  "status": "ok",
  "scheduled_job_id": "new-job-uuid",
  "run_at": "2026-04-05T14:10:00Z",
  "audit_log_id": "audit-uuid"
}
```

**Response 404**: exception not found.
**Response 409**: action already pending for this exception.
**Response 422**: missing required fields.

---

## POST /dashboard/actions/cancel

Cancel all pending jobs for a lead.

**Auth**: Required

**Request body**
```json
{
  "contact_id": "abc123",
  "reason": "operator: incorrect campaign assignment"
}
```

**Response 200**
```json
{
  "status": "ok",
  "cancelled_job_count": 3,
  "audit_log_id": "audit-uuid"
}
```

---

## POST /dashboard/actions/finalize

Force finalize a lead — closes campaign, cancels pending jobs, writes audit log.

**Auth**: Required

**Request body**
```json
{
  "contact_id": "abc123",
  "reason": "operator: lead enrolled via manual process"
}
```

**Response 200**
```json
{
  "status": "ok",
  "audit_log_id": "audit-uuid"
}
```

**Note**: No GHL write is triggered directly. The finalization writes `lead_state.status = 'closed'` and cancels pending jobs. GHL `AI Campaign = No` write may be enqueued as a separate job if the lead was active.

---

## POST /dashboard/actions/resolve

Resolve an exception without retrying.

**Auth**: Required

**Request body**
```json
{
  "exception_id": "uuid",
  "note": "Confirmed stale — contact enrolled offline"
}
```

**Response 200**
```json
{
  "status": "ok",
  "audit_log_id": "audit-uuid"
}
```

---

## POST /dashboard/actions/ignore

Suppress an exception from the open list.

**Auth**: Required

**Request body**
```json
{
  "exception_id": "uuid"
}
```

**Response 200**
```json
{
  "status": "ok",
  "audit_log_id": "audit-uuid"
}
```

---

## GET /dashboard/ai-timeseries

Returns weekly AI behavior time series for trend charts on the AI Performance page.

**Auth**: Optional

**Query params**:
- `from_date` (optional): ISO date, default `now() - 28 days`
- `to_date` (optional): ISO date, default `now()`

**Response 200**
```json
{
  "period": {"from": "2026-03-09T00:00:00Z", "to": "2026-04-06T00:00:00Z"},
  "time_series": [
    {
      "date": "2026-03-09",
      "total_calls": 42,
      "blank_transcript_rate": 7.1,
      "unknown_intent_rate": 11.9,
      "intent_distribution": {
        "enrolled": 5,
        "not_interested": 8,
        "callback_request": 12,
        "low_confidence_audio": 5
      }
    }
  ]
}
```

**Fields**:
- `blank_transcript_rate`: percentage (0–100) of calls with null/empty transcript in that week.
- `unknown_intent_rate`: percentage of calls classified as `low_confidence_audio` or with no detected intent.
- `intent_distribution`: per-week count of each detected intent for stacked/area trend charts.

---

## GET /dashboard/exceptions

List exception records with grouping support.

**Auth**: Optional

**Query params**:
- `status` (optional): `open` | `resolved` | `ignored`. Default: `open`.
- `severity` (optional): `critical` | `warning`.
- `type` (optional): exact exception type match.
- `limit` (optional): integer, max 500. Default: 200.
- `offset` (optional): integer. Default: 0.

**Response 200**
```json
{
  "exceptions": [
    {
      "id": "uuid",
      "call_event_id": "call-uuid",
      "entity_type": "call",
      "entity_id": "call-uuid",
      "type": "unknown_call_status",
      "severity": "warning",
      "status": "open",
      "resolution_reason": null,
      "resolved_by": null,
      "context_json": {"call_status": "unknown_value"},
      "version": 1,
      "created_at": "2026-04-05T14:00:00Z",
      "updated_at": "2026-04-05T14:00:00Z"
    }
  ],
  "total": 134,
  "status_filter": "open",
  "groups": [
    {"type": "unknown_call_status", "severity": "warning", "count": 134},
    {"type": "crm_task_failed",     "severity": "critical", "count": 3}
  ]
}
```

**`groups` field**: per-type aggregates over all records matching `status` (and `severity` if provided), regardless of `type` filter or pagination. Used by the UI to render grouped views without a second round-trip.

---

## POST /dashboard/actions/bulk-ignore

Bulk-ignore all open exceptions of a given type. Used to clear noise from the exception queue.

**Auth**: Required

**Request body**
```json
{
  "type": "unknown_call_status",
  "note": "Cleared stale entries from queue"
}
```

**Response 200**
```json
{
  "status": "ok",
  "ignored_count": 134,
  "audit_log_id": "audit-uuid"
}
```

**Behavior**: `UPDATE exceptions SET status='ignored' WHERE type=:type AND status='open'`. Single audit log entry referencing the type.

---

## POST /dashboard/actions/acknowledge-alert

Mark an active alert as acknowledged. Acknowledged alerts appear in the **Acknowledged** tab on the Alerts page and are removed from the **Active** tab.

**Auth**: Required

**Request body**
```json
{
  "alert_id": "uuid",
  "note": "Investigating — known incident in progress"
}
```
- `alert_id`: required; the `id` of the `alert_events` row to acknowledge.
- `note`: optional free-text note written to the audit log. Max not enforced server-side; keep under 500 characters.

**Response 200**
```json
{
  "status": "ok",
  "alert_id": "uuid",
  "audit_log_id": "audit-uuid"
}
```

**Response 409**: alert not found or not in `active` status (already resolved or acknowledged).
**Response 422**: missing `alert_id`.
**Response 403**: missing or invalid Bearer token.

**Behavior**: `UPDATE alert_events SET status='acknowledged', resolved_at=now() WHERE id=:alert_id AND status='active'`. If `rowcount == 0` the API returns 409. One `audit_log` row is written with `action='acknowledge_alert'`, `entity_type='alert'`, `entity_id=alert_id`.

---

## GET /dashboard/card-metrics

Returns compact current + previous metric pairs for every navigation card indicator on the home page.

**Auth**: Optional

**Response 200**
```json
{
  "events_per_min":             {"value": 17,    "previous_value": 14},
  "open_exceptions":            {"value": 3,     "previous_value": 5},
  "backlog_size":               {"value": 8,     "previous_value": 11},
  "active_alerts":              {"value": 1,     "previous_value": 0},
  "lookup_rate":                {"value": 42,    "previous_value": 38},
  "config_health":              {"value": "healthy", "previous_value": "healthy"},
  "pickup_rate":                {"value": 0.31,  "previous_value": 0.28},
  "meaningful_engagement_rate": {"value": 0.54,  "previous_value": 0.49},
  "urgent_leads_count":         {"value": 7,     "previous_value": 4},
  "active_leads":               {"value": 1240,  "previous_value": 1180},
  "sync_success_rate":          {"value": 0.97,  "previous_value": 0.95},
  "anomaly_count":              {"value": 2,     "previous_value": 0},
  "webhook_delivery_pct":       {"value": 0.94,  "previous_value": 1.0},
  "computed_at": "2026-04-10T14:00:00Z"
}
```

**Fields**:
- `urgent_leads_count`: calls in the last 7 days with a high-intent `detected_intent` (enrolled, callback_request, callback_with_time, re_engaged, human_transfer_request), duration ≥ 30s, transcript and recording present. Color semantics: 0 = green, high = red.
- `active_leads`: count of `lead_state` rows where `status NOT IN ('closed','terminal')` and `do_not_call IS NOT TRUE`. Used as the Lead Lifecycle nav card primary metric (replaces `in_vm_sequence` as of 2026-04-28).
- `in_vm_sequence`: leads in active voicemail tier (tier 0–2 only; tier 3 excluded as it is terminal/finalized).
- `finalized_today`: leads where `status IN ('closed','terminal') OR do_not_call IS TRUE OR ai_campaign_value = '3'` and `updated_at >= midnight CST`. Includes tier-3 leads as finalized (updated 2026-04-28).
- `webhook_delivery_pct`: fraction of `launch_outbound_call` jobs completed in the last 24 hours (excluding the most recent 20 minutes) that have a matching `call_event`. Value is 0–1. `null` when no qualifying jobs exist. Displayed on the Queue Health nav card alongside backlog size. Color semantics: ≥ 0.7 = green, ≥ 0.4 = yellow, < 0.4 = red. Added 2026-04-29 following the April 28 Synthflow HTTP step burst incident.
- All numeric rate fields are 0–1 (not 0–100).
- `config_health` is a string enum: `"healthy"` | `"warning"` | `"error"`.
- `previous_value` is the corresponding metric from the prior 24-hour window (used for trend arrow direction).

**Nav card mapping** (as of 2026-04-29):

| Page | Primary metric | Secondary metric | Display format |
|---|---|---|---|
| `/queue` | `backlog_size` | `webhook_delivery_pct` | `{n} backlog · {pct}% webhooks` |
| `/lead-lifecycle` | `active_leads` | `finalized_today` | `{n} active · {n} finalized` |
| `/campaign-overview` | `active_leads` | — | `{n} leads` |

---

## GET /dashboard/recent-calls

Returns calls with duration ≥ 30s that have a transcript and recording URL. Each row is enriched with sales queue metadata. Used by the Sales Queue page (`/conversion-funnel`).

**Auth**: Optional

**Query params**:
- `from_date` (optional): ISO date, default `now() - 7 days`
- `to_date` (optional): ISO date, default `now()`
- `voice_agent` (optional): `ColdLead` | `NewLead` | `Inbound` — exact match on `call_events.voice_agent`
- `limit` (optional): integer, max 500, default 200

**Response 200**
```json
{
  "period": {"from": "2026-04-03T00:00:00Z", "to": "2026-04-10T00:00:00Z"},
  "voice_agent_filter": null,
  "total": 42,
  "calls": [
    {
      "contact_id": "abc123",
      "lead_name": "Sarah Johnson",
      "phone": "+16025550101",
      "campaign_name": "New Lead",
      "voice_agent": "NewLead",
      "status": "completed",
      "duration_seconds": 187,
      "recording_url": "https://...",
      "transcript": "Full transcript text...",
      "transcript_preview": "Hi Sarah, this is Cora from...",
      "call_time": "2026-04-10T13:45:00Z",
      "detected_intent": "callback_request",
      "sales_priority": "urgent",
      "sales_score": 85,
      "attempts": 3,
      "last_call_minutes_ago": 22,
      "recommended_action": "Call Now"
    }
  ]
}
```

**`lead_name` resolution** (three-tier, in priority order):
1. `raw_payload_json->>'Name'` unless the value matches a phone-number pattern (`^\+?[\d\s\-\(\)\.]{7,}$`) or is blank.
2. GHL contact `firstName` from `raw_payload_json->'executed_actions'->'get_the_user_preferences_from_gohighlevel'->>'return_value'::jsonb->'results'->'results.data'->'contact'->>'firstName'` (Inbound call path).
3. `"Unknown"` as final fallback.

**Sales priority scoring**:
- Base score: per-intent table (0–100). `enrolled`/`human_transfer_request` = 100, `callback_request`/`callback_with_time`/`re_engaged` = 90, `interested_not_now` = 70, `failed_booking` = 65, `partial_engagement` = 40, all others ≤ 30.
- Recency bonus: +10 if `last_call_minutes_ago < 30`; +5 if `< 120`.
- Thresholds: `urgent` ≥ 80, `review` ≥ 40, `none` < 40.

---

## GET /dashboard/lead-lifecycle

Returns per-lead campaign journey data for the Lead Lifecycle Monitor page.

**Auth**: Optional

**Query params**:
- `status` (optional): `all` | `active` | `finalized` | `vm` | `dnc`. Default: `all`.
- `campaign` (optional): `all` | `Cold Lead` | `New Lead` | `Inbound`. Default: `all`.
- `limit` (optional): integer, default 100.
- `offset` (optional): integer, default 0.

**Response 200**
```json
{
  "summary": {
    "active": 1447,
    "in_vm_sequence": 1305,
    "campaign_switched": 1,
    "finalized": 18,
    "avg_days_to_close": 5.8
  },
  "total": 1465,
  "rows": [
    {
      "contact_id": "+16025550101",
      "lead_name": "Sarah Johnson",
      "phone": "+16025550101",
      "current_campaign": "Cold Lead",
      "initial_campaign": "New Lead",
      "vm_tier": "2",
      "status": null,
      "do_not_call": false,
      "first_contact_at": "2026-04-21T14:00:00Z",
      "last_contact_at": "2026-04-27T14:06:00Z",
      "days_active": 6,
      "total_calls": 4,
      "total_sms": 3,
      "total_email": 1,
      "last_intent": "partial_engagement",
      "campaign_switches": 1,
      "next_job_type": "launch_outbound_call",
      "next_run_at": "2026-04-28T14:02:00Z",
      "finalization_reason": null,
      "finalized_at": null
    }
  ],
  "filters": {"status": "all", "campaign": "all"}
}
```

**Finalized classification rules** (applied identically in summary counts, row filter, and `statusBadge` on the frontend):
- `do_not_call IS TRUE` → DNC
- `status IN ('closed', 'terminal')` → Finalized
- `ai_campaign_value = '3'` → Finalized (terminal voicemail tier — GHL finalization writes have been made)
- `ai_campaign_value IN ('0','1','2')` → VM Sequence
- All others → Active

**Important**: `ai_campaign_value = '3'` is treated as Finalized even if `lead_state.status` is not `'closed'` — `_finalize_campaign()` writes to GHL but does not update `status`. Never treat tier-3 leads as active.

**Implementation**: `app/services/lead_lifecycle.py` — `get_lead_lifecycle()`

---

## GET /dashboard/campaign-overview

Returns lead rows for the Campaign Overview page — one row per contact active in the specified window.

**Auth**: Optional

**Query params**:
- `from_date` (optional): ISO date, default `now() - 7 days`
- `to_date` (optional): ISO date, default `now()`
- `campaign` (optional): `New Lead` | `Cold Lead`
- `limit` (optional): integer, max 500, default 200

**Response 200**
```json
{
  "from_date": "2026-04-03",
  "to_date": "2026-04-10",
  "rows": [
    {
      "contact_id": "abc123",
      "contact": "+16025550101",
      "campaign_name": "New Lead",
      "last_call_at": "2026-04-09T18:32:00Z",
      "next_action": "send_sms",
      "status": "active",
      "sales_outcome": "follow_up",
      "sales_next_action": "Send pricing info",
      "sales_follow_up_at": "2026-04-11T14:00:00Z",
      "sales_updated_by": "agent_01"
    }
  ],
  "total": 38
}
```

**Notes**:
- This endpoint is read-only. Outcome fields (`sales_outcome`, `sales_next_action`, etc.) reflect whatever was last saved via `POST /dashboard/sales-queue/outcome`.
- The Campaign Overview page renders these fields as read-only badges — no inline editing.

---

## POST /dashboard/sales-queue/outcome

Log a post-call sales outcome from the Sales Queue view. Updates `lead_state` with the outcome and optional next-action scheduling.

**Auth**: Required

**Request body**
```json
{
  "contact_id": "abc123",
  "sales_outcome": "follow_up",
  "sales_next_action": "Send pricing email",
  "sales_follow_up_at": "2026-04-11T14:00:00Z",
  "sales_notes": "Interested, wants to compare with competitor",
  "updated_by": "agent_01"
}
```

**Valid `sales_outcome` values**: `booked` | `follow_up` | `not_interested` | `no_answer` | `voicemail` | `wrong_number`

**Terminal outcomes** (row moves to "Completed" in Sales Queue): `booked`, `not_interested`, `wrong_number`

**Validation rules**:
- `sales_outcome` required; must be a known value.
- `sales_next_action` + `sales_follow_up_at` required when `sales_outcome == "follow_up"`.
- `sales_notes` max 200 characters.
- `updated_by` required.

**Response 200**
```json
{
  "status": "ok",
  "contact_id": "abc123",
  "sales_outcome": "follow_up",
  "is_terminal": false
}
```

**Response 422**: missing required fields or unknown outcome value.

---

## WebSocket ws://host:8001/dashboard/ws/events

**Protocol**: JSON messages, one event per message.

**Message schema**
```json
{
  "id": "uuid",
  "event_type": "job_completed",
  "entity_type": "call",
  "entity_id": "call-id",
  "contact_id": "contact-id",
  "message": "Human-readable summary",
  "payload": {},
  "created_at": "2026-04-05T14:00:00Z"
}
```

**Error message (Redis unavailable)**
```json
{
  "type": "error",
  "code": "realtime_unavailable",
  "fallback_url": "/dashboard/events"
}
```

---

## GET /dashboard/db/tables

Returns all Postgres user tables with live row estimates.

**Auth**: None required (read-only metadata)

**Response 200**
```json
{
  "tables": [
    { "name": "call_events", "row_estimate": 23500 },
    { "name": "lead_state",  "row_estimate": 1028 }
  ]
}
```

**Notes**:
- Row estimates come from `pg_stat_user_tables.n_live_tup` — they are approximate (updated by autovacuum) and may lag slightly behind the true count.
- Tables are sorted alphabetically.

---

## POST /dashboard/db/query

Execute an arbitrary SQL statement against the production database and return results as JSON.

**Auth**: Required — `Authorization: Bearer {SECRET_KEY}`

**Request body**
```json
{ "sql": "SELECT * FROM lead_state LIMIT 10;" }
```

**Response 200 — SELECT**
```json
{
  "columns": ["contact_id", "campaign_name", "status"],
  "rows": [
    ["+17204925394", "Cold Lead", null],
    ["+13616494022", "Cold Lead", null]
  ],
  "row_count": 2,
  "truncated": false
}
```

**Response 200 — DML (INSERT / UPDATE / DELETE)**
```json
{
  "columns": [],
  "rows": [],
  "row_count": 3,
  "truncated": false
}
```
`row_count` is the number of rows affected. DML is auto-committed.

**Response 400** — SQL syntax error or runtime error. `detail` contains the Postgres error message.

**Limits**:
- Maximum 500 rows returned for SELECT queries. If the result set exceeds 500 rows, `truncated: true` is set and only the first 500 rows are returned.
- `null` values in rows are returned as JSON `null`.
- All non-null cell values are coerced to strings.

**Frontend**: Accessible at `/db-explorer`. Includes a table browser (left sidebar) and CSV download button. The downloaded `.csv` opens natively in Excel.

---

## GET /dashboard/webhook-failures

Returns Synthflow webhook delivery health for the last 24 hours — calls that completed (`launch_outbound_call` job status = `completed`) but never produced a matching `call_events` row within 7 days after execution.

**Auth**: None (read-only health data).

**Response 200**
```json
{
  "summary": {
    "total_launched": 660,
    "got_webhook":    655,
    "missing":        5,
    "webhook_pct":    99
  },
  "failures": [
    {
      "job_id":                  "uuid",
      "contact_id":              "+15551234567",
      "campaign":                "Cold Lead",
      "placed_at":               "2026-04-30T10:42:00Z",
      "executed_at":             "2026-04-30T10:42:40Z",
      "minutes_since_execution": 38
    }
  ],
  "window_hours": 24,
  "recorded_at":  "2026-04-30T11:20:00Z"
}
```

**Exclusions (summary and failures list)**:
- Jobs executed within the last 20 minutes (Synthflow may still be delivering).
- Leads whose `lead_state.status` is `terminal` or `closed` — already resolved by operator action.
- Leads with an active `pending`/`claimed`/`running` `launch_outbound_call` job — already being handled (retry scheduled or duplicate-job guard confirmed one exists).

**Frontend**: Rendered as the **Webhook Delivery — 24h** collapsible panel in Queue Health (`/queue`). Header shows delivery %, got/total counts, and a missing badge when `missing > 0`. Expanded drawer shows the failures table with inline action buttons.

---

## POST /dashboard/actions/advance-stale-lead

Manually advance a stale lead whose Synthflow webhook was missed. Operator confirms the outcome in Synthflow logs and clicks the corresponding button in the Webhook Delivery panel.

**Auth**: Required — `Authorization: Bearer {SECRET_KEY}` + `X-Operator-Id` header.

**Request body**
```json
{ "contact_id": "+15551234567", "outcome": "voicemail" }
```
`outcome` must be `"voicemail"` or `"no_answer"`.

**Response 200 — voicemail, non-tier-2**
```json
{ "status": "ok", "action": "advanced", "tier_from": "1", "tier_to": "2", "run_at": "...", "audit_log_id": "uuid" }
```

**Response 200 — voicemail, tier 2 (finalizes)**
```json
{ "status": "ok", "action": "finalized", "tier_from": "2", "reason": "tier_2_voicemail_complete" }
```

**Response 200 — no_answer, first miss (schedules retry)**
```json
{ "status": "ok", "action": "retry_scheduled", "tier": "1", "run_at": "...", "audit_log_id": "uuid" }
```

**Response 200 — no_answer, consecutive miss (closes)**
```json
{ "status": "ok", "action": "closed", "reason": "consecutive_no_answer", "last_call_status": "no_answer" }
```

**Response 404** — contact_id not found in `lead_state`.

**Response 409** — lead already has a pending/claimed/running job; action cancelled to avoid duplicate.

**Side effects**: Updates `lead_state` (tier, status, last_call_status), schedules a `launch_outbound_call` job (voicemail/retry paths), writes GHL field updates (finalize/close paths), writes `audit_log` row.

**Idempotency**: The 409 guard prevents double-processing. GHL writes resolve phone → UUID via `search_contact_by_phone()`.

---

## POST /dashboard/actions/recover-call-webhook

Fetch a completed call from the Synthflow API by call_id and replay the full `process_call_event` pipeline — exactly as if the webhook had been delivered. Use when a call completed in Synthflow but no webhook arrived.

**Auth**: Required — `Authorization: Bearer {SECRET_KEY}` + `X-Operator-Id` header.

**Request body**
```json
{ "contact_id": "+15551234567", "call_id": "synthflow-call-id-from-logs-page" }
```

**Response 200**
```json
{
  "status":            "ok",
  "action":            "recovery_scheduled",
  "synthflow_call_id": "abc123",
  "call_status":       "completed",
  "campaign_name":     "Cold Lead",
  "audit_log_id":      "uuid"
}
```

**Response 409** — a `process_call_event` job for this `call_id` is already active.

**Response 502** — Synthflow API returned an error or timed out.

**Pipeline**: Fetches `GET https://api.synthflow.ai/v2/calls/{call_id}` → normalizes payload (infers `campaign_name` from Agent field, injects `contact_id`) → schedules `process_call_event` job → worker runs AI analysis, updates `lead_state`, writes GHL fields.

**Idempotency**: `call_events.dedupe_key = "{call_id}:process_call_event"` (unique constraint) prevents duplicate DB inserts if the endpoint is called twice for the same call.

**Frontend**: "Call Completed" button in the Webhook Delivery panel. Clicking expands an inline input for the Synthflow call_id (found in Synthflow Logs page). Pressing Enter or clicking "Fetch →" submits. Row disappears from the panel once the job is active.

