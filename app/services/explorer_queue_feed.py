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
import time
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
    # Position in the feed (1 = top of the ranking) and Ali's lead id, so a skipped row can be
    # reported back to him by something he can look up (the live feed carries no name/email).
    rank: int | None = None
    lead_id: str | None = None

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
    count: int | None = None  # the feed's own "rows returned" figure, when it sends one


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
    for position, raw in enumerate(raw_rows, start=1):
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
                rank=position,
                lead_id=_opt_str(raw.get("lead_id")),
            )
        )
    count = payload.get("count")
    return FeedResult(
        view=view, rows=tuple(rows), tracked_since=_opt_str(payload.get("tracked_since")),
        count=count if isinstance(count, int) and not isinstance(count, bool) else None,
    )


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


class LiveFeed:
    """Ali's token-protected read: GET <url>?view=..&limit=.. with a Bearer token.

    Contract (Ali, 2026-11-06): send ONLY `view` and `limit` (any other parameter is a 400);
    `limit` defaults to 25 server-side so it is always sent explicitly; the payload has no
    name/email. `count` in the response is the number of rows returned; a result equal to the
    limit means more rows exist (no paging yet). The token is never logged.
    """

    RETRIES = 2  # extra attempts on a 5xx / network error, then raise

    def __init__(self, url: str, token: str, *, client: Any = None, sleep: Any = None):
        if not url or not token:
            raise FeedNotConfigured("live feed needs both a URL and a service token")
        self._url = url
        self._token = token
        self._client = client
        self._sleep = sleep or time.sleep

    def fetch(self, view: str = "reach_out", limit: int = 25) -> FeedResult:
        import httpx

        if view not in VALID_VIEWS:
            raise FeedError(f"unknown view: {view!r}")
        if not 1 <= limit <= MAX_LIMIT:
            raise FeedError(f"limit must be 1..{MAX_LIMIT}, got {limit}")
        own = self._client is None
        client = self._client or httpx.Client(timeout=30.0)
        try:
            last_err = "unknown error"
            for attempt in range(self.RETRIES + 1):
                try:
                    resp = client.get(
                        self._url, params={"view": view, "limit": limit},
                        headers={"Authorization": f"Bearer {self._token}"},
                    )
                except httpx.TransportError as exc:
                    last_err = f"network error: {type(exc).__name__}"
                else:
                    if resp.status_code in (401, 403):
                        raise FeedError(f"feed rejected the credential (HTTP {resp.status_code})")
                    if resp.status_code == 400:
                        raise FeedError("feed rejected the request parameters (HTTP 400)")
                    if resp.status_code >= 500:
                        last_err = f"feed server error (HTTP {resp.status_code})"
                    elif resp.status_code != 200:
                        raise FeedError(f"unexpected feed response (HTTP {resp.status_code})")
                    else:
                        return self._parse(resp, view)
                if attempt < self.RETRIES:
                    self._sleep(2 ** attempt)
            raise FeedError(last_err)
        finally:
            if own:
                client.close()

    @staticmethod
    def _parse(resp: Any, view: str) -> FeedResult:
        try:
            payload = resp.json()
        except ValueError as exc:
            raise FeedError("feed response is not JSON") from exc
        result = parse_feed_payload(payload)
        if result.view != view:
            raise FeedError(f"feed returned view {result.view!r}, asked for {view!r}")
        if result.count is not None and result.count != len(result.rows):
            raise FeedError(f"feed count {result.count} does not match {len(result.rows)} rows")
        return result


def build_feed(settings: Any) -> ExplorerFeed:
    """Pick the feed from settings. A fixture path wins (test feed; the nightly job stays on it
    until go-live); otherwise the live feed when URL and token are both configured."""
    path = getattr(settings, "explorer_queue_feed_path", "") or ""
    if path:
        return FixtureFeed(path)
    url = getattr(settings, "explorer_queue_feed_url", "") or ""
    token = getattr(settings, "cora_service_token", "") or ""
    if url and token:
        return LiveFeed(url, token)
    raise FeedNotConfigured(
        "no feed configured: set explorer_queue_feed_path (test) or explorer_queue_feed_url + CORA_SERVICE_TOKEN"
    )
