"""
Explorer call queue — pure decision logic (spec/49).

No I/O, no GHL, no database, no clock: everything the decision needs is passed
in, so every acceptance rule is testable in isolation. The service layer (step 5)
gathers the inputs and applies the decisions.

This module must never import a mailer / SMS / call sender or an LLM client
(enforced by a test).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from app.services.explorer_queue_feed import FeedRow

# ── Tags ──────────────────────────────────────────────────────────────────────
TAG_PRIORITY = "ent-priority-call"      # the one tag Rose's Smart List filters on
TAG_SOURCE = "ent-src-campus-ranking"   # provenance; never removed
STAGE_TAGS = frozenset({
    "ent-stage-new-explorer", "ent-stage-activating",
    "ent-stage-active-learner", "ent-stage-engaged-learner",
})
BAND_TAGS = frozenset({"ent-score-high", "ent-score-mid", "ent-score-low"})

# Mapping primary_state -> stage tag is NOT decided (open with Ali). Until it is
# supplied, no stage tag is written and the row is counted as "stage unmapped".
StageMap = Mapping[str, str]

_GHL_URL = re.compile(
    r"^https://app\.gohighlevel\.com/v2/location/(?P<loc>[A-Za-z0-9]+)/contacts/detail/(?P<cid>[A-Za-z0-9]+)/?$"
)


def parse_ghl_url(url: str | None) -> tuple[str, str] | None:
    """Return (location_id, contact_id) or None. Never guesses."""
    if not url:
        return None
    m = _GHL_URL.match(url.strip())
    return (m["loc"], m["cid"]) if m else None


def score_band_tag(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 40:
        return "ent-score-high"
    if score >= 25:
        return "ent-score-mid"
    return "ent-score-low"


def desired_tags(row: FeedRow, stage_map: StageMap) -> set[str]:
    tags = {TAG_PRIORITY, TAG_SOURCE}
    stage = stage_map.get(row.primary_state or "")
    if stage in STAGE_TAGS:
        tags.add(stage)
    band = score_band_tag(row.score)
    if band:
        tags.add(band)
    return tags


def summary_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Per-contact state held by the job (mirrors explorer_queue_contacts) ──────
@dataclass(frozen=True)
class ContactState:
    tagged_at: datetime
    drained_at: datetime | None = None
    last_score: float | None = None
    last_summary_hash: str | None = None


# ── Decision ──────────────────────────────────────────────────────────────────
SKIP_NO_URL = "skip_no_ghl_url"
SKIP_NO_ACCESS = "skip_no_access"
SKIP_AI_CAMPAIGN = "skip_ai_campaign"
SKIP_DRAINED = "skip_drained"
TAG_NEW = "tag_new"
RECONCILE = "reconcile"
ALREADY = "already_tagged"
DRAIN = "drain"


@dataclass(frozen=True)
class Decision:
    action: str
    contact_id: str | None = None
    location_id: str | None = None
    tags_to_add: frozenset[str] = frozenset()
    tags_to_remove: frozenset[str] = frozenset()
    write_score: float | None = None
    write_summary: str | None = None
    stage_unmapped: bool = False
    reason: str = ""

    @property
    def writes(self) -> bool:
        return bool(
            self.tags_to_add or self.tags_to_remove
            or self.write_score is not None or self.write_summary is not None
        )


def decide_drain(
    state: ContactState | None, last_connected_call_time: datetime | None
) -> bool:
    """True when a connected call happened AFTER the queue tag was written.

    Non-connected calls never count (caller passes only connected call times).
    A call older than the tag write does not count either.
    """
    if state is None or state.drained_at is not None or last_connected_call_time is None:
        return False
    return last_connected_call_time > state.tagged_at


def decide_row(
    row: FeedRow,
    *,
    allowed_location_id: str,
    ai_campaign: bool,
    current_tags: frozenset[str] | None,
    state: ContactState | None,
    last_connected_call_time: datetime | None,
    stage_map: StageMap,
) -> Decision:
    """Decide what one feed row needs. Order matters; first match wins."""
    parsed = parse_ghl_url(row.ghl_url)
    if parsed is None:
        return Decision(SKIP_NO_URL, reason="ghl_url missing or malformed")
    location_id, contact_id = parsed
    ids = {"contact_id": contact_id, "location_id": location_id}
    if location_id != allowed_location_id:
        return Decision(SKIP_NO_ACCESS, reason="contact is in a location this job cannot write to", **ids)
    if ai_campaign:
        return Decision(SKIP_AI_CAMPAIGN, reason="AI Campaign = Yes stays off the queue", **ids)
    if state is not None and state.drained_at is not None:
        # Re-entry after a connected call is off until Ali says otherwise (spec/49).
        return Decision(SKIP_DRAINED, reason="already drained; no re-entry", **ids)
    if decide_drain(state, last_connected_call_time):
        return Decision(
            DRAIN, tags_to_remove=frozenset({TAG_PRIORITY}),
            reason="connected call after tag write", **ids,
        )

    current = current_tags or frozenset()
    want = desired_tags(row, stage_map)
    add = frozenset(want - current)
    # Only stage/band tags are swapped; priority + source are never removed here.
    remove = frozenset((current & (STAGE_TAGS | BAND_TAGS)) - want)
    stage_unmapped = stage_map.get(row.primary_state or "") not in STAGE_TAGS

    score = row.score if row.score is not None and (state is None or state.last_score != row.score) else None
    summary = None
    if row.ai_summary and (state is None or state.last_summary_hash != summary_hash(row.ai_summary)):
        summary = row.ai_summary  # never overwritten with blank

    if state is None:
        action = TAG_NEW
    elif add or remove or score is not None or summary is not None:
        action = RECONCILE
    else:
        action = ALREADY
    return Decision(
        action, tags_to_add=add, tags_to_remove=remove, write_score=score,
        write_summary=summary, stage_unmapped=stage_unmapped, **ids,
    )


@dataclass
class RunCounts:
    fetched: int = 0
    tagged: int = 0
    already_tagged: int = 0
    untagged: int = 0
    skipped_no_ghl_url: int = 0
    skipped_no_access: int = 0
    skipped_ai_campaign: int = 0
    skipped_drained: int = 0
    stage_unmapped: int = 0
    by_action: dict[str, int] = field(default_factory=dict)


def summarize(decisions: list[Decision]) -> RunCounts:
    c = RunCounts(fetched=len(decisions))
    for d in decisions:
        c.by_action[d.action] = c.by_action.get(d.action, 0) + 1
        if d.stage_unmapped and d.action in (TAG_NEW, RECONCILE, ALREADY):
            c.stage_unmapped += 1
    g = c.by_action.get
    c.tagged = g(TAG_NEW, 0)
    c.already_tagged = g(ALREADY, 0)
    c.untagged = g(DRAIN, 0)
    c.skipped_no_ghl_url = g(SKIP_NO_URL, 0)
    c.skipped_no_access = g(SKIP_NO_ACCESS, 0)
    c.skipped_ai_campaign = g(SKIP_AI_CAMPAIGN, 0)
    c.skipped_drained = g(SKIP_DRAINED, 0)
    return c
