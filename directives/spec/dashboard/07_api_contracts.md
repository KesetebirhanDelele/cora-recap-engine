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
- `campaign` (optional): `New Lead` | `Cold Lead` | `Inbound`
- `from_date` (optional): ISO date, default `now() - 7 days`
- `to_date` (optional): ISO date, default `now()`

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
