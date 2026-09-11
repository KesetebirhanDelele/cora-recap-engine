"""
Alerting service — evaluates health metrics against thresholds, deduplicates
alert state, and dispatches SMTP email notifications.

Called from app/worker/jobs/metrics_jobs.py once per 60-second metrics cycle.
Non-fatal: all exceptions are caught and logged; the metrics cycle must not
fail due to alerting errors.

Alert types (all thresholds settings-driven):
  queue_lag_exceeded    — queue_lag_seconds > ALERT_QUEUE_LAG_THRESHOLD_SECONDS
  error_rate_spike      — error_rate > ALERT_ERROR_RATE_THRESHOLD
  exception_spike       — open_exception_count >= ALERT_EXCEPTION_SPIKE_THRESHOLD
  worker_offline        — active_workers == 0
  ghl_auth_failure      — open exceptions of type 'ghl_auth_failed' >= 1
  intake_auth_failure   — open exceptions of type 'intake_auth_failed' >= 1
                          (GHL lead-intake webhook rejecting on auth)
  outbound_calls_stalled — 0 launch_outbound_call completions in
                          ALERT_OUTBOUND_STALL_HOURS during an active,
                          unpaused campaign window
  duplicate_rate_spike  — (not yet computable; reserved for future metric)
  new_exception         — (spec/30) one email per newly-opened exceptions row,
                          any type/severity — the per-incident counterpart to
                          exception_spike's aggregate threshold. To Kes.
  sales_queue_urgent    — (spec/30) one email per lead newly showing "urgent"
                          sales priority (see dashboard_metrics._compute_sales_priority),
                          routed to Rose (admissions) and/or Taiwo (payment/IPBC)
                          by transcript keyword, or by name if the caller asked
                          for one of them specifically. Fires exactly once per
                          lead, ever — not tracked against resolution (the
                          Sales Queue itself is the mechanism for that).

Deduplication key: (alert_type, status='active').
  - If an active row for the same alert_type was created within
    ALERT_DEDUP_WINDOW_SECONDS: update last_seen_at only; skip email.
  - If no such row: insert new active row; send email.
  - Resolution: if condition clears, resolve all active rows for that type.
"""
from __future__ import annotations

import logging
import re
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Delayed-email debounce (2026-09-11) ─────────────────────────────────────
#
# Kes: most of these clear themselves before anyone needs to act — don't
# email until a problem has actually persisted. Calibrated against real
# alert_events history (median time from created_at to resolved_at):
#   webhook_drop_detected   4.5 min  (n=31)
#   error_rate_spike        7.5 min  (n=15)
#   queue_lag_exceeded      9.6 min  (n=46, heavily right-skewed — some
#                            persist for days, but the median blip clears
#                            fast)
#   exception_spike         ~9.5h    (n=26) — rarely self-heals quickly, but
#                            still worth a short debounce for the rare
#                            instant blip
# Deliberately NOT delayed: outbound_calls_stalled (the flatlined-pipeline
# backstop — see PROGRESS.md 2026-09-08's 12-day silent outage, the reason
# this alert exists at all; only 2 historical samples besides, too little to
# calibrate), worker_offline (zero workers is unambiguous and severe, never
# seen a resolved row in history), ghl_auth_failure / intake_auth_failure
# (an auth failure doesn't self-heal — waiting only delays the fix).
_ALERT_EMAIL_DELAY_SECONDS: dict[str, int] = {
    "queue_lag_exceeded": 900,
    "webhook_drop_detected": 900,
    "error_rate_spike": 900,
    "exception_spike": 300,
}

# ── Alert definitions ─────────────────────────────────────────────────────────

_ALERT_DEFINITIONS = [
    {
        "alert_type": "queue_lag_exceeded",
        "severity": "critical",
        "metric_key": "queue_lag_seconds",
        "threshold_setting": "alert_queue_lag_threshold_seconds",
        "condition": "gt",
        "message_template": "Queue lag is {value:.0f}s, threshold is {threshold:.0f}s",
    },
    {
        "alert_type": "error_rate_spike",
        "severity": "warning",
        "metric_key": "error_rate",
        "threshold_setting": "alert_error_rate_threshold",
        "condition": "gt",
        "message_template": "Error rate is {value:.1%}, threshold is {threshold:.1%}",
    },
    {
        "alert_type": "exception_spike",
        "severity": "warning",
        "metric_key": "open_exception_count",
        "threshold_setting": "alert_exception_spike_threshold",
        "condition": "gte",
        "message_template": "Open exceptions: {value:.0f}, threshold is {threshold:.0f}",
    },
    {
        "alert_type": "worker_offline",
        "severity": "critical",
        "metric_key": "active_workers",
        "threshold_setting": None,   # threshold = 0 (must be > 0)
        "condition": "eq_zero",
        "message_template": "No active workers detected",
    },
]


def _is_breached(condition: str, value: float | None, threshold: float) -> bool:
    """Return True if the alert condition is met."""
    if value is None:
        return False
    if condition == "gt":
        return value > threshold
    if condition == "gte":
        return value >= threshold
    if condition == "eq_zero":
        return value == 0
    return False


# ── Main entry point ──────────────────────────────────────────────────────────

