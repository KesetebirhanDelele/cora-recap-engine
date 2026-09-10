"""
Automatic webhook recovery job — runs every 5 minutes.

For each launch_outbound_call job that completed but never produced a
Synthflow webhook (same dataset as the Webhook Delivery panel):

1. Search Synthflow GET /v2/calls for a matching call within 3 hours of the
   job's execution time, filtered by phone number and campaign model_id.
2. Terminal status found (completed/failed/hangup_on_voicemail/no_answer/
   left_voicemail) → recover_missed_webhook() schedules process_call_event,
   running the full AI+GHL pipeline exactly as if the webhook had arrived.
3. Non-terminal status (in_progress/ringing/etc.) → skip this cycle; the
   next run will recheck.
4. No call found → advance_stale_lead(outcome="no_answer") retries the call
   or closes the lead per tier policy.

Cap: _PER_CYCLE_CAP failures per run (oldest first).
The next 5-minute run handles any remainder.

Panel exclusion: recover_missed_webhook() writes manual_webhook_recovery and
advance_stale_lead() writes manual_advance to audit_log — both already in the
webhook failure panel's exclusion filter. No changes to dashboard_metrics.py
needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PER_CYCLE_CAP = 10
_LOOKBACK_HOURS = 3
_MIN_AGE_MINUTES = 20   # mirrors the panel — skip very-recent executions
_MAX_AGE_HOURS = 24     # same window as the panel
_STUCK_FINALIZE_HOURS = 3  # spec/29: write off a stuck call Synthflow can't resolve
_SCHEDULE_INTERVAL_SECONDS = 300  # 5 minutes
_OPERATOR_ID = "auto_webhook_recovery"

_TERMINAL_STATUSES = frozenset({
    "completed",
    "failed",
    "hangup_on_voicemail",
    "no_answer",
    "left_voicemail",
})

_NON_TERMINAL_STATUSES = frozenset({
    "in_progress",
    "in_queue",
    "ringing",
    "initiated",
    "paused",
    "checking",
    "busy",
})

# Full Synthflow model UUIDs — must match the voice agents configured in Synthflow.
_CAMPAIGN_MODEL_IDS: dict[str, str] = {
    "Cold Lead": "95fd0659-7446-423c-bc51-764c3060c90f",
    "New Lead":  "2608601d-bce6-4bb8-bc0f-f7df9dbf5971",
    "Inbound":   "f98454c1-2cd4-476c-b6f2-c5c425689e61",
}


def auto_webhook_recovery_job(job_id: str) -> None:
    """
    Entrypoint for the auto_webhook_recovery job.
    Follows the same claim/run/complete/reschedule lifecycle as collect_metrics_job.
    """
    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
    from app.worker.exceptions import create_exception

    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id=job_id, worker_id=worker_id)
        if job is None:
            logger.info("auto_webhook_recovery: could not claim job_id=%s — skipping", job_id)
            return

        mark_running(session, job)
        session.commit()

        try:
            _run_recovery_cycle(session, settings)
            complete_job(session, job)
            session.commit()
        except Exception as exc:
            logger.error("auto_webhook_recovery: cycle failed: %s", exc, exc_info=True)
            fail_job(session, job, reason=str(exc))
            create_exception(
                session,
                type="webhook_auto_recovery_failed",
                context={"job_id": job.id, "error": str(exc)},
                severity="warning",
                entity_type="system",
                entity_id="webhook_recovery",
            )
            session.commit()
        finally:
            _reschedule(session)
            session.commit()


def _run_recovery_cycle(session: Session, settings: Any) -> None:
    """Process up to _PER_CYCLE_CAP failures: recover via Synthflow or reschedule."""
    from app.services.stale_recovery import (
        StaleLeadConflict,
        advance_stale_lead,
        recover_missed_webhook,
    )

    failures = _get_pending_failures(session)
    logger.info("auto_webhook_recovery: %d failure(s) queued this cycle", len(failures))

    recovered = rescheduled = skipped = 0

    for row in failures:
        job_id     = row[0]
        contact_id = row[1]
        campaign   = row[2]
        executed_at: datetime = row[3]

        if not contact_id:
            logger.warning("auto_webhook_recovery: job %s has no contact_id — skip", job_id)
            skipped += 1
            continue

        model_id = _CAMPAIGN_MODEL_IDS.get(campaign or "")
        if not model_id:
            logger.warning(
                "auto_webhook_recovery: unrecognised campaign %r for job %s — skip",
                campaign, job_id,
            )
            skipped += 1
            continue

        call = _find_synthflow_call(settings, model_id, contact_id, executed_at)

        if call is not None:
            status = (call.get("call_status") or "").lower()

            if status in _NON_TERMINAL_STATUSES:
                logger.info(
                    "auto_webhook_recovery: call still %r for %s — defer to next cycle",
                    status, contact_id,
                )
                skipped += 1
                continue

            if status in _TERMINAL_STATUSES:
                call_id = call.get("call_id") or call.get("id") or ""
                if not call_id:
                    logger.warning(
                        "auto_webhook_recovery: terminal call has no call_id for %s — skip",
                        contact_id,
                    )
                    skipped += 1
                    continue

                try:
                    recover_missed_webhook(
                        session, contact_id, call_id, _OPERATOR_ID, settings
                    )
                    session.commit()
                    logger.info(
                        "auto_webhook_recovery: recovered | contact=%s call_id=%s status=%s",
                        contact_id, call_id, status,
                    )
                    recovered += 1
                except StaleLeadConflict as exc:
                    logger.info(
                        "auto_webhook_recovery: conflict for %s — %s (skip)", contact_id, exc
                    )
                    skipped += 1
                except Exception as exc:
                    logger.error(
                        "auto_webhook_recovery: recover_missed_webhook failed for %s: %s",
                        contact_id, exc,
                    )
                    skipped += 1
                continue

            # Unknown Synthflow status — treat as not found
            logger.info(
                "auto_webhook_recovery: unknown status %r for %s — treating as no call",
                status, contact_id,
            )

        # No call found (or unknown status) → advance as no_answer
        try:
            advance_stale_lead(session, contact_id, "no_answer", _OPERATOR_ID, settings)
            session.commit()
            logger.info(
                "auto_webhook_recovery: rescheduled (no_answer) | contact=%s", contact_id
            )
            rescheduled += 1
        except StaleLeadConflict as exc:
            logger.info(
                "auto_webhook_recovery: conflict for %s — %s (skip)", contact_id, exc
            )
            skipped += 1
        except Exception as exc:
            logger.error(
                "auto_webhook_recovery: advance_stale_lead failed for %s: %s",
                contact_id, exc,
            )
            skipped += 1

    logger.info(
        "auto_webhook_recovery: cycle done | recovered=%d rescheduled=%d skipped=%d",
        recovered, rescheduled, skipped,
    )

    _recover_stuck_calls(session, settings)


def _recover_stuck_calls(session: Session, settings: Any) -> None:
    """
    spec/29: repair calls stuck on a non-terminal webhook.

    Synthflow sometimes delivers a start-of-call webhook (Status=in-progress)
    and then never delivers the completion callback. process_call_event writes
    a call_pending warning and stops; _get_pending_failures above ignores these
    because a call_events row *does* exist.

    For each stuck outbound row older than _MIN_AGE_MINUTES, ask Synthflow for
    the real outcome and replay it. If the lead has since moved on (a newer
    terminal call_event, or a pending launch_outbound_call) the replay is
    cosmetic — row + exception repaired, no re-routing.
    """
    from sqlalchemy import select

    from app.models.call_event import CallEvent
    from app.models.scheduled_job import ScheduledJob
    from app.services.stale_recovery import (
        StaleLeadConflict,
        WebhookRecoveryError,
        recover_missed_webhook,
    )

    now = datetime.now(tz=timezone.utc)
    min_age_cutoff = now - timedelta(minutes=_MIN_AGE_MINUTES)
    window_cutoff = now - timedelta(days=7)
    # A call Synthflow can't give us a terminal outcome for (no record, or its
    # own record stuck non-terminal) is written off after this long — well past
    # any real call duration or reasonable webhook delay.
    finalize_cutoff = now - timedelta(hours=_STUCK_FINALIZE_HOURS)

    stuck = session.scalars(
        select(CallEvent)
        .where(
            CallEvent.status.in_(("in-progress", "queue")),
            CallEvent.direction == "outbound",
            CallEvent.created_at <= min_age_cutoff,
            CallEvent.created_at >= window_cutoff,
            CallEvent.contact_id.is_not(None),
        )
        .order_by(CallEvent.created_at.asc())
        .limit(_PER_CYCLE_CAP)
    ).all()

    if not stuck:
        return

    logger.info("auto_webhook_recovery: %d stuck non-terminal call(s) this cycle", len(stuck))
    repaired = cosmetic = finalized = skipped = 0

    for row in stuck:
        call_id, contact_id = row.call_id, row.contact_id
        created_at = row.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        newer_terminal = session.scalars(
            select(CallEvent.id).where(
                CallEvent.contact_id == contact_id,
                CallEvent.status.not_in(("in-progress", "queue")),
                CallEvent.created_at > row.created_at,
            ).limit(1)
        ).first()
        pending_launch = session.scalars(
            select(ScheduledJob.id).where(
                ScheduledJob.job_type == "launch_outbound_call",
                ScheduledJob.entity_id == contact_id,
                ScheduledJob.status.in_(("pending", "claimed")),
            ).limit(1)
        ).first()
        moved_on = newer_terminal is not None or pending_launch is not None

        try:
            recover_missed_webhook(
                session, contact_id, call_id, _OPERATOR_ID, settings,
                route=not moved_on,
            )
            session.commit()
            if moved_on:
                cosmetic += 1
            else:
                repaired += 1
            logger.info(
                "auto_webhook_recovery: stuck call queued for %s | contact=%s call_id=%s",
                "cosmetic repair" if moved_on else "full recovery", contact_id, call_id,
            )
        except StaleLeadConflict as exc:
            logger.info("auto_webhook_recovery: stuck call conflict %s — %s (skip)", call_id, exc)
            skipped += 1
        except WebhookRecoveryError as exc:
            session.rollback()
            if created_at < finalize_cutoff:
                _finalize_unrecoverable_stuck_call(session, call_id, str(exc))
                session.commit()
                finalized += 1
                logger.warning(
                    "auto_webhook_recovery: stuck call %s unrecoverable past %dh "
                    "(%s) — finalized as failed", call_id, _STUCK_FINALIZE_HOURS, exc,
                )
            else:
                logger.info(
                    "auto_webhook_recovery: stuck call %s not yet recoverable (%s) — retry next cycle",
                    call_id, exc,
                )
                skipped += 1
        except Exception as exc:
            logger.error("auto_webhook_recovery: stuck call recovery failed for %s: %s", call_id, exc)
            session.rollback()
            skipped += 1

    logger.info(
        "auto_webhook_recovery: stuck-call pass done | full=%d cosmetic=%d finalized=%d skipped=%d",
        repaired, cosmetic, finalized, skipped,
    )


def _finalize_unrecoverable_stuck_call(session: Session, call_id: str, reason: str) -> None:
    """A stuck call Synthflow can't give a terminal outcome for, past
    _STUCK_FINALIZE_HOURS: mark the row failed and resolve the call_pending
    exception (spec/29)."""
    from app.worker.jobs.call_processing import _resolve_call_pending

    session.execute(text("""
        UPDATE call_events
        SET status = 'failed', end_call_reason = 'recovery_unresolved'
        WHERE call_id = :call_id AND status IN ('in-progress', 'queue')
    """), {"call_id": call_id})
    _resolve_call_pending(session, call_id, "failed")


def _get_pending_failures(session: Session) -> list:
    """
    Return up to _PER_CYCLE_CAP webhook failures eligible for auto-recovery.

    Mirrors the Webhook Delivery panel exclusion logic:
    - Excludes rows that already have a call_event (webhook arrived)
    - Excludes rows with a manual_webhook_recovery or manual_advance audit entry
    - Excludes rows explicitly ignored by an operator
    Ordered oldest-first so long-stale failures are resolved first.
    """
    return session.execute(text("""
        SELECT
            sj.id                             AS job_id,
            sj.payload_json->>'contact_id'    AS contact_id,
            sj.payload_json->>'campaign_name' AS campaign_name,
            sj.updated_at                     AS executed_at
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
          AND sj.updated_at <= NOW() - INTERVAL '20 minutes'
          AND sj.updated_at >= NOW() - INTERVAL '24 hours'
          AND ce.call_id   IS NULL
          AND recovery.id  IS NULL
          AND ignored.id   IS NULL
        ORDER BY sj.updated_at ASC
        LIMIT :cap
    """), {"cap": _PER_CYCLE_CAP}).fetchall()


def _find_synthflow_call(
    settings: Any,
    model_id: str,
    contact_id: str,
    executed_at: datetime,
) -> dict | None:
    """
    Search Synthflow for a call matching contact_id within _LOOKBACK_HOURS of
    executed_at. Paginates through results (limit=20) until a match is found
    or pages are exhausted.

    Returns the call record with start_time closest to executed_at, or None.
    """
    from app.adapters.synthflow import SynthflowClient, SynthflowError

    if executed_at.tzinfo is None:
        executed_at = executed_at.replace(tzinfo=timezone.utc)

    from_ms = int(executed_at.timestamp() * 1000)
    to_ms = int((executed_at + timedelta(hours=_LOOKBACK_HOURS)).timestamp() * 1000)

    client = SynthflowClient(settings=settings)
    best: dict | None = None
    best_delta: float | None = None
    limit = 20
    offset = 0

    try:
        while True:
            calls = client.list_calls(
                model_id=model_id,
                lead_phone_number=contact_id,
                from_date_ms=from_ms,
                to_date_ms=to_ms,
                limit=limit,
                offset=offset,
            )
            if not calls:
                break

            for call in calls:
                start_raw = call.get("start_time")
                if start_raw is not None:
                    try:
                        call_time = datetime.fromtimestamp(
                            int(start_raw) / 1000, tz=timezone.utc
                        )
                        delta = abs((call_time - executed_at).total_seconds())
                        if best_delta is None or delta < best_delta:
                            best = call
                            best_delta = delta
                    except (ValueError, TypeError):
                        if best is None:
                            best = call
                elif best is None:
                    best = call

            if len(calls) < limit:
                break
            offset += limit

    except SynthflowError as exc:
        logger.error(
            "auto_webhook_recovery: Synthflow list_calls failed for %s: %s",
            contact_id, exc,
        )
        return None

    return best


def _reschedule(session: Session) -> None:
    """Schedule the next auto_webhook_recovery run in _SCHEDULE_INTERVAL_SECONDS."""
    from app.worker.scheduler import schedule_job

    run_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_SCHEDULE_INTERVAL_SECONDS)
    schedule_job(
        session=session,
        job_type="auto_webhook_recovery",
        entity_type="system",
        entity_id="webhook_recovery",
        run_at=run_at,
        payload={"_scheduled_by": "self"},
    )
    logger.debug("auto_webhook_recovery: rescheduled in %ds", _SCHEDULE_INTERVAL_SECONDS)


def start_webhook_recovery_scheduler() -> None:
    """
    Enqueue the first auto_webhook_recovery job.
    Call once from worker startup if no pending job exists.
    """
    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job

    with get_sync_session() as session:
        existing = session.execute(text("""
            SELECT id FROM scheduled_jobs
            WHERE job_type = 'auto_webhook_recovery' AND status = 'pending'
            LIMIT 1
        """)).fetchone()

        if existing:
            logger.info(
                "start_webhook_recovery_scheduler: job already pending (id=%s) — skipping",
                existing[0],
            )
            return

        schedule_job(
            session=session,
            job_type="auto_webhook_recovery",
            entity_type="system",
            entity_id="webhook_recovery",
            run_at=datetime.now(tz=timezone.utc),
            payload={"_scheduled_by": "startup"},
        )
        session.commit()
        logger.info("start_webhook_recovery_scheduler: job enqueued")
