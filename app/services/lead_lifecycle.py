"""
lead_lifecycle.py — Aggregate per-lead journey data for the Lead Lifecycle Monitor.

Returns:
  - summary: counts of active / vm-sequence / switched / finalized / avg days to close
  - rows: one row per lead_state record with full journey fields
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


# ── Summary counts ────────────────────────────────────────────────────────────

_SUMMARY_SQL = """
SELECT
    COUNT(*)
        FILTER (WHERE (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal')) AND ls.do_not_call IS NOT TRUE)
                                                        AS active,
    COUNT(*)
        FILTER (WHERE ls.ai_campaign_value IS NOT NULL
                  AND ls.ai_campaign_value != '3'
                  AND ls.status NOT IN ('closed', 'terminal'))
                                                        AS in_vm_sequence,
    COUNT(*)
        FILTER (WHERE EXISTS (
            SELECT 1 FROM audit_log al
            WHERE al.entity_id = ls.contact_id
              AND al.action = 'campaign_switch'
        ))                                              AS campaign_switched,
    COUNT(*)
        FILTER (WHERE ls.status IN ('closed', 'terminal') OR ls.do_not_call IS TRUE)
                                                        AS finalized,
    ROUND(CAST(AVG(
        CASE
            WHEN ls.status IN ('closed', 'terminal') OR ls.do_not_call IS TRUE
            THEN EXTRACT(EPOCH FROM (ls.updated_at - first_ce.first_contact)) / 86400.0
        END
    ) AS numeric), 1)                                   AS avg_days_to_close
FROM lead_state ls
LEFT JOIN LATERAL (
    SELECT MIN(COALESCE(ce.call_started_at, ce.created_at)) AS first_contact
    FROM call_events ce
    WHERE ce.contact_id = ls.contact_id
) first_ce ON TRUE;
"""

# ── Per-lead rows ─────────────────────────────────────────────────────────────

_ROWS_SQL = """
WITH call_agg AS (
    SELECT
        ls.contact_id                                                       AS contact_id,
        MIN(COALESCE(ce.call_started_at, ce.created_at))                   AS first_contact_at,
        MAX(COALESCE(ce.call_started_at, ce.created_at))                   AS last_contact_at,
        COUNT(ce.id)                                                        AS total_calls,
        MAX(ce.lead_name)                                                   AS lead_name,
        -- last intent: from the most recent call that has one
        (
            SELECT ce2.detected_intent
            FROM call_events ce2
            WHERE ce2.contact_id = ls.contact_id
              AND ce2.detected_intent IS NOT NULL
            ORDER BY COALESCE(ce2.call_started_at, ce2.created_at) DESC
            LIMIT 1
        )                                                                   AS last_intent
    FROM lead_state ls
    LEFT JOIN call_events ce ON ce.contact_id = ls.contact_id
    GROUP BY ls.contact_id
),
msg_agg AS (
    SELECT
        contact_id,
        COUNT(*) FILTER (WHERE channel = 'sms')        AS total_sms,
        COUNT(*) FILTER (WHERE channel = 'email')      AS total_email
    FROM outbound_messages
    GROUP BY contact_id
),
initial_campaign AS (
    SELECT DISTINCT ON (entity_id)
        entity_id                               AS contact_id,
        context_json->>'from'                  AS campaign
    FROM audit_log
    WHERE action = 'campaign_switch'
    ORDER BY entity_id, created_at ASC
),
switch_count AS (
    SELECT entity_id AS contact_id, COUNT(*) AS switches
    FROM audit_log
    WHERE action = 'campaign_switch'
    GROUP BY entity_id
),
next_job AS (
    SELECT DISTINCT ON (entity_id)
        entity_id   AS contact_id,
        job_type    AS next_job_type,
        run_at      AS next_run_at
    FROM scheduled_jobs
    WHERE status = 'pending'
    ORDER BY entity_id, run_at ASC
),
finalization AS (
    SELECT DISTINCT ON (entity_id)
        entity_id                               AS contact_id,
        context_json->>'reason'                AS reason,
        created_at                              AS finalized_at
    FROM audit_log
    WHERE action IN ('finalize', 'cancel', 'campaign_off', 'do_not_call')
    ORDER BY entity_id, created_at DESC
)
SELECT
    ls.contact_id,
    COALESCE(ca.lead_name, ls.contact_id)           AS lead_name,
    ls.normalized_phone,
    ls.campaign_name                                AS current_campaign,
    COALESCE(ic.campaign, ls.campaign_name)         AS initial_campaign,
    ls.ai_campaign_value                            AS vm_tier,
    ls.status,
    ls.do_not_call,
    ca.first_contact_at,
    ca.last_contact_at,
    CAST(ROUND(
        EXTRACT(EPOCH FROM (NOW() - ca.first_contact_at)) / 86400.0
    ) AS int)                                       AS days_active,
    COALESCE(ca.total_calls, 0)                     AS total_calls,
    COALESCE(ma.total_sms, 0)                       AS total_sms,
    COALESCE(ma.total_email, 0)                     AS total_email,
    ca.last_intent,
    COALESCE(sc.switches, 0)                        AS campaign_switches,
    nj.next_job_type,
    nj.next_run_at,
    fin.reason                                      AS finalization_reason,
    fin.finalized_at
FROM lead_state ls
LEFT JOIN call_agg       ca  ON ca.contact_id  = ls.contact_id
LEFT JOIN msg_agg        ma  ON ma.contact_id  = ls.contact_id
LEFT JOIN initial_campaign ic ON ic.contact_id = ls.contact_id
LEFT JOIN switch_count   sc  ON sc.contact_id  = ls.contact_id
LEFT JOIN next_job       nj  ON nj.contact_id  = ls.contact_id
LEFT JOIN finalization   fin ON fin.contact_id = ls.contact_id
WHERE (:status_filter = 'all'
       OR (:status_filter = 'active'    AND (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal')) AND ls.do_not_call IS NOT TRUE)
       OR (:status_filter = 'finalized' AND (ls.status IN ('closed', 'terminal') OR ls.do_not_call IS TRUE))
       OR (:status_filter = 'vm'        AND ls.ai_campaign_value IS NOT NULL AND ls.ai_campaign_value != '3')
       OR (:status_filter = 'dnc'       AND ls.do_not_call IS TRUE))
  AND (:campaign_filter = 'all' OR ls.campaign_name = :campaign_filter)
ORDER BY ca.first_contact_at DESC NULLS LAST
LIMIT  :limit
OFFSET :offset;
"""

_COUNT_SQL = """
SELECT COUNT(*)
FROM lead_state ls
WHERE (:status_filter = 'all'
       OR (:status_filter = 'active'    AND (ls.status IS NULL OR ls.status NOT IN ('closed', 'terminal')) AND ls.do_not_call IS NOT TRUE)
       OR (:status_filter = 'finalized' AND (ls.status IN ('closed', 'terminal') OR ls.do_not_call IS TRUE))
       OR (:status_filter = 'vm'        AND ls.ai_campaign_value IS NOT NULL AND ls.ai_campaign_value != '3')
       OR (:status_filter = 'dnc'       AND ls.do_not_call IS TRUE))
  AND (:campaign_filter = 'all' OR ls.campaign_name = :campaign_filter);
"""


def get_lead_lifecycle(
    session: Session,
    status_filter: str = "all",
    campaign_filter: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    # Summary
    summary_row = session.execute(text(_SUMMARY_SQL)).fetchone()
    summary = {
        "active":            int(summary_row[0] or 0),
        "in_vm_sequence":    int(summary_row[1] or 0),
        "campaign_switched": int(summary_row[2] or 0),
        "finalized":         int(summary_row[3] or 0),
        "avg_days_to_close": float(summary_row[4]) if summary_row[4] is not None else None,
    }

    params = {
        "status_filter":   status_filter,
        "campaign_filter": campaign_filter,
        "limit":           limit,
        "offset":          offset,
    }

    total = int(session.execute(text(_COUNT_SQL), params).scalar() or 0)
    rows_raw = session.execute(text(_ROWS_SQL), params).fetchall()

    rows = []
    for r in rows_raw:
        rows.append({
            "contact_id":          r[0],
            "lead_name":           r[1],
            "phone":               r[2],
            "current_campaign":    r[3],
            "initial_campaign":    r[4],
            "vm_tier":             r[5],
            "status":              r[6],
            "do_not_call":         bool(r[7]) if r[7] is not None else False,
            "first_contact_at":    r[8].isoformat()  if r[8]  else None,
            "last_contact_at":     r[9].isoformat()  if r[9]  else None,
            "days_active":         int(r[10]) if r[10] is not None else None,
            "total_calls":         int(r[11]),
            "total_sms":           int(r[12]),
            "total_email":         int(r[13]),
            "last_intent":         r[14],
            "campaign_switches":   int(r[15]),
            "next_job_type":       r[16],
            "next_run_at":         r[17].isoformat() if r[17] else None,
            "finalization_reason": r[18],
            "finalized_at":        r[19].isoformat() if r[19] else None,
        })

    return {
        "summary": summary,
        "total":   total,
        "rows":    rows,
        "filters": {"status": status_filter, "campaign": campaign_filter},
    }
