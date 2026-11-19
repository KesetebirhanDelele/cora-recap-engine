"""
Daily push of "who staff actually reached" to Ali's Explorer app (spec/49 addendum, 2026-11-18).

Ali's pool suppression ignores anyone contacted in the last 7 days, but the column it reads
(explorer_journey_profiles.last_contacted_at) is empty. We hand him one row per GHL contact:
the time of that contact's last CONNECTED staff call (UTC). He maps ghl_contact_id -> lead ->
profile and writes the column himself; his response tells us what matched.

Contract (Ali, 2026-11-17/18):
  POST https://enterprise.colaberry.ai/api/v1/explorer-growth/contact-activity
  Authorization: Bearer <CORA_SERVICE_TOKEN>      (same token as the outreach feed)
  {"source": "ghl-rose-calls", "rows": [{"ghl_contact_id": "...", "last_connected_call_at": "...Z"}]}
  -> {"received","matched","unmatched","written","unchanged","rejected_future","unmatched_ghl_contact_ids"}
  * a timestamp with no offset is a 400; an unknown body key is a 400;
  * a timestamp more than 5 minutes in the future is refused (counted in rejected_future);
  * the same file twice is safe (he only ever moves a timestamp forward), so resending is harmless.

Rules here: connected calls only (a no-answer stays in his pool); the FIRST push is the trailing
14 days; after that every run re-sends the trailing 8 days (his suppression window is 7), so a missed day self-heals
and a contact who joins his list a few days after the call still gets the time. Off by default (app_config
explorer_contact_activity_enabled). Never logs the token or contact ids.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SOURCE = "ghl-rose-calls"
ENABLED_KEY = "explorer_contact_activity_enabled"
LAST_OK_KEY = "explorer_contact_activity_last_success_at"
FIRST_PUSH_DAYS = 14
# Every daily run re-sends the trailing 8 days of live conversations (Kes, 2026-11-19). Ali's suppression looks back
# 7 days and his write is forward-only, so repeats are harmless; the repeat is what lets a contact who becomes an
# Explorer learner a few days AFTER Rose's call still receive that call's time (his unmatched rows are dropped).
DAILY_WINDOW_DAYS = 8
OVERLAP = timedelta(days=1)  # extra margin back from the last success after an outage
BATCH_SIZE = 500
CLOCK_MARGIN = timedelta(seconds=30)  # never send a time Ali could call "future" (his tolerance is 5 min)
RETRIES = 2  # extra attempts on a 5xx / network error


class ContactActivityError(RuntimeError):
    """The push failed. The message never contains the token or contact ids."""


class ContactActivityNotConfigured(ContactActivityError):
    pass


@dataclass
class PushResult:
    window_start: datetime
    window_end: datetime
    rows: int = 0
    sent: bool = False
    received: int = 0
    matched: int = 0
    unmatched: int = 0
    written: int = 0
    unchanged: int = 0
    rejected_future: int = 0
    batches: int = 0
    skipped_reason: str | None = None
    unmatched_sample: list[str] = field(default_factory=list)
    unmatched_ids: list[str] = field(default_factory=list)  # full ids, for the operator tool only; never logged


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    """UTC with a trailing Z, second precision, as Ali requires."""
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_start(last_success: datetime | None, now: datetime, days: int | None = None) -> datetime:
    """days given (operator override) = trailing N days. First push (no success yet) = trailing 14 days. Normal
    daily run = trailing 8 days; after an outage it reaches back to one day before the last success, never past 14."""
    now = _utc(now)
    if days is not None:
        return now - timedelta(days=days)
    if last_success is None:
        return now - timedelta(days=FIRST_PUSH_DAYS)
    daily = now - timedelta(days=DAILY_WINDOW_DAYS)
    return max(min(_utc(last_success) - OVERLAP, daily), now - timedelta(days=FIRST_PUSH_DAYS))


def rep_ids(settings: Any) -> tuple[str, ...]:
    """GHL user ids whose calls count (comma-separated setting). Empty = all staff."""
    raw = getattr(settings, "explorer_contact_activity_rep_user_ids", "") or ""
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def build_rows(
    session: Session, start: datetime, end: datetime, rep_user_ids: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """One row per GHL contact: the latest CONNECTED call inside [start, end] by the given staff (empty = all).
    Sorted for a stable file."""
    from app.core.human_answer_judge import reached_conditions
    from app.models.staff_call_quality import StaffCallQuality as S

    conds = [
        *reached_conditions(S),
        S.call_time >= start,
        S.call_time <= end,
        S.ghl_contact_id.is_not(None),
        S.ghl_contact_id != "",
    ]
    if rep_user_ids:
        conds.append(S.rep_user_id.in_(rep_user_ids))
    q = (
        select(S.ghl_contact_id, func.max(S.call_time))
        .where(*conds)
        .group_by(S.ghl_contact_id)
        .order_by(S.ghl_contact_id)
    )
    return [{"ghl_contact_id": cid, "last_connected_call_at": iso_z(t)} for cid, t in session.execute(q).all()]


def build_body(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {"source": SOURCE, "rows": rows}


class ContactActivityClient:
    """POST to Ali's endpoint. Retries only 5xx / network errors; a 4xx is a contract problem and fails at once."""

    def __init__(self, url: str, token: str, *, client: Any = None, sleep: Any = None):
        if not url or not token:
            raise ContactActivityNotConfigured("contact-activity push needs both a URL and the service token")
        if not url.lower().startswith("https://"):
            raise ContactActivityNotConfigured("contact-activity URL must start with https://")
        self._url, self._token, self._client = url, token, client
        self._sleep = sleep or time.sleep

    def post(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        import httpx

        own = self._client is None
        client = self._client or httpx.Client(timeout=60.0, follow_redirects=False)
        try:
            last = "unknown error"
            for attempt in range(RETRIES + 1):
                try:
                    resp = client.post(
                        self._url, json=build_body(rows),
                        headers={"Authorization": f"Bearer {self._token}"},
                    )
                except httpx.TransportError as exc:
                    last = f"network error: {type(exc).__name__}"
                else:
                    code = resp.status_code
                    if code in (401, 403):
                        raise ContactActivityError(f"endpoint rejected the credential (HTTP {code})")
                    if code == 400:
                        raise ContactActivityError(f"endpoint rejected the request body (HTTP 400): {_detail(resp)}")
                    if 300 <= code < 400:
                        # A tracker/redirect in front of the API (Ali's mailer rewrote the link once): never follow.
                        raise ContactActivityError(f"endpoint redirected (HTTP {code}); the URL is not the API")
                    if code >= 500:
                        last = f"endpoint server error (HTTP {code})"
                    elif code != 200:
                        raise ContactActivityError(f"unexpected response (HTTP {code})")
                    else:
                        return _parse(resp)
                if attempt < RETRIES:
                    self._sleep(2 ** attempt)
            raise ContactActivityError(last)
        finally:
            if own:
                client.close()


def _detail(resp: Any) -> str:
    try:
        return str(resp.text)[:200]
    except Exception:  # pragma: no cover
        return ""


def _parse(resp: Any) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise ContactActivityError("endpoint response is not JSON") from exc
    if not isinstance(data, dict) or "received" not in data or "matched" not in data:
        raise ContactActivityError("endpoint response is missing the expected counts")
    return data


def push_contact_activity(
    session: Session, settings: Any, *, now: datetime | None = None, client: Any = None,
    dry_run: bool = False, window_days: int | None = None,
) -> PushResult:
    """Build and send the file. dry_run builds only (nothing sent, nothing recorded). Not gated here: the caller
    checks enabled()."""
    from app.core.app_config import get_str, set_config_value

    now = _utc(now or datetime.now(tz=timezone.utc))
    end = now - CLOCK_MARGIN
    raw = get_str(LAST_OK_KEY, session, settings, "")
    try:
        last_ok = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        last_ok = None
    start = window_start(last_ok, now, window_days)
    rows = build_rows(session, start, end, rep_ids(settings))
    res = PushResult(window_start=start, window_end=end, rows=len(rows))
    if dry_run:
        res.skipped_reason = "dry_run"
        return res
    if not rows:
        # Nothing to send is a success: advance the mark so the window does not grow forever.
        set_config_value(session, LAST_OK_KEY, iso_z(now), updated_by="explorer_contact_activity")
        res.skipped_reason = "no_rows"
        return res

    cli = client or ContactActivityClient(
        getattr(settings, "explorer_contact_activity_url", "") or "", getattr(settings, "cora_service_token", "") or "",
    )
    for i in range(0, len(rows), BATCH_SIZE):
        data = cli.post(rows[i:i + BATCH_SIZE])
        res.batches += 1
        for k in ("received", "matched", "unmatched", "written", "unchanged", "rejected_future"):
            setattr(res, k, getattr(res, k) + int(data.get(k) or 0))
        res.unmatched_ids += [str(x) for x in (data.get("unmatched_ghl_contact_ids") or [])]
        if len(res.unmatched_sample) < 5:
            res.unmatched_sample += [str(x)[:6] + "…" for x in (data.get("unmatched_ghl_contact_ids") or [])][:5]
    res.sent = True
    if res.rejected_future:
        logger.warning("explorer_contact_activity: %d rows refused as future-dated (clock skew?)", res.rejected_future)
    # Mark success only after every batch was accepted; a failure above leaves the old mark, so the next run re-sends.
    set_config_value(session, LAST_OK_KEY, iso_z(now), updated_by="explorer_contact_activity")
    logger.info(
        "explorer_contact_activity: pushed rows=%d batches=%d matched=%d unmatched=%d written=%d unchanged=%d",
        res.rows, res.batches, res.matched, res.unmatched, res.written, res.unchanged,
    )
    return res


def enabled(session: Session, settings: Any) -> bool:
    from app.core.app_config import get_bool

    return get_bool(ENABLED_KEY, session, settings, False)