def evaluate_alerts(session: Session, settings: Any) -> None:
    """
    Evaluate all alert conditions against the current health metrics.
    Called once per metrics cycle (60 seconds).

    Non-fatal: exceptions are caught per-alert; no alert failure propagates up.
    """
    from app.services.dashboard_metrics import get_health

    try:
        health = get_health(session)
    except Exception as exc:
        logger.error("alerting: failed to compute health for alert eval: %s", exc)
        return

    now = datetime.now(tz=timezone.utc)
    dedup_window = timedelta(seconds=settings.alert_dedup_window_seconds)

    # Also check ghl_auth_failure separately (exception-type query)
    try:
        _evaluate_ghl_auth_failure(session, settings, now, dedup_window)
    except Exception as exc:
        logger.error("alerting: ghl_auth_failure eval failed: %s", exc)

    try:
        _evaluate_webhook_drop(session, settings, now, dedup_window)
    except Exception as exc:
        logger.error("alerting: webhook_drop eval failed: %s", exc)

    try:
        _evaluate_intake_auth_failure(session, settings, now, dedup_window)
    except Exception as exc:
        logger.error("alerting: intake_auth_failure eval failed: %s", exc)

    try:
        _evaluate_outbound_stall(session, settings, now, dedup_window)
    except Exception as exc:
        logger.error("alerting: outbound_stall eval failed: %s", exc)

    try:
        _evaluate_new_exceptions(session, settings, now)
    except Exception as exc:
        logger.error("alerting: new_exceptions eval failed: %s", exc)

    try:
        _evaluate_sales_queue_urgent(session, settings, now)
    except Exception as exc:
        logger.error("alerting: sales_queue_urgent eval failed: %s", exc)

    for defn in _ALERT_DEFINITIONS:
        try:
            _evaluate_single_alert(
                session=session,
                settings=settings,
                defn=defn,
                health=health,
                now=now,
                dedup_window=dedup_window,
            )
        except Exception as exc:
            logger.error(
                "alerting: alert eval failed for %s: %s", defn["alert_type"], exc
            )

    try:
        _send_pending_delayed_alert_emails(session, settings, now)
    except Exception as exc:
        logger.error("alerting: pending_delayed_emails eval failed: %s", exc)


def _send_pending_delayed_alert_emails(session: Session, settings: Any, now: datetime) -> None:
    """
    For alert types in _ALERT_EMAIL_DELAY_SECONDS, the creation path leaves
    email_sent_at NULL instead of emailing right away. Once an active row
    has existed at least that type's delay without resolving, send the
    email here, once. Runs every cycle, independent of which evaluator
    created the row — a row that resolves before crossing its delay never
    gets picked up here at all (status='active' filter), which is the
    whole point: self-healing blips never generate an email.
    """
    for alert_type, delay_seconds in _ALERT_EMAIL_DELAY_SECONDS.items():
        cutoff = now - timedelta(seconds=delay_seconds)
        rows = session.execute(
            text("""
                SELECT id, severity, message
                FROM alert_events
                WHERE alert_type = :t AND status = 'active'
                  AND email_sent_at IS NULL AND created_at <= :cutoff
            """),
            {"t": alert_type, "cutoff": cutoff},
        ).fetchall()
        for row in rows:
            alert_id, severity, message = row
            _send_alert_email(
                settings=settings, alert_id=alert_id, alert_type=alert_type,
                severity=severity, message=message, now=now,
            )
            session.execute(
                text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
                {"now": now, "id": alert_id},
            )


