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
# Ali's 3 reachable buckets (2026-11-05). The older 4 names stay in the set only so a
# contact tagged before the change has its old stage tag swapped out, not left behind.
STAGE_TAGS = frozenset({
    "ent-stage-not-started", "ent-stage-started", "ent-stage-doing-work",
    "ent-stage-new-explorer", "ent-stage-activating",
    "ent-stage-active-learner", "ent-stage-engaged-learner",
})
BAND_TAGS = frozenset({"ent-score-high", "ent-score-mid", "ent-score-low"})

# Exit tags. A contact leaves Rose's list ONLY because her Smart List filter
# ("has ent-priority-call AND none of the ent-out- tags") stops matching. The job never
# removes ent-priority-call and never deletes a contact.
TAG_OUT_CONNECTED = "ent-out-connected-call"   # added by the job after a connected call
TAG_OUT_CALLBACK = "ent-out-callback"          # applied by Rose: keep on the list
# Applied by Rose by hand. The job only reads them, to record why a contact left.
OUT_TERMINAL_TAGS = {
    "ent-out-not-interested": "not_interested",
    "ent-out-enrolled": "enrolled",
    "ent-out-wrong-number": "wrong_number",
}
REASON_CONNECTED = "connected_call"
# Added by the job when a tagged contact is no longer in the accelerator's list (enrolled
# people vanish from it). Hidden by Rose's Smart List filter like the other ent-out- tags.
TAG_OUT_LEFT_QUEUE = "ent-out-left-queue"
REASON_LEFT_FEED = "left_feed"

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


def lookup_stage(stage_map: StageMap, primary_state: str | None) -> str | None:
    """Case-insensitive lookup (the accelerator sends UPPERCASE states)."""
    if not primary_state:
        return None
    want = primary_state.strip().upper()
    for k, v in stage_map.items():
        if k.strip().upper() == want:
            return v
    return None


def desired_tags(row: FeedRow, stage_map: StageMap) -> set[str]:
    tags = {TAG_PRIORITY, TAG_SOURCE}
    stage = lookup_stage(stage_map, row.primary_state)
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
    drained_at: datetime | None = None  # when the contact exited the queue (kept name; see exit_reason)
    exit_reason: str | None = None
    last_score: float | None = None
    last_summary_hash: str | None = None


# ── Decision ──────────────────────────────────────────────────────────────────
SKIP_NO_URL = "skip_no_ghl_url"
SKIP_NO_ACCESS = "skip_no_access"
SKIP_AI_CAMPAIGN = "skip_ai_campaign"
SKIP_DRAINED = "skip_exited"
TAG_NEW = "tag_new"
RECONCILE = "reconcile"
ALREADY = "already_tagged"
EXIT = "exit"


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
    exit_reason: str | None = None
    callback_hold: bool = False

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


def decide_exit(
    state: ContactState | None,
    current_tags: frozenset[str] | None,
    last_connected_call_time: datetime | None,
    left_feed: bool = False,
) -> tuple[str, frozenset[str]] | None:
    """Return (exit_reason, tags_to_add) when a contact should leave the queue, else None.

    1. Rose applied a terminal outcome tag -> exit, reason from the tag, no GHL write
       needed (her filter already hides it).
    2. Connected call after the queue tag was written -> add ent-out-connected-call,
       unless Rose applied ent-out-callback (stays on the list).
    3. Contact vanished from a complete feed -> add ent-out-left-queue (beats callback).
    """
    tags = current_tags or frozenset()
    for tag, reason in OUT_TERMINAL_TAGS.items():
        if tag in tags:
            return reason, frozenset()
    if TAG_OUT_CALLBACK not in tags and decide_drain(state, last_connected_call_time):
        return REASON_CONNECTED, frozenset({TAG_OUT_CONNECTED}) if TAG_OUT_CONNECTED not in tags else frozenset()
    if left_feed:
        # Overrides callback: someone who vanished may have enrolled; calling them is the
        # worst outcome. Caller must only set this when the feed looks complete.
        return REASON_LEFT_FEED, (frozenset() if TAG_OUT_LEFT_QUEUE in tags else frozenset({TAG_OUT_LEFT_QUEUE}))
    return None


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
        # Already exited. Re-entry happens only if a human removes the exit tag AND we never
        # re-add it: the human override stands (spec/49).
        return Decision(SKIP_DRAINED, reason="already exited; no re-entry by the job", **ids)
    callback = TAG_OUT_CALLBACK in (current_tags or frozenset())
    exit_ = decide_exit(state, current_tags, last_connected_call_time)
    if exit_ is not None:
        reason, add_tags = exit_
        return Decision(EXIT, tags_to_add=add_tags, exit_reason=reason, reason=f"exit: {reason}", **ids)

    current = current_tags or frozenset()
    want = desired_tags(row, stage_map)
    add = frozenset(want - current)
    stage_unmapped = lookup_stage(stage_map, row.primary_state) not in STAGE_TAGS
    # Only stage/band tags are swapped; priority + source are never removed here.
    # A family we have no value for (unmapped stage, missing score) is left alone,
    # never stripped: "we don't know" must not erase what is already there.
    managed: set[str] = set()
    if not stage_unmapped:
        managed |= STAGE_TAGS
    if score_band_tag(row.score) is not None:
        managed |= BAND_TAGS
    remove = frozenset((current & managed) - want)

    score = row.score if row.score is not None and (state is None or state.last_score != row.score) else None
    summary = None
    why = row.why_now
    if why and (state is None or state.last_summary_hash != summary_hash(why)):
        summary = why  # never overwritten with blank

    if state is None:
        action = TAG_NEW
    elif add or remove or score is not None or summary is not None:
        action = RECONCILE
    else:
        action = ALREADY
    return Decision(
        action, tags_to_add=add, tags_to_remove=remove, write_score=score,
        write_summary=summary, stage_unmapped=stage_unmapped, callback_hold=callback, **ids,
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
    c.untagged = g(EXIT, 0)  # contacts that left the queue this run
    c.skipped_no_ghl_url = g(SKIP_NO_URL, 0)
    c.skipped_no_access = g(SKIP_NO_ACCESS, 0)
    c.skipped_ai_campaign = g(SKIP_AI_CAMPAIGN, 0)
    c.skipped_drained = g(SKIP_DRAINED, 0)
    return c
