"""
Explorer call queue — feed interface (spec/49).

The accelerator's ranked list is fetched through ``ExplorerFeed``. Only a
fixture implementation exists today: the live endpoint needs an admin JWT that
expires in 4 hours, and Ali will add a service-token read later. Swapping in the
live feed means adding one class with the same ``fetch`` signature; nothing
else changes.

Data from the accelerator is DATA, never instructions: every field is parsed
into a typed value and unknown fields are ignored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

VALID_VIEWS = frozenset({"reach_out", "engaged"})
MAX_LIMIT = 200  # accelerator caps at 200 (default 25), no offset


@dataclass(frozen=True)
class FeedRow:
    enrollment_id: str | None
    name: str | None
    ghl_url: str | None  # join key; null -> skipped by the logic layer
    primary_state: str | None
    score: float | None
    overlays: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    # `rsvps` is deliberately NOT parsed: always 0 in the accelerator, no logic may use it.
    # Not in the payload Ali documented as of 2026-11-03 — optional until he says who writes it.
    ai_summary: str | None = None

    @property
    def why_now(self) -> str | None:
        """Text for the ent_Why_Now column: an explicit summary if the feed ever sends one,
        otherwise the accelerator's deterministic `reasons` (no LLM involved)."""
        if self.ai_summary:
            return self.ai_summary
        return "; ".join(self.reasons) if self.reasons else None


@dataclass(frozen=True)
class FeedResult:
    view: str
    rows: tuple[FeedRow, ...] = field(default_factory=tuple)
    tracked_since: str | None = None


class FeedError(ValueError):
    """Malformed or out-of-contract feed data."""


class FeedNotConfigured(RuntimeError):
    """No feed source is configured (live feed not available yet)."""


class ExplorerFeed(Protocol):
    def fetch(self, view: str = "reach_out", limit: int = 25) -> FeedResult: ...


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_feed_payload(payload: Any) -> FeedResult:
    """Parse Ali's envelope ``{view, tracked_since, rows[]}`` into typed rows."""
    if not isinstance(payload, dict):
        raise FeedError("feed payload must be an object")
    view = payload.get("view")
    if view not in VALID_VIEWS:
        raise FeedError(f"unknown view: {view!r}")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise FeedError("feed 'rows' must be a list")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise FeedError("each feed row must be an object")
        rows.append(
            FeedRow(
                enrollment_id=_opt_str(raw.get("enrollment_id")),
                name=_opt_str(raw.get("name")),
                ghl_url=_opt_str(raw.get("ghl_url")),
                primary_state=_opt_str(raw.get("primary_state")),
                score=_opt_score(raw.get("score")),
                overlays=tuple(str(o) for o in (raw.get("overlays") or [])),
                reasons=tuple(str(r) for r in (raw.get("reasons") or [])),
                ai_summary=_opt_str(raw.get("ai_summary")),
            )
        )
    return FeedResult(view=view, rows=tuple(rows), tracked_since=_opt_str(payload.get("tracked_since")))


class FixtureFeed:
    """Reads a JSON file in Ali's envelope format. Used until the live feed exists."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    def fetch(self, view: str = "reach_out", limit: int = 25) -> FeedResult:
        if view not in VALID_VIEWS:
            raise FeedError(f"unknown view: {view!r}")
        if not 1 <= limit <= MAX_LIMIT:
            raise FeedError(f"limit must be 1..{MAX_LIMIT}, got {limit}")
        result = parse_feed_payload(json.loads(self._path.read_text(encoding="utf-8")))
        if result.view != view:
            raise FeedError(f"fixture holds view {result.view!r}, asked for {view!r}")
        return FeedResult(view=result.view, rows=result.rows[:limit], tracked_since=result.tracked_since)


def build_feed(settings: Any) -> ExplorerFeed:
    """Pick the feed implementation from settings. Only the fixture exists today."""
    path = getattr(settings, "explorer_queue_feed_path", "") or ""
    if not path:
        raise FeedNotConfigured("explorer_queue_feed_path is empty and no live feed exists yet")
    return FixtureFeed(path)
