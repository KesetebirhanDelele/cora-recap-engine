"""
Outbound call launch job — runs on the `default` RQ queue.

Executes the Synthflow Make Call workflow for a scheduled outbound call.
This job is enqueued by POST /v1/test/calls/outbound and by the production
outbound call scheduler.

Job lifecycle:
  1. Claim the ScheduledJob row
  2. Read phone, lead_name, campaign_name from payload
  3. Check campaign active window in caller's local timezone (live mode only).
     If outside window: cancel current job, reschedule at next window-open time.
  4. Call SynthflowClient.launch_new_lead_call()
  5. Log the result and complete the job
  6. On failure: create exception record, fail the job

The Synthflow call completion arrives separately via:
  POST /v1/webhooks/calls (completed-call webhook)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running, release_job_to_pending
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)

_CALL_BATCH_SIZE = 4     # calls per slot
_CALL_SLOT_SECONDS = 300  # 5-minute slot window
# Spacing between individual calls within a slot: 300 / 4 = 75 s.
# Calls in the same batch fire at +0s, +75s, +150s, +225s — never simultaneously.
_CALL_WITHIN_SLOT_SPACING = _CALL_SLOT_SECONDS // _CALL_BATCH_SIZE  # 75 s

# Fixed global anchor for bucket alignment — shared with voicemail_jobs.py's
# retry scheduling so every launch_outbound_call job (fresh calls, deferred
# calls, voicemail-tier retries, and lead-requested exact-time callbacks)
# lands on the same grid and can be checked for collisions against each
# other, regardless of which code path scheduled it.
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)
# Postgres advisory-lock key that serializes launch_outbound_call bucket
# allocation across concurrent schedulers (spec/29). Arbitrary fixed constant.
_BUCKET_ALLOC_LOCK_KEY = 2909_0001
# Bound the free-bucket search to the same 4-hour horizon the old count-based
# version used, so behavior doesn't silently search forever.
_MAX_BUCKET_SEARCH = int(timedelta(hours=4).total_seconds() // _CALL_WITHIN_SLOT_SPACING)  # 192


def _bucket_index(dt: datetime) -> int:
    """
    Epoch-relative 75s bucket index for dt.

    SQLite (used in unit tests) returns naive datetimes for DateTime(timezone=True)
    columns even though they were stored as UTC-aware — normalize before
    subtracting so this doesn't crash. Every datetime in this system is UTC by
    convention, so a naive value is always assumed to already be UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - _EPOCH).total_seconds() // _CALL_WITHIN_SLOT_SPACING)


def _bucket_start(index: int) -> datetime:
    """Wall-clock start time of the given epoch-relative bucket."""
    return _EPOCH + timedelta(seconds=index * _CALL_WITHIN_SLOT_SPACING)


# Priority campaign for the bump logic below (spec/21). New Lead is rare
# (<2% of volume) but time-sensitive; Cold Lead is the overwhelming majority
# and not time-sensitive, so New Lead may displace a pending Cold Lead job
# from a contested slot. Comparison is case-insensitive, matching every
# other campaign_name check in this codebase (e.g. the pause-flag checks
# just below in launch_outbound_call_job).
_PRIORITY_CAMPAIGN = "new lead"


def _bump_lower_priority_job(session, job, *, start_bucket: int, end_bucket: int, occupied: dict) -> None:
    """
    Move a displaced pending job to the next free bucket.

    Single bump only (spec/21 — Kes's explicit decision, given Cold Lead's
    volume share makes cascading/starvation risk from this negligible in
    practice): this never recurses to bump whatever bucket it lands on —
    it always searches forward for a bucket that is *actually free*, the
    same search every other job in this module uses, so a bump can never
    create a new collision as a side effect of resolving one.

    Uses a version-checked UPDATE (claim.py's optimistic-concurrency
    pattern) rather than a raw update, so this is safe if a worker claims
    `job` between this function reading it and writing the new run_at — in
    that race, rowcount is 0 and the bump is simply skipped (the job is no
    longer pending, so it no longer needs to move).
    """
    from sqlalchemy import update

    from app.models.scheduled_job import ScheduledJob

    for bucket in range(start_bucket, end_bucket):
        if bucket in occupied:
            continue
        new_run_at = _bucket_start(bucket)
        result = session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job.id, ScheduledJob.version == job.version)
            .values(run_at=new_run_at, version=job.version + 1, updated_at=datetime.now(tz=timezone.utc))
        )
        if result.rowcount == 0:
            logger.info(
                "_bump_lower_priority_job: version conflict on job %s — already claimed "
                "elsewhere, no longer needs bumping",
                job.id,
            )
            return
        logger.info(
            "_bump_lower_priority_job: displaced job %s (campaign=%r) to %s to make room "
            "for a priority (%s) job",
            job.id, (job.payload_json or {}).get("campaign_name"),
            new_run_at.isoformat(), _PRIORITY_CAMPAIGN,
        )
        return

    logger.warning(
        "_bump_lower_priority_job: no free bucket in search range for job %s — leaving it in place",
        job.id,
    )


