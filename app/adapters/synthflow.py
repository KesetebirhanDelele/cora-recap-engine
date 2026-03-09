"""
Synthflow adapter — Phase 7.

Schedules outbound AI callback calls for voicemail tier progression.
Auth: Bearer token (SYNTHFLOW_API_KEY).

Stop conditions confirmed before implementation:
  - SYNTHFLOW_API_KEY: present in config ✓
  - SYNTHFLOW_MODEL_ID: ebd5ad8c-64d6-4316-b3ad-b056c74ce973 ✓
  - Auth shape: Authorization: Bearer {api_key} ✓
  - Endpoint: POST SYNTHFLOW_BASE_URL (https://api.synthflow.ai/v2/calls) ✓

Duplicate callback prevention:
  Callers MUST check for existing pending scheduled_jobs before calling
  schedule_callback(). This adapter does not enforce idempotency itself —
  that responsibility belongs to the voicemail_jobs service layer.

New Lead policy stop condition:
  validate_for_new_lead_vm_policy() must pass before New Lead callbacks
  are scheduled. New Lead tier delays are currently unresolved.

Retry policy:
  Retries on 429, 5xx, TimeoutException.
  Bounded by settings.synthflow_retry_max.
  Delay doubles per attempt (_retry_delay kwarg overridable in tests).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class SynthflowError(RuntimeError):
    """Raised when a Synthflow API call fails after all retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SynthflowClient:
    """
    Synthflow AI calling API client.

    Usage:
        client = SynthflowClient()
        result = client.schedule_callback(
            phone="+15551234567",
            scheduled_time=datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc),
        )

    Injectable for tests:
        mock_http = MagicMock(spec=httpx.Client)
        client = SynthflowClient(settings=s, _http=mock_http)
    """

    def __init__(self, settings: Settings | None = None, _http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._http = _http or httpx.Client(
            timeout=float(self.settings.synthflow_timeout_seconds),
        )

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.synthflow_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── HTTP transport with bounded retry ─────────────────────────────────────

    def _post(
        self,
        payload: dict,
        *,
        _retry_delay: float = 1.0,
    ) -> dict:
        """
        POST to the Synthflow calls endpoint with bounded retry.

        _retry_delay: base delay seconds; pass 0.0 in tests to skip sleeps.
        """
        url = self.settings.synthflow_base_url

        for attempt in range(self.settings.synthflow_retry_max + 1):
            try:
                resp = self._http.post(url, headers=self._headers(), json=payload)

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.settings.synthflow_retry_max:
                        logger.warning(
                            "Synthflow transient error | status=%d attempt=%d/%d",
                            resp.status_code, attempt + 1, self.settings.synthflow_retry_max,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                    raise SynthflowError(
                        f"Synthflow request failed after {attempt + 1} attempts: "
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                resp.raise_for_status()
                return resp.json() if resp.content else {}

            except httpx.TimeoutException as exc:
                if attempt < self.settings.synthflow_retry_max:
                    logger.warning(
                        "Synthflow timeout | attempt=%d/%d",
                        attempt + 1, self.settings.synthflow_retry_max,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise SynthflowError(
                    f"Synthflow timed out after {attempt + 1} attempts"
                ) from exc

            except httpx.HTTPStatusError as exc:
                raise SynthflowError(
                    f"Synthflow HTTP error: {exc.response.status_code}",
                    status_code=exc.response.status_code,
                ) from exc

        raise SynthflowError(
            f"Synthflow request exhausted {self.settings.synthflow_retry_max} retries"
        )

    # ── Payload builder (always safe — no API call) ───────────────────────────

    def build_callback_payload(
        self,
        phone: str,
        model_id: str | None = None,
        scheduled_time: datetime | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """
        Build the request body for a Synthflow callback call.

        phone:          E.164 normalized phone number of the contact.
        model_id:       Synthflow model/agent ID. Defaults to SYNTHFLOW_MODEL_ID.
        scheduled_time: UTC datetime for when the call should be placed.
                        If None, Synthflow places the call immediately.
        metadata:       Optional key-value metadata passed through to the call record.

        Returns a dict ready to POST. Does not call the API.
        """
        payload: dict[str, Any] = {
            "model_id": model_id or self.settings.synthflow_model_id,
            "phone": phone,
        }
        if scheduled_time is not None:
            payload["scheduled_time"] = scheduled_time.isoformat()
        if metadata:
            payload["metadata"] = metadata
        return payload

    # ── Write operation ───────────────────────────────────────────────────────

    def schedule_callback(
        self,
        phone: str,
        model_id: str | None = None,
        scheduled_time: datetime | None = None,
        metadata: dict | None = None,
        *,
        _retry_delay: float = 1.0,
    ) -> dict:
        """
        Schedule a Synthflow AI callback call.

        Callers must check for duplicate pending callbacks before calling this.
        Duplicate prevention is the caller's responsibility (enforced in
        voicemail_jobs.py via scheduled_jobs table lookup).

        Returns the Synthflow API response dict containing the call record.
        """
        self.settings.validate_for_synthflow()
        payload = self.build_callback_payload(phone, model_id, scheduled_time, metadata)

        logger.info(
            "Synthflow schedule_callback | phone=<redacted> model_id=%s scheduled_time=%s",
            payload.get("model_id"),
            payload.get("scheduled_time", "immediate"),
        )

        return self._post(payload, _retry_delay=_retry_delay)

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SynthflowClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