def _evaluate_single_alert(
    session: Session,
    settings: Any,
    defn: dict,
    health: dict,
    now: datetime,
    dedup_window: timedelta,
) -> None:
    """Evaluate a single alert definition and upsert alert_events accordingly."""
    from app.models.alert_event import AlertEvent

    alert_type = defn["alert_type"]
    metric_key = defn["metric_key"]
    value = health.get(metric_key)

    threshold_setting = defn.get("threshold_setting")
    threshold: float = (
        float(getattr(settings, threshold_setting))
        if threshold_setting
        else 0.0
    )

    breached = _is_breached(defn["condition"], value, threshold)

    # Find existing active row for this alert type
    existing = session.execute(
        text("""
            SELECT id, created_at
            FROM alert_events
            WHERE alert_type = :alert_type AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"alert_type": alert_type},
    ).fetchone()

    if breached:
        msg = defn["message_template"].format(
            value=value if value is not None else 0,
            threshold=threshold,
        )
        if existing:
            # Dedup: update last_seen_at only if within window
            if now - existing[1].replace(tzinfo=timezone.utc) < dedup_window:
                session.execute(
                    text("""
                        UPDATE alert_events
                        SET last_seen_at = :now
                        WHERE id = :id
                    """),
                    {"now": now, "id": existing[0]},
                )
                return  # skip email
            # Outside dedup window — treat as new alert burst
        # Insert new active row
        alert_id = str(uuid.uuid4())
        row = AlertEvent(
            id=alert_id,
            alert_type=alert_type,
            severity=defn["severity"],
            status="active",
            current_value=float(value) if value is not None else None,
            threshold=threshold if threshold_setting else None,
            message=msg,
            last_seen_at=now,
            created_at=now,
        )
        session.add(row)
        session.flush()
        if not _ALERT_EMAIL_DELAY_SECONDS.get(alert_type):
            _send_alert_email(settings=settings, alert_id=alert_id, alert_type=alert_type,
                              severity=defn["severity"], message=msg, now=now)
            session.execute(
                text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
                {"now": now, "id": alert_id},
            )
        # else: email_sent_at stays NULL — _send_pending_delayed_alert_emails
        # sends it later only if the condition is still active past the delay.
    else:
        # Condition cleared — resolve any active row. Per Kes (2026-09-11):
        # no resolution email, status update only. The active alert already
        # told them about the problem; a follow-up "it's fine now" email is
        # noise, not signal.
        if existing:
            session.execute(
                text("""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = :now
                    WHERE alert_type = :alert_type AND status = 'active'
                """),
                {"now": now, "alert_type": alert_type},
            )


def _evaluate_ghl_auth_failure(
    session: Session,
    settings: Any,
    now: datetime,
    dedup_window: timedelta,
) -> None:
    """Check for open GHL auth-failure exceptions and alert if any found."""
    from app.models.alert_event import AlertEvent

    row = session.execute(text("""
        SELECT COUNT(*) FROM exceptions
        WHERE type = 'ghl_auth_failed' AND status = 'open'
    """)).fetchone()
    count = int(row[0]) if row else 0

    alert_type = "ghl_auth_failure"
    existing = session.execute(
        text("""
            SELECT id, created_at FROM alert_events
            WHERE alert_type = :alert_type AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"alert_type": alert_type},
    ).fetchone()

    if count >= 1:
        msg = f"GHL auth failure detected ({count} open exception(s))"
        if existing:
            if now - existing[1].replace(tzinfo=timezone.utc) < dedup_window:
                session.execute(
                    text("UPDATE alert_events SET last_seen_at = :now WHERE id = :id"),
                    {"now": now, "id": existing[0]},
                )
                return
        alert_id = str(uuid.uuid4())
        row_obj = AlertEvent(
            id=alert_id,
            alert_type=alert_type,
            severity="critical",
            status="active",
            current_value=float(count),
            threshold=1.0,
            message=msg,
            last_seen_at=now,
            created_at=now,
        )
        session.add(row_obj)
        session.flush()
        _send_alert_email(settings=settings, alert_id=alert_id, alert_type=alert_type,
                          severity="critical", message=msg, now=now)
        session.execute(
            text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
            {"now": now, "id": alert_id},
        )
    else:
        # No resolution email — see _evaluate_single_alert's matching comment.
        if existing:
            session.execute(
                text("""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = :now
                    WHERE alert_type = :alert_type AND status = 'active'
                """),
                {"now": now, "alert_type": alert_type},
            )


# ── Shared upsert / resolve helpers (used by the checks below) ────────────────

def _upsert_active_alert(
    session: Session,
    settings: Any,
    now: datetime,
    *,
    alert_type: str,
    severity: str,
    message: str,
    current_value: float | None = None,
    threshold: float | None = None,
) -> None:
    """Insert a new active alert row + one email when this condition is not
    already active; otherwise just advance last_seen_at. One page per
    incident (from onset until it resolves), not one per metrics cycle —
    these checks run every 60s and their conditions persist for the whole
    outage."""
    from app.models.alert_event import AlertEvent

    existing = session.execute(
        text("""
            SELECT id FROM alert_events
            WHERE alert_type = :t AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"t": alert_type},
    ).fetchone()

    if existing:
        session.execute(
            text("UPDATE alert_events SET last_seen_at = :now WHERE id = :id"),
            {"now": now, "id": existing[0]},
        )
        return

    alert_id = str(uuid.uuid4())
    session.add(AlertEvent(
        id=alert_id,
        alert_type=alert_type,
        severity=severity,
        status="active",
        current_value=current_value,
        threshold=threshold,
        message=message,
        last_seen_at=now,
        created_at=now,
    ))
    session.flush()
    _send_alert_email(settings=settings, alert_id=alert_id, alert_type=alert_type,
                      severity=severity, message=message, now=now)
    session.execute(
        text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
        {"now": now, "id": alert_id},
    )


def _resolve_active_alert(
    session: Session,
    now: datetime,
    *,
    alert_type: str,
) -> None:
    """Resolve any active row for alert_type. No-op when nothing is active.
    No resolution email — see _evaluate_single_alert's matching comment
    (2026-09-11): the active alert already told them about the problem, a
    follow-up "it's fine now" email is noise."""
    existing = session.execute(
        text("""
            SELECT id FROM alert_events
            WHERE alert_type = :t AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"t": alert_type},
    ).fetchone()
    if not existing:
        return
    session.execute(
        text("""
            UPDATE alert_events SET status = 'resolved', resolved_at = :now
            WHERE alert_type = :t AND status = 'active'
        """),
        {"now": now, "t": alert_type},
    )


# ── Lead-intake auth-failure alert ───────────────────────────────────────────