def _compute_window_run_at(session, window_start: datetime, *, campaign_name: str | None = None) -> datetime:
    """
    Find the next free 75-second bucket at or after window_start.

    Buckets are aligned to a fixed global epoch (_EPOCH) rather than to
    window_start itself, so every call — however it was scheduled — competes
    for the same grid. A bucket counts as occupied if any pending/claimed
    launch_outbound_call job's actual run_at falls inside it. This includes
    lead-requested exact-time callbacks (app/core/intent_actions.py), whose
    run_at is set directly to the lead's requested time and is never itself
    moved by this function — it simply reserves whichever bucket it happens
    to land in, and calls computed here route around it. At most one call is
    placed per bucket, preserving the "4 calls per 5-minute window, 75s
    apart" pacing while actually preventing collisions instead of just
    approximating spacing via a count (the previous implementation counted
    pending jobs and did arithmetic assuming they were all placed on this
    same grid, which broke silently the moment an arbitrary-timestamp
    callback entered the same pool).

    campaign_name (spec/21): when this normalizes to "new lead", the search
    is priority-aware — if the earliest candidate bucket is held by a
    *pending* job from a lower-priority campaign, that job is bumped (see
    _bump_lower_priority_job) and this bucket is returned immediately.
    Claimed/running jobs and jobs already at New Lead priority are never
    bumped — the search just continues forward past them, identical to the
    non-priority path. Omitting campaign_name (the default, None) preserves
    the exact prior behavior with no priority awareness at all.

    Searches up to 4 hours ahead (_MAX_BUCKET_SEARCH buckets). If that
    entire range is already fully occupied — implausible under any
    realistic load, it would require ~192 simultaneous pending calls — falls
    back to the bucket immediately after the search range and logs a
    warning rather than looping indefinitely.
    """
    from sqlalchemy import select, text

    from app.models.scheduled_job import ScheduledJob

    # spec/29: serialize bucket allocation. Without this, concurrent schedulers
    # (a burst of GHL New/Cold Lead webhooks, each in its own transaction) each
    # read the same "free bucket" snapshot and all write to it — observed
    # 2026-09-10 as 14 launch jobs in a single 5-minute window against a cap of
    # 4. The xact lock is held to commit/rollback, covering the read, the
    # priority-bump decision, and the caller's insert. Postgres only; SQLite
    # tests are single-threaded so the ladder assertions still hold unlocked.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BUCKET_ALLOC_LOCK_KEY}
        )

    is_priority = (campaign_name or "").strip().lower() == _PRIORITY_CAMPAIGN

    # Ceiling: never return a time before window_start — and never in the past.
    # _slot_aware_run_at floors `now + delay` to the 5-minute boundary, which is
    # already in the past whenever delay is small or the scheduler is running
    # behind. Without the `now` clamp, the search starts on a past bucket whose
    # earlier jobs have since `completed` (so they no longer count as occupied),
    # and a fresh burst of quick re-schedules all pile onto that same past
    # timestamp → fire at once. Observed 2026-09-10: 6 launch jobs stacked on a
    # 4-minute-old 5-minute boundary.
    now = datetime.now(tz=timezone.utc)
    start_bucket = max(
        int(-(-(window_start - _EPOCH).total_seconds() // _CALL_WITHIN_SLOT_SPACING)),
        int(-(-(now - _EPOCH).total_seconds() // _CALL_WITHIN_SLOT_SPACING)),
    )
    end_bucket = start_bucket + _MAX_BUCKET_SEARCH

    existing_jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status.in_(["pending", "claimed"]),
            ScheduledJob.run_at >= _bucket_start(start_bucket),
            ScheduledJob.run_at < _bucket_start(end_bucket),
        )
    ).all()
    occupied = {_bucket_index(j.run_at): j for j in existing_jobs}

    for bucket in range(start_bucket, end_bucket):
        occupant = occupied.get(bucket)
        if occupant is None:
            return _bucket_start(bucket)

        if is_priority and occupant.status == "pending":
            occupant_campaign = (occupant.payload_json or {}).get("campaign_name", "")
            if occupant_campaign.strip().lower() != _PRIORITY_CAMPAIGN:
                _bump_lower_priority_job(
                    session, occupant,
                    start_bucket=bucket + 1, end_bucket=end_bucket, occupied=occupied,
                )
                return _bucket_start(bucket)
        # Occupied by a claimed/running job, by another New Lead job, or by
        # a pending lower-priority job when this job isn't priority itself
        # — search forward, same as the non-priority path.

    logger.warning(
        "_compute_window_run_at: no free bucket in %d-bucket search window starting %s "
        "— falling back to first bucket past the search range",
        _MAX_BUCKET_SEARCH, window_start.isoformat(),
    )
    return _bucket_start(end_bucket)


def _is_do_not_call(session, contact_id: str) -> bool:
    """True if lead_state.do_not_call is set for this contact. False if no row exists."""
    from sqlalchemy import select

    from app.models.lead_state import LeadState

    if not contact_id:
        return False
    value = session.scalars(
        select(LeadState.do_not_call).where(LeadState.contact_id == contact_id)
    ).first()
    return bool(value)


_SPAM_LIKELY_TAG = "spam likely"


def _has_spam_likely_tag(contact_id: str, settings) -> bool:
    """
    True if the contact's live GHL record carries a "spam likely" tag.

    Reads GHL directly (this signal only lives in GHL, not lead_state).
    Fails open — any lookup problem (non-GHL contact_id, GHL outage, bad
    response shape) logs a warning and returns False rather than blocking
    outbound calling on a GHL read. Matches the non-fatal GHL-read pattern
    used elsewhere (e.g. create_crm_task's contact fetch).
    """
    if not contact_id:
        return False

    stripped = contact_id.replace(" ", "").replace("-", "").replace("+", "")
    if stripped.isdigit():
        # Phone-derived contact_id, not a real GHL contact ID — nothing to look up.
        return False

    try:
        from app.adapters.ghl import GHLClient

        ghl = GHLClient(settings=settings)
        record = ghl.get_contact(contact_id)
        tags = record.get("contact", {}).get("tags", []) or []
        return any(str(t).strip().lower() == _SPAM_LIKELY_TAG for t in tags)
    except Exception as exc:
        logger.warning(
            "_has_spam_likely_tag: GHL contact fetch failed (failing open) | "
            "contact_id=%s: %s",
            contact_id, exc,
        )
        return False


def launch_outbound_call_job(job_id: str) -> None:
    """
    Worker job: invoke Synthflow Make Call workflow.

    Reads job payload, checks campaign active window in the caller's local
    timezone, then calls SynthflowClient.launch_new_lead_call(). Call
    completion arrives via webhook callback.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("launch_outbound_call_job: already claimed | job_id=%s", job_id)
            return

        # ── System pause check ────────────────────────────────────────────────
        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            logger.info(
                "launch_outbound_call_job: system paused — releasing | job_id=%s", job_id
            )
            release_job_to_pending(session, job)
            session.commit()
            return

        # ── Outbound campaign pause check ─────────────────────────────────────
        # Holds New Lead and Cold Lead jobs while Inbound continues normally.
        if flags.outbound_campaigns_paused:
            _campaign = ((job.payload_json or {}).get("campaign_name") or "").strip().lower()
            if _campaign in ("new lead", "cold lead"):
                logger.info(
                    "launch_outbound_call_job: outbound campaigns paused — releasing | "
                    "campaign=%r job_id=%s", _campaign, job_id,
                )
                release_job_to_pending(session, job, defer_seconds=60)
                session.commit()
                return

        # ── Cold Lead-only campaign pause check ───────────────────────────────
        # Holds only Cold Lead jobs while New Lead and Inbound continue normally.
        if flags.cold_lead_campaign_paused:
            _campaign = ((job.payload_json or {}).get("campaign_name") or "").strip().lower()
            if _campaign == "cold lead":
                logger.info(
                    "launch_outbound_call_job: cold lead campaign paused — releasing | "
                    "campaign=%r job_id=%s", _campaign, job_id,
                )
                release_job_to_pending(session, job, defer_seconds=60)
                session.commit()
                return

        # Load payload before mark_running so the window check can cancel
        # the job while it is still in 'claimed' status (cancel_job requires
        # pending or claimed).
        payload = job.payload_json or {}
        phone = payload.get("phone_number", "")
        lead_name = payload.get("lead_name", "")
        campaign_name = payload.get("campaign_name", "New_Lead")
        correlation_id = payload.get("correlation_id", job_id)
        contact_id = payload.get("contact_id") or phone

        # ── Blocked dial-number guard ─────────────────────────────────────────
        # Prevents dialing Synthflow agent numbers or other system phones that
        # were accidentally enrolled as leads (e.g. test contacts in GHL).
        _blocked = {
            n.strip()
            for n in (settings.blocked_dial_numbers or "").split(",")
            if n.strip()
        }
        if phone in _blocked:
            logger.error(
                "launch_outbound_call_job: phone is on blocked list — cancelling | "
                "phone=%s contact_id=%s job_id=%s",
                phone, contact_id, job_id,
            )
            from app.worker.claim import cancel_job
            cancel_job(session, job.id)
            create_exception(
                session,
                type="blocked_dial_number",
                severity="critical",
                context={"phone": phone, "contact_id": contact_id, "job_id": job_id},
                entity_type="lead",
                entity_id=contact_id,
            )
            session.commit()
            return

        # ── Do-not-call guard (belt-and-suspenders with enter_campaign) ────────
        # lead_state.do_not_call was never checked before dialing — a
        # previously-known, separately-tracked gap (see PROGRESS.md,
        # 2026-07-15 session) closed here. Catches jobs already scheduled
        # before do_not_call was set, or entered via a path other than
        # enter_campaign().
        if _is_do_not_call(session, contact_id):
            # Logged, not raised as an exception — see enter_campaign()'s
            # matching guard: a do_not_call suppression is expected list churn
            # (GHL re-triggers already-worked contacts), not a dashboard alert.
            logger.warning(
                "launch_outbound_call_job: do_not_call set — cancelling | "
                "contact_id=%s job_id=%s campaign=%s",
                contact_id, job_id, campaign_name,
            )
            from app.worker.claim import cancel_job
            cancel_job(session, job.id)
            session.commit()
            return

        # ── "spam likely" GHL tag guard ─────────────────────────────────────────
        # lead_state has no concept of GHL's own tags — this reads GHL live at
        # dial time to catch contacts GHL (or a carrier-side spam flag synced
        # into GHL) has already tagged, independent of Cora's own do_not_call
        # state. See PROGRESS.md 2026-07-17 finding #3.
        if _has_spam_likely_tag(contact_id, settings):
            logger.info(
                "launch_outbound_call_job: 'spam likely' GHL tag — cancelling | "
                "contact_id=%s job_id=%s",
                contact_id, job_id,
            )
            from app.worker.claim import cancel_job
            cancel_job(session, job.id)
            create_exception(
                session,
                type="outbound_suppressed_spam_likely_tag",
                severity="warning",
                context={"contact_id": contact_id, "job_id": job_id, "campaign_name": campaign_name},
                entity_type="lead",
                entity_id=contact_id,
            )
            session.commit()
            return

        # ── Urgent-escalation guard (belt-and-suspenders with enter_campaign) ──
        # Catches jobs that were already scheduled before an escalation
        # happened, or jobs from paths other than enter_campaign() (nurture
        # scheduler, voicemail-tier retries).
        from app.core.escalation_guard import check_urgent_unresolved
        escalation = check_urgent_unresolved(session, contact_id)
        if escalation is not None:
            logger.info(
                "launch_outbound_call_job: urgent unresolved escalation — cancelling | "
                "contact_id=%s job_id=%s detected_intent=%s call_time=%s",
                contact_id, job_id,
                escalation["detected_intent"], escalation["call_time"],
            )
            from app.worker.claim import cancel_job
            cancel_job(session, job.id)
            create_exception(
                session,
                type="outbound_suppressed_urgent_escalation",
                severity="warning",
                context={
                    "contact_id": contact_id,
                    "job_id": job_id,
                    "campaign_name": campaign_name,
                    **escalation,
                },
                entity_type="lead",
                entity_id=contact_id,
            )
            session.commit()
            return

        # ── Enrolled-student guard (belt-and-suspenders with enter_campaign) ──
        # spec/27. Catches jobs scheduled before this guard shipped, or from
        # paths other than enter_campaign() (nurture scheduler, voicemail-tier
        # retries). Reads GHL live by phone; fails open. Logged with its
        # reason, NOT raised as an exception — see enter_campaign()'s matching
        # guard: a student suppression is expected GHL list churn until the
        # GHL-side fix lands, nothing for an operator to action.
        from app.core.student_guard import check_is_student
        student = check_is_student(phone or contact_id, settings)
        if student is not None:
            logger.warning(
                "launch_outbound_call_job: contact is a student — cancelling | "
                "contact_id=%s job_id=%s reason=%s matched_tags=%s",
                contact_id, job_id,
                student["classification_source"], student["matched_tags"],
            )
            from app.worker.claim import cancel_job
            cancel_job(session, job.id)
            session.commit()
            return

        # ── Campaign active-window check (live mode only) ─────────────────────
        # Shadow mode skips this — no real outbound action is taken so there
        # is nothing to defer.
        if not flags.shadow_mode_enabled:
            from app.core.campaign_schedule import (
                get_contact_timezone,
                is_campaign_active,
                next_active_window_start,
            )
            from app.worker.claim import cancel_job
            from app.worker.scheduler import schedule_job

            now = datetime.now(tz=timezone.utc)
            contact_tz = get_contact_timezone(session, contact_id, settings)
            if not is_campaign_active(campaign_name, now, settings, contact_tz, session):
                next_open = next_active_window_start(campaign_name, now, settings, contact_tz, session)
                run_at = _compute_window_run_at(session, next_open, campaign_name=campaign_name)
                logger.info(
                    "launch_outbound_call_job: outside active window — deferring | "
                    "campaign=%s contact_tz=%s job_id=%s rescheduled_for=%s slot_offset_s=%d",
                    campaign_name, contact_tz, job_id, run_at.isoformat(),
                    int((run_at - next_open).total_seconds()),
                )
                cancel_job(session, job.id)
                schedule_job(
                    session=session,
                    job_type="launch_outbound_call",
                    entity_type=job.entity_type,
                    entity_id=job.entity_id,
                    run_at=run_at,
                    payload=payload,
                )
                return

        mark_running(session, job)

        # ── Shadow mode: log and skip the real Synthflow call ─────────────────
        if flags.shadow_mode_enabled:
            from app.worker.shadow import log_shadow_action
            log_shadow_action(
                session,
                contact_id=contact_id,
                action_type="outbound_call",
                payload={
                    "run_at": job.run_at.isoformat() if job.run_at else None,
                    "campaign": campaign_name,
                    "phone": phone,
                    "lead_name": lead_name,
                    "correlation_id": correlation_id,
                },
            )
            complete_job(session, job)
            return

        try:
            from app.adapters.synthflow import SynthflowClient

            client = SynthflowClient(settings=settings)
            result = client.launch_new_lead_call(
                phone=phone,
                lead_name=lead_name,
                campaign_name=campaign_name,
                metadata={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "source": payload.get("source", "e2e_test_harness"),
                },
            )
            logger.info(
                "launch_outbound_call_job: Synthflow call launched | "
                "correlation_id=%s job_id=%s result=%s",
                correlation_id, job_id, result,
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "launch_outbound_call_job: error | job_id=%s: %s", job_id, exc
            )
            create_exception(
                session,
                type="outbound_launch_failed",
                severity="critical",
                context={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "error": str(exc),
                },
                entity_type="lead",
                entity_id=contact_id,
            )
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise
