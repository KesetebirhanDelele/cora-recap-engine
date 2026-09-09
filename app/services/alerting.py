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

Deduplication key: (alert_type, status='active').
  - If an active row for the same alert_type was created within
    ALERT_DEDUP_WINDOW_SECONDS: update last_seen_at only; skip email.
  - If no such row: insert new active row; send email.
  - Resolution: if condition clears, resolve all active rows for that type.
"""
from __future__ import annotations

import logging
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

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
        _send_alert_email(settings=settings, alert_id=alert_id, alert_type=alert_type,
                          severity=defn["severity"], message=msg, now=now,
                          is_resolution=False)
        # Mark email_sent_at
        session.execute(
            text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
            {"now": now, "id": alert_id},
        )
    else:
        # Condition cleared — resolve any active row
        if existing:
            session.execute(
                text("""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = :now
                    WHERE alert_type = :alert_type AND status = 'active'
                """),
                {"now": now, "alert_type": alert_type},
            )
            resolve_msg = f"Alert {alert_type} resolved (value={value})"
            _send_alert_email(
                settings=settings,
                alert_id=existing[0],
                alert_type=alert_type,
                severity=defn["severity"],
                message=resolve_msg,
                now=now,
                is_resolution=True,
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
                          severity="critical", message=msg, now=now, is_resolution=False)
        session.execute(
            text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
            {"now": now, "id": alert_id},
        )
    else:
        if existing:
            session.execute(
                text("""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = :now
                    WHERE alert_type = :alert_type AND status = 'active'
                """),
                {"now": now, "alert_type": alert_type},
            )
            _send_alert_email(settings=settings, alert_id=existing[0],
                              alert_type=alert_type, severity="critical",
                              message=f"Alert {alert_type} resolved", now=now,
                              is_resolution=True)


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
                      severity=severity, message=message, now=now, is_resolution=False)
    session.execute(
        text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
        {"now": now, "id": alert_id},
    )


def _resolve_active_alert(
    session: Session,
    settings: Any,
    now: datetime,
    *,
    alert_type: str,
    severity: str,
) -> None:
    """Resolve any active row for alert_type + send one resolution email.
    No-op when nothing is active."""
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
    _send_alert_email(settings=settings, alert_id=existing[0], alert_type=alert_type,
                      severity=severity, message=f"Alert {alert_type} resolved",
                      now=now, is_resolution=True)


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
        _resolve_active_alert(session, settings, now, alert_type=alert_type, severity=severity)


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
            _resolve_active_alert(session, settings, now, alert_type=alert_type, severity=severity)
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
        _resolve_active_alert(session, settings, now, alert_type=alert_type, severity=severity)
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
        _resolve_active_alert(session, settings, now, alert_type=alert_type, severity=severity)


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
        _send_alert_email(
            settings=settings, alert_id=alert_id, alert_type=alert_type,
            severity="warning", message=msg, now=now, is_resolution=False,
        )
        session.execute(
            text("UPDATE alert_events SET email_sent_at = :now WHERE id = :id"),
            {"now": now, "id": alert_id},
        )
    else:
        if existing:
            session.execute(
                text("""
                    UPDATE alert_events
                    SET status = 'resolved', resolved_at = :now
                    WHERE alert_type = :alert_type AND status = 'active'
                """),
                {"now": now, "alert_type": alert_type},
            )
            _send_alert_email(
                settings=settings, alert_id=existing[0], alert_type=alert_type,
                severity="warning",
                message=f"Alert {alert_type} resolved",
                now=now, is_resolution=True,
            )


# ── SMTP email sender ─────────────────────────────────────────────────────────

def _send_alert_email(
    settings: Any,
    alert_id: str,
    alert_type: str,
    severity: str,
    message: str,
    now: datetime,
    is_resolution: bool,
) -> None:
    """
    Send an alert or resolution email via SMTP.
    Non-fatal: exceptions are logged; no raise.
    Does nothing if smtp_enabled is False or required settings are missing.
    """
    if not settings.smtp_enabled:
        logger.debug("alerting: SMTP disabled — skipping email for %s", alert_type)
        return

    from_addr = settings.alert_email_from
    to_addr = settings.alert_email_to
    if not from_addr or not to_addr:
        logger.warning(
            "alerting: ALERT_EMAIL_FROM or ALERT_EMAIL_TO not configured; skipping email"
        )
        return

    subject_prefix = "[RESOLVED]" if is_resolution else f"[{severity.upper()}]"
    subject = f"{subject_prefix} Cora Alert: {alert_type}"

    body_lines = [
        f"Alert ID: {alert_id}",
        f"Type: {alert_type}",
        f"Severity: {severity}",
        f"Status: {'resolved' if is_resolution else 'active'}",
        f"Message: {message}",
        f"Time: {now.isoformat()}",
        "",
        "This is an automated alert from the Cora Recap Engine.",
        "Log in to the dashboard to view details and take action.",
    ]
    body = "\n".join(body_lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info("alerting: email sent for %s (alert_id=%s)", alert_type, alert_id)
    except Exception as exc:
        logger.error("alerting: SMTP send failed for %s: %s", alert_type, exc)
