"""
AI cold lead tagging — filter-building + orchestration logic (spec/31).

Pure service logic, independent of the RQ/worker claim machinery, so it's
unit-testable with a mocked GHLClient and DB session (see
app/worker/jobs/ai_cold_lead_tagging_jobs.py for the worker-lifecycle
wrapper that calls run_tagging_cycle()).

Filter contract confirmed live 2026-09-18 against production (15,693 total
contacts at the time) — see directives/spec/31_ai_cold_lead_tagging.md for
the full verification record, including operators/fields that looked
plausible but were rejected live (documented there so nobody re-tries them).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def build_search_filters(settings: Any) -> list[dict]:
    """
    Build the GHL /contacts/search filter list for the 5 UI criteria.

    All 5 objects are ANDed together by GHL. The tags exclusion uses a
    single filter with an array value — confirmed live to give an identical
    result to 10 separately-ANDed not_contains filters (spec/31).
    """
    cutoff_epoch_ms = int(
        (
            datetime.now(timezone.utc)
            - timedelta(days=settings.ai_cold_lead_tagging_lookback_days)
        ).timestamp()
        * 1000
    )
    exclude_tags = [
        t.strip() for t in settings.ai_cold_lead_tagging_exclude_tags.split(",") if t.strip()
    ]
    return [
        {"field": "type", "operator": "eq", "value": "lead"},
        {"field": "dnd", "operator": "eq", "value": False},
        # A single wildcard covers both "phone not empty" and "starts with
        # +1" — a contact with an empty phone can't match "+1*" (spec/31).
        {"field": "phone", "operator": "wildcard", "value": "+1*"},
        {"field": "tags", "operator": "not_contains", "value": exclude_tags},
        {"field": "lastActivity", "operator": "range", "value": {"gte": 0, "lte": cutoff_epoch_ms}},
    ]


def fetch_candidate_contacts(client: Any, settings: Any) -> Iterator[dict]:
    """
    Page through GHL /contacts/search matches, capped by
    ai_cold_lead_tagging_batch_cap so a first live run can't attempt
    thousands of tags in one cycle.

    Pagination via each contact's own `searchAfter` cursor — confirmed live
    round-trip with zero page overlap (spec/31).
    """
    filters = build_search_filters(settings)
    page_limit = 100
    search_after: list[Any] | None = None
    cap = settings.ai_cold_lead_tagging_batch_cap
    yielded = 0

    while True:
        result = client.search_contacts(filters, page_limit=page_limit, search_after=search_after)
        contacts = result.get("contacts", [])
        if not contacts:
            return

        for contact in contacts:
            if yielded >= cap:
                logger.info(
                    "ai_cold_lead_tagging: batch cap (%d) reached — remainder deferred to next run",
                    cap,
                )
                return
            yield contact
            yielded += 1

        if len(contacts) < page_limit:
            return  # last page
        search_after = contacts[-1].get("searchAfter")
        if not search_after:
            return  # cursor missing — stop rather than loop forever


def tag_contact(client: Any, contact: dict, settings: Any, mode_flags: Any) -> tuple[bool, str | None]:
    """Tag one contact. Returns (success, error_message)."""
    from app.adapters.ghl import GHLError

    contact_id = contact.get("id")
    try:
        client.add_contact_tag(contact_id, settings.ai_cold_lead_tagging_tag, mode_flags=mode_flags)
        return True, None
    except GHLError as exc:
        return False, str(exc)


def run_tagging_cycle(session: Session, settings: Any) -> "TagAiColdLeadsRun":  # noqa: F821
    """
    Run one full tagging cycle and persist a TagAiColdLeadsRun row.

    Checks ai_cold_lead_tagging_enabled FIRST — if off, writes a
    status="skipped" row and returns without calling GHL at all, so the
    dashboard shows *why* nothing happened rather than silence.

    Per-contact failures are isolated (one bad contact doesn't abort the
    cycle) — a hard failure fetching a search page (e.g. GHL exhausting
    retries) propagates after the run row is marked "failed", so the
    worker-job wrapper's own fail_job/create_exception path still fires.
    """
    from app.adapters.ghl import GHLClient
    from app.core.mode_flags import get_mode_flags
    from app.models.tag_ai_cold_leads_run import TagAiColdLeadsRun

    mode_flags = get_mode_flags(session, settings)

    run = TagAiColdLeadsRun(
        id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
        status="running",
        dry_run=not (mode_flags.ai_cold_lead_tagging_enabled and mode_flags.ghl_writes_enabled),
    )
    session.add(run)
    session.flush()

    if not mode_flags.ai_cold_lead_tagging_enabled:
        logger.info("ai_cold_lead_tagging: disabled (ai_cold_lead_tagging_enabled=false) — skipping cycle")
        run.status = "skipped"
        run.finished_at = datetime.now(timezone.utc)
        return run

    client = GHLClient(settings=settings)
    scanned = tagged = skipped_already_tagged = failed = 0
    tag_lower = settings.ai_cold_lead_tagging_tag.lower()

    try:
        for contact in fetch_candidate_contacts(client, settings):
            scanned += 1
            existing_tags = [t.lower() for t in (contact.get("tags") or [])]
            if tag_lower in existing_tags:
                # Idempotency double-guard beyond the filter's own exclusion
                # of ai_cold_lead_tagging_tag — a contact could match this
                # search page from a stale index before GHL's tag write is
                # reflected in a subsequent search.
                skipped_already_tagged += 1
                continue
            success, error = tag_contact(client, contact, settings, mode_flags)
            if success:
                tagged += 1
            else:
                failed += 1
                logger.warning(
                    "ai_cold_lead_tagging: failed to tag contact_id=%s: %s",
                    contact.get("id"), error,
                )
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        run.contacts_scanned = scanned
        run.contacts_tagged = tagged
        run.contacts_skipped_already_tagged = skipped_already_tagged
        run.contacts_failed = failed
        client.close()

    run.status = "completed"
    return run
