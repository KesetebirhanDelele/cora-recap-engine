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
        WHERE exception_type = 'ghl_auth_failed' AND status = 'open'
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