def _evaluate_intake_auth_failure(
    session: Session,
    settings: Any,
    now: datetime,
    dedup_window: timedelta,
) -> None:
    """
    Fire when POST /v1/webhooks/leads is rejecting requests on auth.

    A 401 storm there means whatever is configured to trigger outbound calls
    (GHL workflow actions) is being turned away — no ScheduledJob, no
    call_event, nothing else in this system notices. Driven by the
    deduplicated 'intake_auth_failed' exceptions call_intake.py records.
    """
    alert_type = "intake_auth_failure"
    severity = "critical"

    count = int(session.execute(text("""
        SELECT COUNT(*) FROM exceptions
        WHERE type = 'intake_auth_failed' AND status = 'open'
    """)).scalar() or 0)

    if count >= 1:
        _upsert_active_alert(
            session, settings, now,
            alert_type=alert_type, severity=severity,
            message=(
                f"Lead-intake webhook is rejecting requests on auth "
                f"({count} open). GHL call triggers are being dropped — check the "
                f"X-Cora-Webhook-Secret header on the GHL workflow actions against "
                f"CORA_INBOUND_WEBHOOK_SECRET on the server."
            ),
            current_value=float(count), threshold=1.0,
        )
    else:
        _resolve_active_alert(session, now, alert_type=alert_type)


# ── Outbound-call stall alert ────────────────────────────────────────────────

def _evaluate_outbound_stall(
    session: Session,
    settings: Any,
    now: datetime,
    dedup_window: timedelta,
) -> None:
    """
    Fire when zero outbound calls have been placed for
    ALERT_OUTBOUND_STALL_HOURS while a campaign is inside its active window
    and outbound calling is not paused.

    Cora's core output is outbound calls, and nothing else alerts on their
    absence: a broken upstream trigger produces no failed job and no
    exception, so the pipeline can flatline silently (it did, 12 days,
    Aug–Sep 2026). This is the backstop for that whole class of failure.
    """
    alert_type = "outbound_calls_stalled"
    severity = "critical"

    # Deliberately held → silence is expected.
    try:
        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused or flags.outbound_campaigns_paused:
            _resolve_active_alert(session, now, alert_type=alert_type)
            return
    except Exception as exc:
        logger.warning("outbound_stall: mode-flag read failed, continuing: %s", exc)

    # Outside every campaign's active window (nights / weekends) → expected quiet.
    # Fail toward alerting: if the window check errors, assume active.
    try:
        from app.core.campaign_schedule import is_campaign_active
        any_active = (
            is_campaign_active("New Lead", now, settings, session=session)
            or is_campaign_active("Cold Lead", now, settings, session=session)
        )
    except Exception as exc:
        logger.warning("outbound_stall: active-window check failed, assuming active: %s", exc)
        any_active = True
    if not any_active:
        _resolve_active_alert(session, now, alert_type=alert_type)
        return

    hours = int(getattr(settings, "alert_outbound_stall_hours", 4) or 4)
    window_start = now - timedelta(hours=hours)
    completions = int(session.execute(
        text("""
            SELECT COUNT(*) FROM scheduled_jobs
            WHERE job_type = 'launch_outbound_call'
              AND status = 'completed'
              AND updated_at >= :w
        """),
        {"w": window_start},
    ).scalar() or 0)

    if completions == 0:
        _upsert_active_alert(
            session, settings, now,
            alert_type=alert_type, severity=severity,
            message=(
                f"No outbound calls completed in the last {hours}h during an active "
                f"campaign window. Check the GHL workflow triggers, the "
                f"/v1/webhooks/leads intake endpoint, and the Synthflow Make Call workflow."
            ),
            current_value=0.0, threshold=1.0,
        )
    else:
        _resolve_active_alert(session, now, alert_type=alert_type)


# ── Webhook drop alert ────────────────────────────────────────────────────────

