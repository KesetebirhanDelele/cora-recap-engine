"""
GHL / LeadConnector adapter — Phase 4.

Auth: per-location API key (Bearer JWT).  Auth shape confirmed:
  Authorization: Bearer {GHL_API_KEY}
  Version: 2021-07-28

Write-mode safety contract:
  All write operations check settings.ghl_writes_enabled FIRST.
  When False (shadow/log-only), the payload is logged and a shadow
  response dict is returned.  The real GHL API is never called.
  When True (live), settings.validate_for_ghl_writes() is called
  to ensure credentials and mode flags are consistent before proceeding.

Unresolved external IDs (config-driven, not hard-coded):
  - Custom field IDs for VM_EMAIL_HTML, VM_SMS_TEXT, LAST_CALL_STATUS,
    MARK_AS_LEAD, NOTES, TASK_PIPELINE_ID, TASK_DEFAULT_OWNER_ID.
  - Phase 4 payload builders use field labels as keys; field ID resolution
    happens at write time via resolve_field_id() from a fetched contact.

Retry policy:
  Retries on 429, 500, 502, 503, 504 and httpx.TimeoutException.
  Bounded by settings.ghl_retry_max. Delay doubles per attempt (2^n seconds).
  Non-retryable errors (4xx except 429) raise immediately.
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


class GHLError(RuntimeError):
    """Raised when a GHL API call fails after all retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GHLClient:
    """
    GoHighLevel / LeadConnector API client.

    Usage (read path — always safe):
        client = GHLClient()
        contact = client.search_contact_by_phone("+15551234567")

    Usage (write path — shadow-gated by default):
        result = client.create_task(contact_id, title="Follow-up call")
        # Shadow mode: logs payload, returns shadow dict, no API call.
        # Live mode:   calls GHL API (requires explicit approval to enable).

    Usage as context manager:
        with GHLClient() as client:
            contact = client.get_contact(contact_id)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        _http: httpx.Client | None = None,
        api_key_override: str | None = None,
    ):
        self.settings = settings or get_settings()
        # api_key_override: use a different Private Integration token than
        # settings.ghl_api_key — e.g. settings.ghl_conversations_api_key, which
        # is scoped for Conversations reads (contacts/tasks/fields scope on
        # ghl_api_key does not cover Conversations — confirmed live, a
        # conversations call against ghl_api_key returns 401 "not authorized
        # for this scope"). See app/adapters/ghl_internal_comment.py for the
        # existing write-side use of this same conversations key.
        self._api_key_override = api_key_override
        # _http injected in tests to avoid real network calls
        self._http = _http or httpx.Client(
            base_url=self.settings.ghl_base_url,
            timeout=self.settings.ghl_timeout_seconds,
        )

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key_override or self.settings.ghl_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Version": _VERSION_HEADER,
        }

    # ── HTTP transport with bounded retry ─────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        _retry_delay: float = 1.0,
        parse_json: bool = True,
        version_override: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute an HTTP request with bounded retry on transient failures.

        _retry_delay: base delay seconds (doubles per attempt).
                      Pass 0.0 in tests to skip real sleeps.
        parse_json: when False, returns raw response bytes instead of a
                    parsed dict — used for binary endpoints (e.g. call
                    recording audio).
        version_override: use a different Version header than the module
                    default for this one call — some newer Conversations
                    endpoints were tested live against both the legacy
                    header and "v3" with identical results, so this exists
                    for forward compatibility, not because it's currently
                    required.
        """
        headers = self._headers()
        if version_override:
            headers["Version"] = version_override
        for attempt in range(self.settings.ghl_retry_max + 1):
            try:
                resp = self._http.request(method, path, headers=headers, **kwargs)

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.settings.ghl_retry_max:
                        logger.warning(
                            "GHL transient error | status=%d attempt=%d/%d path=%s",
                            resp.status_code,
                            attempt + 1,
                            self.settings.ghl_retry_max,
                            path,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                    raise GHLError(
                        f"GHL request failed after {attempt + 1} attempts: "
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                resp.raise_for_status()
                if not parse_json:
                    return resp.content
                return resp.json() if resp.content else {}

            except httpx.TimeoutException as exc:
                if attempt < self.settings.ghl_retry_max:
                    logger.warning(
                        "GHL timeout | attempt=%d/%d path=%s",
                        attempt + 1,
                        self.settings.ghl_retry_max,
                        path,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise GHLError(
                    f"GHL request timed out after {attempt + 1} attempts: {method} {path}"
                ) from exc

            except httpx.HTTPStatusError as exc:
                # Non-retryable 4xx errors raise immediately
                body_snippet = exc.response.text[:300] if exc.response.content else ""
                raise GHLError(
                    f"GHL HTTP error: {exc.response.status_code} {path} | {body_snippet}",
                    status_code=exc.response.status_code,
                ) from exc

        raise GHLError(
            f"GHL request exhausted {self.settings.ghl_retry_max} retries: {method} {path}"
        )

    # ── Read operations ───────────────────────────────────────────────────────

    def search_contact_by_phone(self, phone: str) -> dict | None:
        """
        Search for a GHL contact by normalized E.164 phone number.

        Returns the first matching contact dict, or None if not found.
        Used during event enrichment to resolve contact_id from call data.
        Phone numbers are redacted from logs per security policy.
        """
        self.settings.validate_for_ghl_reads()
        logger.info("GHL search_contact_by_phone | phone=<redacted>")
        result = self._request(
            "GET",
            "/contacts/",
            params={"locationId": self.settings.ghl_location_id, "query": phone},
        )
        contacts = result.get("contacts", [])
        return contacts[0] if contacts else None

    def get_conversations_by_contact(self, contact_id: str, limit: int = 20) -> list[dict]:
        """
        Return the list of GHL conversations for a contact (newest first).

        Used to find the active conversation ID before fetching message history.
        Returns an empty list on any error so callers can degrade gracefully.
        """
        self.settings.validate_for_ghl_reads()
        logger.info("GHL get_conversations_by_contact | contact_id=%s", contact_id)
        result = self._request(
            "GET",
            "/conversations/search",
            params={
                "locationId": self.settings.ghl_location_id,
                "contactId": contact_id,
                "limit": limit,
            },
        )
        return result.get("conversations", [])

    def get_conversation_messages(self, conversation_id: str, limit: int = 20) -> list[dict]:
        """
        Return messages for a conversation (newest first).

        Each message dict from GHL includes: id, direction, messageType, body,
        dateAdded, status.  direction is 'inbound' | 'outbound'.
        messageType is 'SMS' | 'Email' | 'Activity' | etc.
        Returns an empty list on any error so callers can degrade gracefully.
        """
        self.settings.validate_for_ghl_reads()
        logger.info("GHL get_conversation_messages | conversation_id=%s", conversation_id)
        result = self._request(
            "GET",
            f"/conversations/{conversation_id}/messages",
            params={"limit": limit},
        )
        # GHL double-nests this response: {"messages": {"lastMessageId": ...,
        # "nextPage": ..., "messages": [...]}} — confirmed live 2026-08-27.
        # A single .get("messages", []) returns the inner metadata dict, not
        # the message list; iterating it yields its string keys, which then
        # crash on the first .get() call downstream. Unwrap both shapes
        # defensively in case some accounts/responses are single-nested.
        outer = result.get("messages", [])
        if isinstance(outer, dict):
            return outer.get("messages", [])
        return outer

    def search_conversations(
        self,
        *,
        contact_id: str | None = None,
        assigned_to: str | None = None,
        last_message_type: str | None = None,
        start_after_date: int | None = None,
        sort_by: str | None = None,
        sort: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search conversations location-wide (not tied to a known contact_id).

        assigned_to: GHL user ID — scope to a specific rep's conversations.
        last_message_type: e.g. "TYPE_CALL" — GHL's actual message-type value
            for calls (confirmed live; "CALL" alone is not the wire value).
        sort_by: e.g. "last_message_date" (GHL's documented values).
        sort: "asc" or "desc".
        start_after_date: a *pagination cursor*, not a "since this time"
            filter — per GHL's own docs, "should contain the sort value of
            the last document" (i.e. `lastMessageDate` from the previous
            page's last item, when paginating in the direction `sort_by` +
            `sort` establish). GHL's default sort (when `sort_by`/`sort` are
            omitted) is descending by recency — passing a fixed lookback
            timestamp with no explicit sort direction silently walks
            *backward* into an old, stale window rather than "since then to
            now" (confirmed live 2026-08-27 — see spec/23). Callers wanting
            "everything since time X" should pass `sort_by="last_message_date",
            sort="asc"` and treat this as the starting cursor, paginating
            forward from there — see `_run_scan_cycle`'s discovery loop.

        Note: last_message_type filters on the conversation's *most recent*
        message, not "contains a message of this type anywhere in the
        thread" — a call conversation that gets a follow-up text/note before
        this runs will no longer match. Prefer omitting this filter for
        discovery (walk each returned conversation's messages instead) —
        see spec/23.

        Requires ghl_conversations_api_key (contacts.readonly on
        ghl_api_key does not cover this — see __init__ note).
        """
        self.settings.validate_for_ghl_reads()
        params: dict[str, Any] = {"locationId": self.settings.ghl_location_id, "limit": limit}
        if contact_id:
            params["contactId"] = contact_id
        if assigned_to:
            params["assignedTo"] = assigned_to
        if last_message_type:
            params["lastMessageType"] = last_message_type
        if sort_by:
            params["sortBy"] = sort_by
        if sort:
            params["sort"] = sort
        if start_after_date is not None:
            params["startAfterDate"] = start_after_date
        result = self._request("GET", "/conversations/search", params=params)
        return result.get("conversations", [])

    def get_message_recording(self, message_id: str, location_id: str | None = None) -> bytes:
        """
        Fetch the raw call recording audio for a message (audio/x-wav).

        Raises GHLError (status_code=422) if the message has no recording —
        confirmed live, e.g. a call that never connected (busy/no-answer).
        Callers should catch this and skip, not treat it as a hard failure.
        """
        self.settings.validate_for_ghl_reads()
        loc_id = location_id or self.settings.ghl_location_id
        return self._request(
            "GET",
            f"/conversations/messages/{message_id}/locations/{loc_id}/recording",
            parse_json=False,
        )

    def get_message_transcription(self, message_id: str, location_id: str | None = None) -> list[dict] | None:
        """
        Fetch GHL's own auto-generated transcription for a message, if one
        exists. Returns a list of {mediaChannel, sentenceIndex, startTime,
        endTime, transcript, confidence} sentence entries, or None if GHL
        has not generated a transcript for this message (confirmed live:
        GHL returns 400 CONVERSATIONS_MSG_RECORDING_NOT_FOUND for a call
        that was never transcribed — e.g. transcription feature not enabled
        on the account, or the call predates it being enabled. This is a
        normal, expected outcome, not an error — callers needing a
        transcript should fall back to pulling the recording and
        transcribing independently when this returns None).
        """
        self.settings.validate_for_ghl_reads()
        loc_id = location_id or self.settings.ghl_location_id
        try:
            result = self._request(
                "GET",
                f"/conversations/locations/{loc_id}/messages/{message_id}/transcription",
            )
        except GHLError as exc:
            if exc.status_code == 400:
                return None
            raise
        return result if isinstance(result, list) else result.get("transcriptions")

    def get_contact(self, contact_id: str) -> dict:
        """
        Fetch a full GHL contact record by contact ID.

        The returned dict includes the `customFields` array needed to
        resolve field IDs for write operations via resolve_field_id().
        """
        self.settings.validate_for_ghl_reads()
        logger.info("GHL get_contact | contact_id=%s", contact_id)
        return self._request("GET", f"/contacts/{contact_id}")

    # ── Field resolution helpers ──────────────────────────────────────────────

    def get_location_fields(self) -> list[dict]:
        """
        Fetch all custom field definitions for the GHL location.

        Returns a list of {id, name, fieldKey, ...} objects — one per field
        defined in the location, regardless of whether any contact has a value.

        This is required for reliable label→ID resolution because
        GET /contacts/{id} only returns customFields that already have a value
        on that specific contact. New contacts or contacts that have never had
        a particular field written will have an empty customFields array.

        Requires `locations/customFields.readonly` scope on the Private
        Integration token.

        Use resolve_field_id_from_location() with the result to map
        field labels to UUIDs before any live write call.
        """
        self.settings.validate_for_ghl_reads()
        logger.info("GHL get_location_fields | location_id=%s", self.settings.ghl_location_id)
        result = self._request(
            "GET",
            f"/locations/{self.settings.ghl_location_id}/customFields",
        )
        return result.get("customFields", [])

    @staticmethod
    def resolve_field_id_from_location(field_label: str, location_fields: list[dict]) -> str | None:
        """
        Find a custom field UUID from location-level field definitions.

        Preferred over resolve_field_id() when the contact may not yet have
        a value for the target field (new contact, first write to that field).

        location_fields: result of get_location_fields().
        Matches on `name` (human label) or `fieldKey` (snake_case).
        """
        for field in location_fields:
            if field.get("name") == field_label or field.get("fieldKey") == field_label:
                return field.get("id")
        return None

    @staticmethod
    def resolve_field_id(field_label: str, contact: dict) -> str | None:
        """
        Find a custom field ID from a fetched contact record by label or key.

        IMPORTANT: GHL only returns customFields entries that already have a
        value on this contact. If a field has never been written, it will not
        appear here and this method will return None. Use
        resolve_field_id_from_location() with get_location_fields() when the
        contact may be new or when a field has never been set.

        GHL custom field objects in contact record: {id, value} only — the
        `name` and `fieldKey` are NOT returned by GET /contacts/{id}.
        (They ARE returned by GET /locations/{id}/customFields.)

        Returns the field `id` string used for write payloads, or None.
        """
        for field in contact.get("customFields", []):
            if field.get("name") == field_label or field.get("fieldKey") == field_label:
                return field.get("id")
        return None

    @staticmethod
    def get_field_value(field_label: str, contact: dict) -> str | None:
        """
        Read the current value of a custom field from a contact record.

        Used to read ai_campaign_value (tier state) during event routing.
        """
        for field in contact.get("customFields", []):
            if field.get("name") == field_label or field.get("fieldKey") == field_label:
                return field.get("value")
        return None

    # ── Payload builders (always safe — no API calls, no side effects) ────────

    def build_field_update_payload(self, field_updates: dict[str, str]) -> dict:
        """
        Build the request body for a GHL contact custom-field update.

        field_updates: {field_label_or_id: value}
        Note: GHL requires field `id` for writes. Resolve via resolve_field_id()
              from a fetched contact before using in a live write.
        In shadow mode this payload is logged but never sent.
        """
        return {
            "customFields": [
                {"id": label_or_id, "value": value}
                for label_or_id, value in field_updates.items()
            ]
        }

    def build_task_payload(
        self,
        title: str,
        description: str = "",
        assigned_to: str = "",
        due_date: str = "",
    ) -> dict:
        """
        Build the request body for a GHL task creation (v2 API).

        GHL v2 task API accepts: title, dueDate, completed (bool), assignedTo.
        It does NOT accept `status` or `description` — sending those fields
        causes a 422 response. description is accepted by the UI but not the API.

        assigned_to: GHL user ID string — omitted when blank.
        due_date: ISO 8601 string — omitted when blank.
        """
        payload: dict[str, Any] = {
            "title": title,
            "completed": False,
        }
        if assigned_to:
            payload["assignedTo"] = assigned_to
        if due_date:
            payload["dueDate"] = due_date
        return payload

    def build_note_payload(self, content: str) -> dict:
        """Build the request body for a GHL contact note append."""
        return {"body": content}

    # ── Write operations (shadow-gated) ───────────────────────────────────────
    #
    # All three write methods accept an optional `mode_flags` parameter.
    # When provided (from a worker that has a DB session), mode_flags takes
    # precedence over self.settings for the shadow/live gate check. This
    # allows dashboard changes to propagate immediately without a restart.
    # When mode_flags is None, falls back to the original settings-based check.

    def update_contact_fields(
        self,
        contact_id: str,
        field_updates: dict[str, str],
        *,
        mode_flags: Any = None,
    ) -> dict:
        """
        Write custom field values to a GHL contact.

        Shadow mode (default): logs payload, returns shadow response dict.
        Live mode: calls GHL PUT /contacts/{id} with the field update payload.

        mode_flags: optional ModeFlags from get_mode_flags(session, settings).
                    When supplied, overrides settings.ghl_writes_enabled.
        """
        payload = self.build_field_update_payload(field_updates)
        writes_enabled = mode_flags.ghl_writes_enabled if mode_flags is not None else self.settings.ghl_writes_enabled
        if not writes_enabled:
            return self._shadow_write("update_contact_fields", contact_id, payload)
        # When mode_flags provided the DB already authorized live writes — only
        # validate credentials (not the settings-level mode gate, which may lag
        # behind the DB state).  Fall back to full settings validation otherwise.
        if mode_flags is not None:
            self.settings.validate_for_ghl_reads()
        else:
            self.settings.validate_for_ghl_writes()
        logger.info("GHL update_contact_fields | contact_id=%s", contact_id)
        return self._request("PUT", f"/contacts/{contact_id}", json=payload)

    def create_task(
        self,
        contact_id: str,
        title: str,
        description: str = "",
        assigned_to: str = "",
        due_date: str = "",
        *,
        mode_flags: Any = None,
    ) -> dict:
        """
        Create a GHL task for a contact.

        Shadow mode (default): logs payload, returns shadow response dict.
        Live mode: calls GHL POST /contacts/{id}/tasks.

        Idempotency: callers must check task_events for an existing 'created'
        record before invoking (enforced by the dedupe service, Phase 3+).

        mode_flags: optional ModeFlags from get_mode_flags(session, settings).
        """
        payload = self.build_task_payload(title, description, assigned_to, due_date)
        writes_enabled = mode_flags.ghl_writes_enabled if mode_flags is not None else self.settings.ghl_writes_enabled
        if not writes_enabled:
            return self._shadow_write("create_task", contact_id, payload)
        if mode_flags is not None:
            self.settings.validate_for_ghl_reads()
        else:
            self.settings.validate_for_ghl_writes()
        logger.info("GHL create_task | contact_id=%s title=%r", contact_id, title)
        return self._request("POST", f"/contacts/{contact_id}/tasks", json=payload)

    def append_note(
        self,
        contact_id: str,
        content: str,
        *,
        mode_flags: Any = None,
    ) -> dict:
        """
        Append a note to a GHL contact.

        Shadow mode (default): logs payload, returns shadow response dict.
        Live mode: calls GHL POST /contacts/{id}/notes.
        Note content is NOT logged (may contain transcript excerpts).

        mode_flags: optional ModeFlags from get_mode_flags(session, settings).
        """
        payload = self.build_note_payload(content)
        writes_enabled = mode_flags.ghl_writes_enabled if mode_flags is not None else self.settings.ghl_writes_enabled
        if not writes_enabled:
            return self._shadow_write("append_note", contact_id, payload)
        if mode_flags is not None:
            self.settings.validate_for_ghl_reads()
        else:
            self.settings.validate_for_ghl_writes()
        logger.info("GHL append_note | contact_id=%s", contact_id)
        return self._request("POST", f"/contacts/{contact_id}/notes", json=payload)

    # ── Shadow write helper ───────────────────────────────────────────────────

    def _shadow_write(self, operation: str, contact_id: str, payload: dict) -> dict:
        """
        Log a would-be write in shadow mode without calling the GHL API.

        Returns a structured shadow response for caller transparency.
        """
        logger.info(
            "GHL shadow write [%s] | contact_id=%s shadow_log_only=%s payload_keys=%s",
            operation,
            contact_id,
            self.settings.ghl_write_shadow_log_only,
            list(payload.keys()),
        )
        return {
            "shadow": True,
            "operation": operation,
            "contact_id": contact_id,
            "payload": payload,
        }

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GHLClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
