"""
Explorer call queue — orchestration (spec/49).

Fetches the accelerator's ranked list through the feed interface, decides per
contact with the pure logic in explorer_queue_logic, and (only in "apply" mode
with the job flag on) writes tags / Score / AI Summary to GHL and records state.

Modes
  "dry"    Reads only. Never writes to GHL or to explorer_queue_contacts. Ignores
           the job flag so a dry run can be done any time. Records a run row.
  "apply"  Requires explorer_queue_tagging_enabled. Real GHL writes additionally
           require ghl_write_mode=live (otherwise the client logs shadow writes and
           the run is recorded as dry_run).

Exit rule: the job never deletes a contact and never removes ent-priority-call. A contact
leaves Rose's list because her Smart List filter excludes the ent-out- tags; after a connected
call the job ADDS ent-out-connected-call (unless Rose set ent-out-callback or an outcome tag).

No SMS / email / call sending and no LLM calls anywhere in this module.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services import explorer_queue_logic as L
from app.services.explorer_queue_feed import ExplorerFeed, FeedNotConfigured, FeedRow, build_feed

logger = logging.getLogger(__name__)

ContactLookup = Callable[[str], dict]


def _load_stage_map(settings: Any) -> dict[str, str]:
    try:
        raw = json.loads(getattr(settings, "explorer_queue_stage_map", "{}") or "{}")
    except json.JSONDecodeError:
        logger.warning("explorer_queue: explorer_queue_stage_map is not valid JSON — using empty map")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _read_contact(contact: dict, ai_campaign_field_id: str) -> tuple[frozenset[str], bool]:
    """Extract (lower-cased tags, AI Campaign == Yes) from a GHL contact record."""
    contact = contact.get("contact", contact) or {}
    tags = frozenset(str(t).lower() for t in (contact.get("tags") or []))
    ai = False
    for f in contact.get("customFields") or []:
        if f.get("id") == ai_campaign_field_id:
            v = f.get("value")
            vals = v if isinstance(v, list) else [v]
            ai = any(str(x).strip().lower() == "yes" for x in vals)
    return tags, ai


def _apply_decision(client: Any, d: L.Decision, settings: Any, mode_flags: Any) -> tuple[bool, bool, bool]:
    """Write one decision to GHL. Returns (tags_ok, score_ok, summary_ok). Raises on GHL error."""
    for tag in sorted(d.tags_to_add):
        client.add_contact_tag(d.contact_id, tag, mode_flags=mode_flags)
    for tag in sorted(d.tags_to_remove):
        client.remove_contact_tag(d.contact_id, tag, mode_flags=mode_flags)
    fields: dict[str, Any] = {}
    if d.write_score is not None:
        fields[settings.explorer_queue_score_field_id] = d.write_score
    if d.write_summary is not None:
        fields[settings.explorer_queue_ai_summary_field_id] = d.write_summary
    if fields:
        client.update_contact_fields(d.contact_id, fields, mode_flags=mode_flags)
    return True, d.write_score is not None, d.write_summary is not None


def run_explorer_queue_cycle(
    session: Session,
    settings: Any,
    *,
    mode: str = "dry",
    feed: ExplorerFeed | None = None,
    client: Any = None,
    contact_lookup: ContactLookup | None = None,
    now: datetime | None = None,
) -> "ExplorerQueueRun":  # noqa: F821
    from app.core.mode_flags import get_mode_flags
    from app.models.explorer_queue import ExplorerQueueContact, ExplorerQueueRun
    from app.models.staff_call_quality import StaffCallQuality

    if mode not in ("dry", "apply"):
        raise ValueError(f"mode must be 'dry' or 'apply', got {mode!r}")
    now = now or datetime.now(timezone.utc)
    mode_flags = get_mode_flags(session, settings)
    view = getattr(settings, "explorer_queue_view", "reach_out")

    will_write = mode == "apply" and mode_flags.explorer_queue_tagging_enabled and mode_flags.ghl_writes_enabled
    run = ExplorerQueueRun(
        id=str(uuid.uuid4()), started_at=now, status="running", dry_run=not will_write, view=view,
    )
    session.add(run)
    session.flush()

    if mode == "apply" and not mode_flags.explorer_queue_tagging_enabled:
        run.status = "skipped"
        run.error_message = "explorer_queue_tagging_enabled is off"
        run.finished_at = datetime.now(timezone.utc)
        return run

    try:
        feed = feed or build_feed(settings)
    except FeedNotConfigured as exc:
        run.status = "skipped"
        run.error_message = str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc)
        return run

    own_client = client is None
    if own_client:
        from app.adapters.ghl import GHLClient
        client = GHLClient(settings=settings)
    lookup = contact_lookup or client.get_contact

    stage_map = _load_stage_map(settings)
    allowed_location = settings.ghl_location_id
    decisions: list[L.Decision] = []
    failed = 0
    try:
        result = feed.fetch(view, limit=settings.explorer_queue_batch_cap)
        parsed = {r.ghl_url: L.parse_ghl_url(r.ghl_url) for r in result.rows}
        ids = [p[1] for p in parsed.values() if p]

        states: dict[str, ExplorerQueueContact] = {}
        last_connected: dict[str, datetime] = {}
        if ids:
            for st in session.execute(
                select(ExplorerQueueContact).where(ExplorerQueueContact.ghl_contact_id.in_(ids))
            ).scalars():
                states[st.ghl_contact_id] = st
        # Out-of-feed contacts still holding the queue tag must also drain.
        open_states = list(session.execute(
            select(ExplorerQueueContact).where(ExplorerQueueContact.drained_at.is_(None))
        ).scalars())
        for st in open_states:
            states.setdefault(st.ghl_contact_id, st)
        all_ids = list(states) + [i for i in ids if i not in states]
        if all_ids:
            for cid, ts in session.execute(
                select(StaffCallQuality.ghl_contact_id, func.max(StaffCallQuality.call_time))
                .where(StaffCallQuality.ghl_contact_id.in_(all_ids), StaffCallQuality.call_connected.is_(True))
                .group_by(StaffCallQuality.ghl_contact_id)
            ):
                last_connected[cid] = ts

        def to_logic_state(st: Any) -> L.ContactState | None:
            return None if st is None else L.ContactState(
                tagged_at=st.tagged_at, drained_at=st.drained_at, exit_reason=st.exit_reason,
                last_score=st.last_score, last_summary_hash=st.last_summary_hash,
            )

        # Only trust "vanished from the feed" when the feed looks complete: non-empty and
        # below the row cap (a capped or empty answer must never mass-exit the queue).
        feed_complete = 0 < len(result.rows) < settings.explorer_queue_batch_cap
        feed_ids: set[str] = set()
        pairs: list[tuple[FeedRow | None, L.Decision]] = []
        for row in result.rows:
            p = parsed[row.ghl_url]
            tags: frozenset[str] | None = None
            ai = False
            if p and p[0] == allowed_location:
                feed_ids.add(p[1])
                try:
                    tags, ai = _read_contact(lookup(p[1]), settings.explorer_queue_ai_campaign_field_id)
                except Exception as exc:  # isolate: one unreadable contact must not stop the batch
                    failed += 1
                    logger.warning("explorer_queue: could not read contact_id=%s: %s", p[1], exc)
                    continue
            st = states.get(p[1]) if p else None
            d = L.decide_row(
                row, allowed_location_id=allowed_location, ai_campaign=ai, current_tags=tags,
                state=to_logic_state(st), last_connected_call_time=last_connected.get(p[1]) if p else None,
                stage_map=stage_map,
            )
            pairs.append((row, d))
            decisions.append(d)

        # Contacts we tagged earlier that are no longer in the feed can still exit
        # (connected call, or Rose's outcome tag), so read their tags and decide.
        for cid, st in list(states.items()):
            if cid in feed_ids or st.drained_at is not None or st.ghl_location_id != allowed_location:
                continue
            try:
                tags, _ai = _read_contact(lookup(cid), settings.explorer_queue_ai_campaign_field_id)
            except Exception as exc:
                failed += 1
                logger.warning("explorer_queue: could not read contact_id=%s: %s", cid, exc)
                continue
            exit_ = L.decide_exit(to_logic_state(st), tags, last_connected.get(cid), left_feed=feed_complete)
            callback = L.TAG_OUT_CALLBACK in tags
            if exit_ is not None:
                reason, add_tags = exit_
                d = L.Decision(
                    L.EXIT, contact_id=cid, location_id=st.ghl_location_id, tags_to_add=add_tags,
                    exit_reason=reason, reason=f"exit: {reason} (not in feed)",
                )
            else:
                d = L.Decision(L.ALREADY, contact_id=cid, location_id=st.ghl_location_id,
                               callback_hold=callback, reason="not in feed; left alone")
            pairs.append((None, d))
            decisions.append(d)

        if will_write:
            for row, d in pairs:
                st = states.get(d.contact_id)
                # State-only change: remember whether Rose has the contact on callback hold.
                if st is not None and d.action in (L.ALREADY, L.RECONCILE) and st.callback_hold != d.callback_hold:
                    st.callback_hold = d.callback_hold
                    st.updated_at = now
                if not d.writes and d.action not in (L.TAG_NEW, L.EXIT):
                    continue
                try:
                    _apply_decision(client, d, settings, mode_flags)
                except Exception as exc:
                    failed += 1
                    logger.warning("explorer_queue: write failed contact_id=%s: %s", d.contact_id, exc)
                    continue
                if d.action == L.EXIT:
                    if st is None:  # Rose's outcome tag on a contact we never tagged
                        st = ExplorerQueueContact(
                            ghl_contact_id=d.contact_id, ghl_location_id=d.location_id,
                            enrollment_id=row.enrollment_id if row else None, tagged_at=now,
                        )
                        session.add(st)
                        states[d.contact_id] = st
                    st.drained_at = now
                    st.exit_reason = d.exit_reason
                    st.callback_hold = False
                    st.updated_at = now
                elif d.action in (L.TAG_NEW, L.RECONCILE) and row is not None:
                    if st is None:
                        st = ExplorerQueueContact(
                            ghl_contact_id=d.contact_id, ghl_location_id=d.location_id,
                            enrollment_id=row.enrollment_id, tagged_at=now,
                        )
                        session.add(st)
                        states[d.contact_id] = st
                    st.callback_hold = d.callback_hold
                    st.last_score = row.score if d.write_score is not None else st.last_score
                    if d.write_summary is not None:
                        st.last_summary_hash = L.summary_hash(d.write_summary)
                    st.last_stage_tag = next((t for t in d.tags_to_add if t in L.STAGE_TAGS), st.last_stage_tag)
                    st.last_score_band_tag = next((t for t in d.tags_to_add if t in L.BAND_TAGS), st.last_score_band_tag)
                    st.updated_at = now

        counts = L.summarize(decisions)
        run.rows_fetched = len(result.rows)
        run.contacts_tagged = counts.tagged
        run.contacts_already_tagged = counts.already_tagged
        run.contacts_untagged = counts.untagged
        run.skipped_no_ghl_url = counts.skipped_no_ghl_url
        run.skipped_ai_campaign = counts.skipped_ai_campaign
        run.skipped_no_access = counts.skipped_no_access
        run.contacts_failed = failed
        run.status = "completed"
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        if own_client:
            client.close()
    return run