def _evaluate_webhook_drop(
    session: Session,
    settings: Any,
    now: datetime,
    dedup_window: timedelta,
) -> None:
    """
    Fire webhook_drop_detected when any 10-min bucket in the last 2 hours has
    < 80% webhook delivery AND >= 5 calls launched.

    Excludes the most recent 20 minutes so in-flight webhooks don't cause
    false positives. Uses the same dedup/resolve pattern as all other alerts.
    """
    from app.models.alert_event import AlertEvent

    row = session.execute(text("""
        SELECT bucket, total, got_webhook,
               total - got_webhook AS missing,
               ROUND(100.0 * got_webhook::numeric / NULLIF(total, 0)) AS webhook_pct
        FROM (
            SELECT
                date_trunc('hour', sj.updated_at)
                    + (EXTRACT(MINUTE FROM sj.updated_at)::int / 10) * INTERVAL '10 minutes' AS bucket,
                COUNT(*) AS total,
                COUNT(
                    CASE WHEN ce.call_id IS NOT NULL OR recovery.id IS NOT NULL OR ignored.id IS NOT NULL THEN 1 END
                ) AS got_webhook
            FROM scheduled_jobs sj
            LEFT JOIN LATERAL (
                SELECT call_id FROM call_events
                WHERE contact_id = sj.payload_json->>'contact_id'
                  AND created_at >= sj.updated_at - INTERVAL '10 minutes'
                  AND created_at <= sj.updated_at + INTERVAL '4 hours'
                LIMIT 1
            ) ce ON true
            LEFT JOIN LATERAL (
                SELECT id FROM audit_log
                WHERE entity_id = sj.payload_json->>'contact_id'
                  AND action IN ('manual_webhook_recovery', 'manual_advance')
                  AND created_at >= sj.updated_at - INTERVAL '30 minutes'
                LIMIT 1
            ) recovery ON true
            LEFT JOIN LATERAL (
                SELECT id FROM audit_log
                WHERE entity_id = sj.id
                  AND action = 'manual_webhook_ignore'
                LIMIT 1
            ) ignored ON true
            WHERE sj.job_type  = 'launch_outbound_call'
              AND sj.status    = 'completed'
              AND sj.updated_at >= NOW() - INTERVAL '2 hours'
              AND sj.updated_at <= NOW() - INTERVAL '20 minutes'
            GROUP BY 1
        ) buckets
        WHERE total >= 5
          AND got_webhook::float / NULLIF(total, 0) < 0.80
        ORDER BY 1 DESC
        LIMIT 1
    """)).fetchone()

    alert_type = "webhook_drop_detected"
    # Check active OR recently-resolved alerts within the 2-hour lookback window.
    # Historical buckets never improve once the window passes, so resolving an alert
    # and having it re-fire on the next cycle is noise, not signal.
    existing = session.execute(
        text("""
            SELECT id, created_at, status FROM alert_events
            WHERE alert_type = :alert_type
              AND created_at >= NOW() - INTERVAL '2 hours'
            ORDER BY created_at DESC LIMIT 1
        """),
        {"alert_type": alert_type},
    ).fetchone()

    if row:
        bucket_str = str(row[0])
        missing    = int(row[3])
        pct        = int(row[4]) if row[4] is not None else 0
        msg = (
            f"Webhook drop: {missing} missing in bucket {bucket_str} "
            f"({pct}% delivery rate)"
        )
        if existing:
            if existing[2] == "active":
                session.execute(
                    text("UPDATE alert_events SET last_seen_at = :now WHERE id = :id"),
                    {"now": now, "id": existing[0]},
                )
            # Suppress re-fire whether active or recently resolved —
            # the same historical bucket triggered this alert already.
            return
        alert_id = str(uuid.uuid4())
        row_obj = AlertEvent(
            id=alert_id,
            alert_type=alert_type,
            severity="warning",
            status="active",
            current_value=float(missing),
            threshold=None,
            message=msg,
            last_seen_at=now,
            created_at=now,
        )
        session.add(row_obj)
        session.flush()
        if not _ALERT_EMAIL_DELAY_SECONDS.get(alert_type):
            _send_alert_email(
                settings=settings, alert_id=alert_id, alert_type=alert_type,
                severity="warning", message=msg, now=now,
            )
            session.execute(
                text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
                {"now": now, "id": alert_id},
            )
        # else: email_sent_at stays NULL — _send_pending_delayed_alert_emails
        # sends it later only if the condition is still active past the delay.
    else:
        # No resolution email — see _evaluate_single_alert's matching comment.
        if existing:
            session.execute(
                text("""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = :now
                    WHERE alert_type = :alert_type AND status = 'active'
                """),
                {"now": now, "alert_type": alert_type},
            )


# ── New-exception alert (spec/30) ───────────────────────────────────────────

# Cap the number of individual exception emails sent per 60s metrics cycle.
# A real outage can open many exceptions at once (e.g. a downstream API
# going down); without a cap, "one email per exception" becomes a mail-bomb
# on top of the outage. exception_spike (the aggregate threshold alert)
# still fires immediately regardless of this cap. Uncaught exceptions catch
# up over the next few cycles.
_NEW_EXCEPTION_BATCH_LIMIT = 20


def _evaluate_new_exceptions(session: Session, settings: Any, now: datetime) -> None:
    """
    Email once for every newly-opened exceptions row (spec/30), any
    type/severity. Dedup uses alert_events as a per-exception notified-ledger
    (alert_type=f"exception_notified:{exception_id}") rather than a schema
    change to `exceptions` — same trick as _evaluate_sales_queue_urgent.

    First-ever run seeds the ledger for every currently-open exception
    without emailing (avoids a backlog flood the first time this ships or
    redeploys after downtime) — detected by the ledger being completely
    empty.
    """
    from app.models.alert_event import AlertEvent

    is_first_run = session.execute(text("""
        SELECT 1 FROM alert_events WHERE alert_type LIKE 'exception_notified:%' LIMIT 1
    """)).fetchone() is None

    rows = session.execute(text("""
        SELECT e.id, e.type, e.severity, e.entity_type, e.entity_id, e.context_json, e.created_at
        FROM exceptions e
        WHERE e.status = 'open'
          AND NOT EXISTS (
              SELECT 1 FROM alert_events ae
              WHERE ae.alert_type = 'exception_notified:' || e.id
          )
        ORDER BY e.created_at ASC
        LIMIT :limit
    """), {"limit": _NEW_EXCEPTION_BATCH_LIMIT}).fetchall()

    for row in rows:
        exc_id, exc_type, severity, entity_type, entity_id, context_json, created_at = row
        ledger_id = str(uuid.uuid4())
        session.add(AlertEvent(
            id=ledger_id,
            alert_type=f"exception_notified:{exc_id}",
            severity=severity or "warning",
            status="resolved",  # tombstone — nothing to resolve later, just a sent-marker
            message=f"exception {exc_id} ({exc_type})",
            last_seen_at=now,
            created_at=now,
            resolved_at=now,
        ))
        session.flush()

        if is_first_run:
            continue  # seed the ledger silently, no email for pre-existing backlog

        detail = ""
        if context_json:
            try:
                parts = [f"{k}={v}" for k, v in list(context_json.items())[:5]]
                detail = " | " + ", ".join(parts)
            except Exception:
                pass
        msg = f"New exception: type={exc_type} entity={entity_type}:{entity_id}{detail}"
        _send_alert_email(
            settings=settings, alert_id=exc_id, alert_type="new_exception",
            severity=severity or "warning", message=msg, now=now,
        )


# ── Sales-queue urgent-notice alert (spec/30) ───────────────────────────────

# Mirrors dashboard_metrics._INTENT_SCORES' always-urgent set (score >= 80
# even with zero recency bonus) — the common case for "this needs a human
# now." Deliberately narrower than the full recency-boosted definition
# dashboard_metrics._compute_sales_priority computes (e.g. a failed_booking
# call < 30 min old can also score >= 80); recomputing that here would mean
# duplicating the recency math in SQL for an edge case. Revisit if that gap
# turns out to matter in practice.
_SALES_QUEUE_URGENT_INTENTS = frozenset({
    "enrolled", "callback_request", "callback_with_time",
    "re_engaged", "human_transfer_request",
})
_SALES_QUEUE_URGENT_INTENTS_SQL = ",".join(f"'{i}'" for i in sorted(_SALES_QUEUE_URGENT_INTENTS))
_SALES_QUEUE_SCAN_WINDOW_DAYS = 1

# Per Kes (2026-09-11): Rose owns admissions questions, Taiwo owns
# payment/IPBC questions. Free keyword match against the transcript — not a
# controlled vocabulary, easy to extend if a real transcript gets misrouted.
_ADMISSIONS_KEYWORDS = (
    "admission", "admissions", "enroll", "enrollment", "apply", "application",
    "program", "cohort", "curriculum", "start date", "requirement", "prerequisite",
    "scholarship", "orientation", "class schedule",
)
_PAYMENT_KEYWORDS = (
    "payment", "ipbc", "invoice", "bill", "billing", "refund", "installment",
    "tuition", "balance", "financing", "loan", "deposit", "autopay", "auto pay",
    "credit card",
)


def _extract_caller_turns(transcript: str | None) -> str:
    """
    Isolate the caller's own lines ("human:"-prefixed) from a Synthflow
    transcript, for topic/name routing decisions.

    Must not classify on Cora's own scripted lines — the bot's canned
    greeting says "no payment, no pressure" (describing the free preview)
    on nearly every call, both campaigns, which would otherwise false-match
    the payment keyword almost universally. Falls back to the raw
    transcript if no "human:" lines are found (unexpected format — fail
    open rather than classify on nothing).
    """
    if not transcript:
        return ""
    human_lines = [
        line.split(":", 1)[1].strip()
        for line in transcript.splitlines()
        if line.strip().lower().startswith("human:")
    ]
    return " ".join(human_lines) if human_lines else transcript


def _route_sales_queue_recipients(settings: Any, transcript: str | None) -> list[str]:
    """
    Decide who gets the urgent-sales-queue email for one lead.

    Priority: an explicit name mention ("can I talk to Rose") always wins,
    per Kes's instruction — routes to that person regardless of topic.
    Otherwise route by topic keyword (admissions -> Rose, payment/IPBC ->
    Taiwo; both keyword sets hit in the caller's own words -> both, a
    genuine dual-topic call). If no signal at all, default to Rose only
    (revised 2026-09-11, per Kes: strictly either/or, not both) — Sales
    Queue leads are inherently admissions-track calls (New/Cold Lead
    campaigns); Taiwo's payment/IPBC domain is the narrower exception that
    only applies when the caller's words actually raise it.

    Classifies on the caller's words only (_extract_caller_turns) — not
    Cora's own scripted lines, which mention "Admissions" and "payment"
    (the free-preview pitch) on nearly every call regardless of topic. See
    that function's docstring.
    """
    text_lower = _extract_caller_turns(transcript).lower()

    named = []
    if re.search(r"\brose\b", text_lower):
        named.append(settings.alert_email_rose)
    if re.search(r"\btaiwo\b", text_lower):
        named.append(settings.alert_email_taiwo)
    if named:
        return named

    recipients = []
    if any(kw in text_lower for kw in _ADMISSIONS_KEYWORDS):
        recipients.append(settings.alert_email_rose)
    if any(kw in text_lower for kw in _PAYMENT_KEYWORDS):
        recipients.append(settings.alert_email_taiwo)
    if recipients:
        return recipients

    return [settings.alert_email_rose]


def _sales_queue_routing_reason(transcript: str | None) -> str:
    """Human-readable version of _route_sales_queue_recipients' logic, for
    the "why this reached you" line in the friendly email. Kept as a
    separate small function rather than changing that one's return shape,
    to avoid disturbing its existing callers/tests. Classifies on the
    caller's words only — see _extract_caller_turns."""
    text_lower = _extract_caller_turns(transcript).lower()

    named = []
    if re.search(r"\brose\b", text_lower):
        named.append("Rose")
    if re.search(r"\btaiwo\b", text_lower):
        named.append("Taiwo")
    if named:
        return f"the caller asked for {' and '.join(named)} by name"

    admissions_hit = any(kw in text_lower for kw in _ADMISSIONS_KEYWORDS)
    payment_hit = any(kw in text_lower for kw in _PAYMENT_KEYWORDS)
    if admissions_hit and payment_hit:
        return "the call touched both admissions and payment/IPBC topics"
    if admissions_hit:
        return "the call sounded admissions-related"
    if payment_hit:
        return "the call sounded payment/IPBC-related"
    return "the topic wasn't clear from what the caller said, defaulting to admissions"


def _evaluate_sales_queue_urgent(session: Session, settings: Any, now: datetime) -> None:
    """
    Email Rose and/or Taiwo exactly once per lead when it enters "urgent"
    sales priority (spec/30) — a permanent, fire-once notification, not
    tracked against resolution over time. Per Kes, 2026-09-11: whether the
    lead has since been addressed is tracked by a separate mechanism (the
    Sales Queue itself); this alert doesn't re-check that on every cycle to
    decide whether a *resend* is allowed.

    It does still check sales_outcome once, at the moment a lead is first
    considered (the `ls.sales_outcome IS NULL` filter below) — a lead a rep
    already resolved before this cycle ran is skipped, never emailed at
    all. That's a one-time gate at detection time, not ongoing tracking:
    once sent, a contact never re-alerts through this path again regardless
    of what happens to sales_outcome afterward, and a lead resolved after
    the email already went out doesn't get walked back either.

    If Cora already has a pending launch_outbound_call job for this contact
    (e.g. callback_with_time extracted a specific promised time, or any of
    the other urgent intents fell back to a scheduled retry), its run_at is
    included in the message so Rose/Taiwo know a callback is already booked
    and when — not just that the lead is urgent. This only covers callbacks
    Cora itself scheduled; an appointment booked directly in GHL's own
    calendar (not through a Cora phone call) is invisible to Cora — see
    spec/22's "out of scope" note on GHL-native appointment ingestion.

    Dedup uses alert_events with alert_type=f"sales_queue_urgent:{contact_id}"
    — any row at all (regardless of status) means already notified; that
    contact never re-alerts through this path again.

    Gated by settings.alert_sales_queue_enabled (default False) — this is
    the one alert type that emails people other than Kes.
    """
    if not settings.alert_sales_queue_enabled:
        return

    from app.models.alert_event import AlertEvent

    window_start = now - timedelta(days=_SALES_QUEUE_SCAN_WINDOW_DAYS)
    rows = session.execute(text(f"""
        SELECT DISTINCT ON (ce.contact_id)
            ce.contact_id, ce.detected_intent, ce.transcript,
            COALESCE(ce.start_time_utc, ce.created_at) AS call_time,
            sj.run_at AS next_callback_at,
            sj.payload_json->>'intent_reason' AS next_callback_reason,
            COALESCE(ls.normalized_phone, ce.contact_id) AS phone,
            NULLIF(TRIM(ce.raw_payload_json->>'Name'), '') AS lead_name
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        LEFT JOIN LATERAL (
            SELECT run_at, payload_json
            FROM scheduled_jobs
            WHERE job_type = 'launch_outbound_call'
              AND status IN ('pending', 'claimed', 'running')
              AND payload_json->>'contact_id' = ce.contact_id
            ORDER BY run_at ASC
            LIMIT 1
        ) sj ON true
        WHERE ce.detected_intent IN ({_SALES_QUEUE_URGENT_INTENTS_SQL})
          AND COALESCE(ce.duration_seconds, 0) >= 30
          AND ce.transcript IS NOT NULL AND ce.transcript != ''
          AND COALESCE(ce.start_time_utc, ce.created_at) >= :window_start
          AND ls.sales_outcome IS NULL
        ORDER BY ce.contact_id, COALESCE(ce.start_time_utc, ce.created_at) DESC
    """), {"window_start": window_start}).fetchall()

    for row in rows:
        (contact_id, intent, transcript, call_time,
         next_callback_at, next_callback_reason, phone, lead_name) = row
        alert_type = f"sales_queue_urgent:{contact_id}"

        already_sent = session.execute(
            text("SELECT 1 FROM alert_events WHERE alert_type = :t LIMIT 1"),
            {"t": alert_type},
        ).fetchone()
        if already_sent:
            continue  # fire-once — already notified for this lead, ever

        recipients = _route_sales_queue_recipients(settings, transcript)
        routing_reason = _sales_queue_routing_reason(transcript)
        call_time_str = call_time.isoformat() if hasattr(call_time, "isoformat") else str(call_time)

        callback_note = ""
        if next_callback_at is not None:
            cb_str = (
                next_callback_at.isoformat() if hasattr(next_callback_at, "isoformat")
                else str(next_callback_at)
            )
            reason_str = f" (reason: {next_callback_reason})" if next_callback_reason else ""
            callback_note = (
                f"Cora already has a follow-up call scheduled for {cb_str} UTC{reason_str} "
                f"— no need to book a separate one."
            )

        # Internal audit-trail message (alert_events row), distinct from the
        # friendly email body sent to Rose/Taiwo below.
        msg = (
            f"Urgent sales-queue lead — contact_id={contact_id} intent={intent} "
            f"call_time={call_time_str}. {callback_note} Routed to: {', '.join(recipients)} "
            f"({routing_reason})."
        ).strip()
        # status='resolved' from the start — this is a fire-once tombstone
        # (existence alone blocks a resend), not an active/resolved
        # lifecycle like the other alert types track.
        alert_id = str(uuid.uuid4())
        session.add(AlertEvent(
            id=alert_id, alert_type=alert_type, severity="warning", status="resolved",
            message=msg, last_seen_at=now, created_at=now, resolved_at=now,
        ))
        session.flush()

        transcript_excerpt = (transcript or "").strip()[:400]
        if len(transcript or "") > 400:
            transcript_excerpt += "..."

        _send_sales_queue_urgent_email(
            settings=settings, to_addrs=recipients, cc_addrs=None,
            lead_name=lead_name or "", phone=phone, intent=intent,
            call_time_str=call_time_str, transcript_excerpt=transcript_excerpt,
            routing_reason=routing_reason, callback_note=callback_note,
        )
        session.execute(
            text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
            {"now": now, "id": alert_id},
        )


