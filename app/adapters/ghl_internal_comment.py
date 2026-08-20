"""
GHL InternalComment adapter — transcript + recording link into Conversations.

A third, separate GHL auth mechanism from both app/adapters/ghl.py (GHLClient,
spec/16, contacts/tasks/fields only — no Conversations scope) and
app/adapters/ghl_conversations.py (OAuth Marketplace app, spec/19/20 — needed
only for type="Call" via a registered Conversation Provider). This one is a
second Private Integration token (settings.ghl_conversations_api_key), scoped
with conversations.readonly / conversations/message.readonly /
conversations/message.write.

Confirmed working request shape (tested live against both the Cora Sandbox
account and the real production Colaberry account, 2026-07-16):

    POST /conversations/messages
    Authorization: Bearer <ghl_conversations_api_key>
    Version: 2021-07-28
    {
      "contactId": "<real GHL contact id>",
      "type": "InternalComment",
      "message": "<transcript>\n\n--------------------------\n\nRecording: <url>"
    }

InternalComment is staff-only — never visible to the contact/customer. No
attachment is sent; the recording URL is plain text and renders as a
clickable link in GHL's UI. This sidesteps the attachment-validation wall
that blocked the OAuth Call-type write's recording field (spec/20 §7)
entirely, because there's no attachment involved.

Retry policy: same shape as GHLClient._request() (spec/16) — retries on
429/500/502/503/504 and httpx.TimeoutException, bounded by
settings.ghl_retry_max, exponential backoff.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_VERSION_HEADER = "2021-07-28"
_SEPARATOR = "\n\n--------------------------\n\n"


class GhlInternalCommentError(RuntimeError):
    """Raised when a GHL InternalComment write fails after all retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GhlInternalCommentClient:
    """
    GHL InternalComment write client.

    Usage:
        client = GhlInternalCommentClient()
        result = client.write_call_note(
            contact_id="...", call_id="...", transcript="...", recording_url="...",
        )
    """

    def __init__(self, settings: Settings | None = None, _http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._http = _http or httpx.Client(
            base_url=self.settings.ghl_base_url,
            timeout=self.settings.ghl_timeout_seconds,
        )

    # ── HTTP transport with bounded retry ─────────────────────────────────────

    def _request(self, method: str, path: str, *, _retry_delay: float = 1.0, **kwargs: Any) -> dict:
        for attempt in range(self.settings.ghl_retry_max + 1):
            try:
                resp = self._http.request(method, path, **kwargs)

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.settings.ghl_retry_max:
                        logger.warning(
                            "GHL InternalComment transient error | status=%d attempt=%d/%d path=%s",
                            resp.status_code, attempt + 1, self.settings.ghl_retry_max, path,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                    raise GhlInternalCommentError(
                        f"GHL InternalComment request failed after {attempt + 1} attempts: "
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                resp.raise_for_status()
                return resp.json() if resp.content else {}

            except httpx.TimeoutException as exc:
                if attempt < self.settings.ghl_retry_max:
                    logger.warning(
                        "GHL InternalComment timeout | attempt=%d/%d path=%s",
                        attempt + 1, self.settings.ghl_retry_max, path,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise GhlInternalCommentError(
                    f"GHL InternalComment request timed out after {attempt + 1} attempts: {method} {path}"
                ) from exc

            except httpx.HTTPStatusError as exc:
                raise GhlInternalCommentError(
                    f"GHL InternalComment HTTP error: {exc.response.status_code} {path} | "
                    f"body={exc.response.text[:500]}",
                    status_code=exc.response.status_code,
                ) from exc

        raise GhlInternalCommentError(
            f"GHL InternalComment request exhausted {self.settings.ghl_retry_max} retries: {method} {path}"
        )

    # ── Write ──────────────────────────────────────────────────────────────

    def write_call_note(
        self,
        *,
        contact_id: str,
        call_id: str,
        transcript: str,
        recording_url: str | None,
    ) -> dict:
        """
        Post a staff-only InternalComment with the call transcript and, if
        present, the recording URL as a plain-text link.

        Shadow-gated on settings.ghl_write_internal_comment — independent of
        both ghl_writes_enabled (spec/16) and ghl_write_conversation_log
        (spec/19/20 OAuth path); this is its own mechanism with its own switch.
        """
        message = f"Transcript (call {call_id})\n\n{transcript.strip()}"
        if recording_url:
            message += f"{_SEPARATOR}Recording: {recording_url}"

        payload = {"contactId": contact_id, "type": "InternalComment", "message": message}

        if not self.settings.ghl_write_internal_comment:
            return self._shadow_write("write_call_note", contact_id, payload)

        self.settings.validate_for_ghl_internal_comment()
        logger.info(
            "GHL write_call_note | contact_id=%s call_id=%s has_recording=%s",
            contact_id, call_id, bool(recording_url),
        )
        return self._request(
            "POST",
            "/conversations/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.settings.ghl_conversations_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Version": _VERSION_HEADER,
            },
        )

    def _shadow_write(self, operation: str, contact_id: str, payload: dict) -> dict:
        """Log a would-be write in shadow mode without calling the GHL API."""
        logger.info(
            "GHL InternalComment shadow write [%s] | contact_id=%s message_length=%d",
            operation, contact_id, len(payload.get("message", "")),
        )
        return {"shadow": True, "operation": operation, "contact_id": contact_id, "payload": payload}

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GhlInternalCommentClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
