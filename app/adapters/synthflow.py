"""
Synthflow adapter.

Triggers outbound AI calls via per-campaign Make Call webhooks.
Auth: Bearer token (SYNTHFLOW_API_KEY).

Voice agent / webhook routing (campaign → webhook → voice agent):
  New Lead  → SYNTHFLOW_LAUNCH_WORKFLOW_URL_New  (p6ihFj7HmplXM2WiuVsaC)
              → model_id 2608601d-bce6-4bb8-bc0f-f7df9dbf5971
  Cold Lead → SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold (33J546NiXxUUIRCbywNVH)
              → model_id 95fd0659-7446-423c-bc51-764c3060c90f
  Inbound   → no outbound Make Call (inbound only)
              → model_id f98454c1-2cd4-476c-b6f2-c5c425689e61

  Do NOT use JylDXjF8QB0Skr5cQzGGm — test/Nexus workflow, silently drops calls.

URL selection: settings.get_synthflow_launch_url(campaign_name)
  Raises ConfigError if the required URL is not configured.

schedule_callback() / SYNTHFLOW_MODEL_ID / SYNTHFLOW_BASE_URL:
  Dead config — schedule_callback() is never called in production.
  All outbound calls use launch_new_lead_call() only.

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
        name: str = "",
        model_id: str | None = None,
        scheduled_time: datetime | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """
        Build the request body for a Synthflow v2/calls API call.

        phone:          E.164 normalized phone number of the contact.
        name:           Contact's display name. Falls back to 'Customer' when empty.
        model_id:       Synthflow model/agent ID. Defaults to SYNTHFLOW_MODEL_ID.
        scheduled_time: UTC datetime for when the call should be placed.
                        If None, Synthflow places the call immediately.
        metadata:       Optional key-value metadata passed through to the call record.

        Returns a dict ready to POST. Does not call the API.
        """
        payload: dict[str, Any] = {
            "model_id": model_id or self.settings.synthflow_model_id,
            "phone": phone,
            "name": name or "Customer",
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
        name: str = "",
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
        payload = self.build_callback_payload(phone, name, model_id, scheduled_time, metadata)

        logger.info(
            "Synthflow schedule_callback | phone=<redacted> name=%r model_id=%s "
            "scheduled_time=%s payload_keys=%s",
            payload.get("name"),
            payload.get("model_id"),
            payload.get("scheduled_time", "immediate"),
            list(payload.keys()),
        )

        return self._post(payload, _retry_delay=_retry_delay)

    # ── New Lead outbound call launch ─────────────────────────────────────────

    def launch_new_lead_call(
        self,
        phone: str,
        lead_name: str,
        campaign_name: str = "New_Lead",
        metadata: dict | None = None,
        *,
        _retry_delay: float = 1.0,
    ) -> dict:
        """
        Trigger Synthflow's "Make Call" workflow for an outbound call.

        Selects the webhook URL based on campaign_name:
          Cold Lead  → SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold
          New Lead / others → SYNTHFLOW_LAUNCH_WORKFLOW_URL_New

        Returns the raw response dict from Synthflow.
        The caller must treat a successful response as "call requested",
        not "call completed" — completion arrives via the webhook callback.
        """
        url = self.settings.get_synthflow_launch_url(campaign_name)
        payload: dict = {
            "phone": phone,
            "name": lead_name,
            "campaign_name": campaign_name,
        }
        if metadata:
            payload["metadata"] = metadata

        logger.info(
            "Synthflow launch_new_lead_call | campaign=%s phone=<redacted>",
            campaign_name,
        )

        for attempt in range(self.settings.synthflow_retry_max + 1):
            try:
                resp = self._http.post(url, headers=self._headers(), json=payload)

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.settings.synthflow_retry_max:
                        logger.warning(
                            "Synthflow launch transient error | status=%d attempt=%d/%d",
                            resp.status_code, attempt + 1, self.settings.synthflow_retry_max,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                    raise SynthflowError(
                        f"Synthflow launch failed after {attempt + 1} attempts: "
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                resp.raise_for_status()
                result = resp.json() if resp.content else {}
                logger.info(
                    "Synthflow launch_new_lead_call: call accepted | campaign=%s",
                    campaign_name,
                )
                return result

            except httpx.TimeoutException as exc:
                if attempt < self.settings.synthflow_retry_max:
                    logger.warning(
                        "Synthflow launch timeout | attempt=%d/%d",
                        attempt + 1, self.settings.synthflow_retry_max,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise SynthflowError(
                    f"Synthflow launch timed out after {attempt + 1} attempts"
                ) from exc

            except httpx.HTTPStatusError as exc:
                raise SynthflowError(
                    f"Synthflow launch HTTP error: {exc.response.status_code}",
                    status_code=exc.response.status_code,
                ) from exc

        raise SynthflowError(
            f"Synthflow launch exhausted {self.settings.synthflow_retry_max} retries"
        )

    # ── Read operation ────────────────────────────────────────────────────────

    def get_call(self, call_id: str, *, _retry_delay: float = 1.0) -> dict:
        """
        Fetch a single Synthflow call record by call_id.

        GET https://api.synthflow.ai/v2/calls/{call_id}

        Returns the call record dict (unwrapped from any {"data": ...} envelope).
        Raises SynthflowError on 4xx/5xx or after retries are exhausted.
        """
        self.settings.validate_for_synthflow_read()
        base = str(self.settings.synthflow_base_url).rstrip("/")
        # synthflow_base_url points to /v2/calls; strip the path and rebuild
        from urllib.parse import urlparse
        parsed = urlparse(base)
        url = f"{parsed.scheme}://{parsed.netloc}/v2/calls/{call_id}"

        for attempt in range(self.settings.synthflow_retry_max + 1):
            try:
                resp = self._http.get(url, headers=self._headers())

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.settings.synthflow_retry_max:
                        logger.warning(
                            "Synthflow get_call transient error | status=%d attempt=%d/%d",
                            resp.status_code, attempt + 1, self.settings.synthflow_retry_max,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                    raise SynthflowError(
                        f"Synthflow get_call failed after {attempt + 1} attempts: "
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                resp.raise_for_status()
                data = resp.json() if resp.content else {}
                # Unwrap {"data": {...}} or {"data": [{...}]} envelope if present
                inner = data.get("data")
                if isinstance(inner, dict):
                    data = inner
                elif isinstance(inner, list) and inner:
                    data = inner[0]
                logger.info("Synthflow get_call | call_id=%s status=%s", call_id, data.get("status") or data.get("call_status"))
                return data

            except httpx.TimeoutException as exc:
                if attempt < self.settings.synthflow_retry_max:
                    logger.warning(
                        "Synthflow get_call timeout | attempt=%d/%d",
                        attempt + 1, self.settings.synthflow_retry_max,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise SynthflowError(
                    f"Synthflow get_call timed out after {attempt + 1} attempts"
                ) from exc

            except httpx.HTTPStatusError as exc:
                raise SynthflowError(
                    f"Synthflow get_call HTTP error: {exc.response.status_code}",
                    status_code=exc.response.status_code,
                ) from exc

        raise SynthflowError(
            f"Synthflow get_call exhausted {self.settings.synthflow_retry_max} retries"
        )

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SynthflowClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