# ── SMTP email sender ─────────────────────────────────────────────────────────

def _smtp_send(
    settings: Any,
    to_addrs: list[str],
    subject: str,
    body: str,
    log_label: str,
    cc_addrs: list[str] | None = None,
) -> None:
    """
    Low-level SMTP send, shared by every alert email (generic system alerts
    and the friendly sales-queue template). Non-fatal: exceptions are
    logged, never raised. Does nothing if smtp_enabled is False or
    from/recipients are missing.
    """
    if not settings.smtp_enabled:
        logger.debug("alerting: SMTP disabled — skipping email for %s", log_label)
        return

    from_addr = settings.alert_email_from
    to_addrs = [a.strip() for a in to_addrs if a and a.strip()]
    cc_addrs = [a.strip() for a in (cc_addrs or []) if a and a.strip()]
    if not from_addr or not to_addrs:
        logger.warning(
            "alerting: ALERT_EMAIL_FROM or recipient list not configured; skipping email for %s",
            log_label,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg.attach(MIMEText(body, "plain"))

    # RFC 5321 envelope recipients must include Cc addresses explicitly —
    # the Cc header alone only controls what recipients *see* on the mail,
    # it doesn't cause SMTP to actually deliver to them.
    envelope_addrs = to_addrs + [a for a in cc_addrs if a not in to_addrs]

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(from_addr, envelope_addrs, msg.as_string())
        logger.info(
            "alerting: email sent for %s (to=%d, cc=%d recipient(s))",
            log_label, len(to_addrs), len(cc_addrs),
        )
    except Exception as exc:
        logger.error("alerting: SMTP send failed for %s: %s", log_label, exc)


def _send_alert_email(
    settings: Any,
    alert_id: str,
    alert_type: str,
    severity: str,
    message: str,
    now: datetime,
    to_override: list[str] | None = None,
) -> None:
    """
    Send a generic system-alert email (queue lag, exception spike,
    new_exception, etc.) via SMTP. Only ever sent for a new/active alert —
    no resolution email (removed 2026-09-11 per Kes: the active alert
    already told them about the problem, a follow-up "it's fine now" email
    is noise, not signal).

    to_override: explicit recipient list, bypassing settings.alert_email_to.
    """
    if to_override is not None:
        to_addrs = to_override
    else:
        # alert_email_to is documented as comma-separated for multiple
        # recipients — must split before handing to smtplib, which treats a
        # single un-split string as one (invalid) RCPT TO address.
        to_addrs = (settings.alert_email_to or "").split(",")

    subject = f"[{severity.upper()}] Cora Alert: {alert_type}"

    body_lines = [
        f"Alert ID: {alert_id}",
        f"Type: {alert_type}",
        f"Severity: {severity}",
        f"Message: {message}",
        f"Time: {now.isoformat()}",
        "",
        "This is an automated alert from the Cora Recap Engine.",
        "Log in to the dashboard to view details and take action.",
    ]
    body = "\n".join(body_lines)

    _smtp_send(settings, to_addrs, subject, body, log_label=alert_type)


def _send_sales_queue_urgent_email(
    settings: Any,
    *,
    to_addrs: list[str],
    cc_addrs: list[str] | None,
    lead_name: str,
    phone: str,
    intent: str,
    call_time_str: str,
    transcript_excerpt: str,
    routing_reason: str,
    callback_note: str,
) -> None:
    """
    Friendly, human-facing email for Rose/Taiwo (not Cora operators) — no
    "Alert ID" / "log in to the dashboard" system-alert framing. Per Kes,
    2026-09-11. No dashboard link — per Kes, 2026-09-11 follow-up: Rose and
    Taiwo look the lead up in GHL directly, not the Cora dashboard.

    cc_addrs: optional CC list. Kes asked to be CC'd (2026-09-11), then
    asked not to be (same day, minutes later) — caller currently always
    passes None. Kept as a parameter rather than removed since this is the
    kind of preference that comes back.
    """
    # Plain hyphen, not an em dash — keeps the Subject header pure ASCII so
    # it isn't RFC 2047 (quoted-printable) encoded by the email library.
    subject = f"Urgent lead needs follow-up - {lead_name or phone}"

    body_lines = [
        f"{lead_name or 'A lead'} needs a follow-up from you.",
        "",
        f"Phone: {phone}",
        f"Called: {call_time_str}",
        f"Why this reached you: {routing_reason}",
    ]
    if callback_note:
        body_lines.append(callback_note.strip())
    body_lines += [
        "",
        "What they said:",
        f'"{transcript_excerpt}"',
        "",
        f"Check this lead's GHL account for full details (look up by phone: {phone}).",
    ]
    body = "\n".join(body_lines)

    _smtp_send(settings, to_addrs, subject, body, log_label=f"sales_queue_urgent:{intent}", cc_addrs=cc_addrs)
