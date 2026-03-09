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

    def __init__(self, settings: Settings | None = None, _http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        # _http injected in tests to avoid real network calls
        self._http = _http or httpx.Client(
            base_url=self.settings.ghl_base_url,
            timeout=self.settings.ghl_timeout_seconds,
        )

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.ghl_api_key}",
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
        **kwargs: Any,
    ) -> dict:
        """
        Execute an HTTP request with bounded retry on transient failures.

        _retry_delay: base delay seconds (doubles per attempt).
                      Pass 0.0 in tests to skip real sleeps.
        """
        for attempt in range(self.settings.ghl_retry_max + 1):
            try:
                resp = self._http.request(method, path, headers=self._headers(), **kwargs)

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
                raise GHLError(
                    f"GHL HTTP error: {exc.response.status_code} {path}",
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

    @staticmethod
    def resolve_field_id(field_label: str, contact: dict) -> str | None:
        """
        Find a custom field ID from a fetched contact record by label or key.

        GHL custom field objects: {id, name, fieldKey, value}.
        Matches on `name` (human label) first, then `fieldKey` (snake_case).

        Returns the field `id` string used for write payloads, or None.
        This resolves unresolved external field IDs at runtime without
        hard-coding production values.
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

    def build_task_payload(self, title: str, description: str = "") -> dict:
        """
        Build the request body for a GHL task creation.

        Per spec constraints:
          - dueDate is None (task_due_date_mode=blank)
          - No assignedTo — GHL-side assignment rules own final assignment
        """
        payload: dict[str, Any] = {
            "title": title,
            "status": "incompleted",
            "dueDate": None,
        }
        if description:
            payload["description"] = description
        return payload

    def build_note_payload(self, content: str) -> dict:
        """Build the request body for a GHL contact note append."""
        return {"body": content}

    # ── Write operations (shadow-gated) ───────────────────────────────────────

    def update_contact_fields(
        self, contact_id: str, field_updates: dict[str, str]
    ) -> dict:
        """
        Write custom field values to a GHL contact.

        Shadow mode (default): logs payload, returns shadow response dict.
        Live mode: calls GHL PUT /contacts/{id} with the field update payload.
        """
        payload = self.build_field_update_payload(field_updates)
        if not self.settings.ghl_writes_enabled:
            return self._shadow_write("update_contact_fields", contact_id, payload)
        self.settings.validate_for_ghl_writes()
        logger.info("GHL update_contact_fields | contact_id=%s", contact_id)
        return self._request("PUT", f"/contacts/{contact_id}", json=payload)

    def create_task(self, contact_id: str, title: str, description: str = "") -> dict:
        """
        Create a GHL task for a contact.

        Shadow mode (default): logs payload, returns shadow response dict.
        Live mode: calls GHL POST /contacts/{id}/tasks.

        Idempotency: callers must check task_events for an existing 'created'
        record before invoking (enforced by the dedupe service, Phase 3+).
        """
        payload = self.build_task_payload(title, description)
        if not self.settings.ghl_writes_enabled:
            return self._shadow_write("create_task", contact_id, payload)
        self.settings.validate_for_ghl_writes()
        logger.info("GHL create_task | contact_id=%s title=%r", contact_id, title)
        return self._request("POST", f"/contacts/{contact_id}/tasks", json=payload)

    def append_note(self, contact_id: str, content: str) -> dict:
        """
        Append a note to a GHL contact.

        Shadow mode (default): logs payload, returns shadow response dict.
        Live mode: calls GHL POST /contacts/{id}/notes.
        Note content is NOT logged (may contain transcript excerpts).
        """
        payload = self.build_note_payload(content)
        if not self.settings.ghl_writes_enabled:
            return self._shadow_write("append_note", contact_id, payload)
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
