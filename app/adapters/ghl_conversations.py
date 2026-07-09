"""
GHL Marketplace OAuth adapter — spec/19, spec/20.

Separate from app/adapters/ghl.py (GHLClient), which uses a static Private
Integration Bearer token. This adapter handles the OAuth 2.0 token lifecycle
(authorization-code exchange, refresh) for the Cora Marketplace app, and will
host the Conversations call-log write method once its request schema is
confirmed against the GHL sandbox (see module docstring note below).

Auth shape (confirmed against GHL's published OAuth docs — see spec/20):
  Token endpoint:  POST https://services.leadconnectorhq.com/oauth/token
  Content-Type:    application/x-www-form-urlencoded

  IMPORTANT — confirmed by live sandbox test, contradicts GHL's own docs for
  Sub-Account-targeted apps: the authorization-code exchange can return a
  Company-level token (userType=Company, locationId=None) even though this
  app's Target User is Sub-Account. get_location_token() handles converting
  that to a usable Location-level token — see app/services/ghl_oauth.py
  complete_oauth_install(), which always does this conversion before storing
  a token. Never assume exchange_code_for_token()'s result is directly usable
  for the Conversations write endpoint without checking userType first.

Retry policy: same shape as GHLClient._request() (spec/16) — retries on
429/500/502/503/504 and httpx.TimeoutException, bounded by
settings.ghl_retry_max, exponential backoff.

write_outbound_call() request schema — confirmed via live sandbox spike
(spec/20 §7), not guessed:
  - Call fields MUST nest under a "call" object. Flat top-level fields
    (callDuration/callStatus) are silently ignored by the API, not rejected.
  - call.to must be the target contact's phone number *exactly as stored on
    the GHL contact record* — a well-formatted but non-matching number is
    rejected (CONVERSATIONS_MSG_INVALID_PHONE).
  - Does NOT currently send `attachments` (recording) or a transcript field.
    Three different recording URLs (two external hosts, one from GHL's own
    "Upload file attachments" endpoint) were all rejected as "Invalid
    recording URL" — this needs further investigation (possibly an
    object-shaped attachment entry, possibly a GHL support question) before
    it can be added. Do not add a recording/transcript field here from
    assumption — see spec/20 §7 for the full investigation trail before
    attempting either.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_TOKEN_PATH = "/oauth/token"


class GhlConversationsError(RuntimeError):
    """Raised when a GHL Marketplace OAuth API call fails after all retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GhlConversationsClient:
    """
    GHL Marketplace OAuth token client.

    Usage:
        client = GhlConversationsClient()
        tokens = client.exchange_code_for_token(code)
        # tokens: {access_token, refresh_token, expires_in, locationId, userType, ...}

        tokens = client.refresh_access_token(refresh_token)
    """

    def __init__(self, settings: Settings | None = None, _http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        # _http injected in tests to avoid real network calls
        self._http = _http or httpx.Client(
            base_url=self.settings.ghl_base_url,
            timeout=self.settings.ghl_timeout_seconds,
        )

    # ── HTTP transport with bounded retry ─────────────────────────────────────
    # Mirrors GHLClient._request() (app/adapters/ghl.py) — same retry contract.

    def _request(
        self,
        method: str,
        path: str,
        *,
        _retry_delay: float = 1.0,
        **kwargs: Any,
    ) -> dict:
        for attempt in range(self.settings.ghl_retry_max + 1):
            try:
                resp = self._http.request(method, path, **kwargs)

                if resp.status_code in _RETRYABLE_STATUS:
                    if attempt < self.settings.ghl_retry_max:
                        logger.warning(
                            "GHL Marketplace OAuth transient error | status=%d attempt=%d/%d path=%s",
                            resp.status_code,
                            attempt + 1,
                            self.settings.ghl_retry_max,
                            path,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                    raise GhlConversationsError(
                        f"GHL Marketplace OAuth request failed after {attempt + 1} attempts: "
                        f"HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )

                resp.raise_for_status()
                return resp.json() if resp.content else {}

            except httpx.TimeoutException as exc:
                if attempt < self.settings.ghl_retry_max:
                    logger.warning(
                        "GHL Marketplace OAuth timeout | attempt=%d/%d path=%s",
                        attempt + 1,
                        self.settings.ghl_retry_max,
                        path,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise GhlConversationsError(
                    f"GHL Marketplace OAuth request timed out after {attempt + 1} attempts: {method} {path}"
                ) from exc

            except httpx.HTTPStatusError as exc:
                # Non-retryable 4xx errors raise immediately. Response body is
                # included — GHL's 400s are typically descriptive validation
                # errors and are useful for pinning down request schemas.
                raise GhlConversationsError(
                    f"GHL Marketplace OAuth HTTP error: {exc.response.status_code} {path} | "
                    f"body={exc.response.text[:500]}",
                    status_code=exc.response.status_code,
                ) from exc

        raise GhlConversationsError(
            f"GHL Marketplace OAuth request exhausted {self.settings.ghl_retry_max} retries: {method} {path}"
        )

    # ── OAuth token lifecycle ──────────────────────────────────────────────────

    def exchange_code_for_token(self, code: str) -> dict:
        """
        Exchange a fresh authorization code (from the /oauth/callback redirect)
        for a Location-level access + refresh token pair.

        Requires settings.validate_for_ghl_marketplace_oauth() to have already
        passed (checked by the caller — the API route).
        """
        logger.info("GHL Marketplace OAuth | exchanging authorization code for token")
        data = {
            "client_id": self.settings.ghl_marketplace_client_id,
            "client_secret": self.settings.ghl_marketplace_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "user_type": "Location",
        }
        if self.settings.ghl_oauth_redirect_uri:
            data["redirect_uri"] = self.settings.ghl_oauth_redirect_uri
        return self._request(
            "POST",
            _TOKEN_PATH,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )

    def refresh_access_token(self, refresh_token: str) -> dict:
        """
        Exchange a refresh_token for a new access + refresh token pair.

        GHL rotates the refresh token on use — the caller must persist the
        new refresh_token from the response, not reuse the old one.
        """
        logger.info("GHL Marketplace OAuth | refreshing access token")
        data = {
            "client_id": self.settings.ghl_marketplace_client_id,
            "client_secret": self.settings.ghl_marketplace_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "user_type": "Location",
        }
        if self.settings.ghl_oauth_redirect_uri:
            data["redirect_uri"] = self.settings.ghl_oauth_redirect_uri
        return self._request(
            "POST",
            _TOKEN_PATH,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )

    def write_outbound_call(
        self,
        access_token: str,
        *,
        contact_id: str,
        to_phone: str,
        from_phone: str,
        call_duration_seconds: int,
        call_status: str = "completed",
    ) -> dict:
        """
        Log a completed outbound call into a GHL contact's Conversations
        activity. Confirmed request schema — see module docstring and
        spec/20 §7. Does NOT attach a recording or transcript (see caveats).

        access_token: a Location-level token for the target GHL location —
        NOT this client's own OAuth client credentials. Callers get this via
        app.services.ghl_oauth.get_valid_access_token().

        to_phone must match the contact's phone number on file exactly (GHL
        validates this) — resolve/confirm it from the contact record, don't
        pass an unverified phone string.

        Shadow-gated on settings.ghl_write_conversation_log — independent of
        ghl_writes_enabled (the Private Integration gate, spec/16); this is a
        separate OAuth mechanism with its own on/off switch.
        """
        payload = {
            "type": "Call",
            "contactId": contact_id,
            "conversationProviderId": self.settings.ghl_conversation_provider_id,
            "call": {
                "callDuration": call_duration_seconds,
                "callStatus": call_status,
                "to": to_phone,
                "from": from_phone,
            },
        }

        if not self.settings.ghl_write_conversation_log:
            return self._shadow_write("write_outbound_call", contact_id, payload)

        self.settings.validate_for_ghl_marketplace_oauth()
        logger.info(
            "GHL write_outbound_call | contact_id=%s duration=%s status=%s",
            contact_id, call_duration_seconds, call_status,
        )
        return self._request(
            "POST",
            "/conversations/messages/outbound",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Version": "2021-07-28",
            },
        )

    def _shadow_write(self, operation: str, contact_id: str, payload: dict) -> dict:
        """
        Log a would-be write in shadow mode without calling the GHL API.

        Mirrors GHLClient._shadow_write() (spec/16) for consistency.
        """
        logger.info(
            "GHL Marketplace shadow write [%s] | contact_id=%s payload_keys=%s",
            operation, contact_id, list(payload.keys()),
        )
        return {"shadow": True, "operation": operation, "contact_id": contact_id, "payload": payload}

    def get_location_token(self, company_access_token: str, company_id: str, location_id: str) -> dict:
        """
        Exchange a Company-level access token for a Location-level one.

        Confirmed necessary in practice (spec/20 §7): despite this app's
        Target User being Sub-Account, the initial authorization-code
        exchange can still return a Company-level token (userType=Company,
        locationId=None) — GHL's Conversations write endpoint rejects those
        outright (401 "authClass type is not allowed to access this scope").
        This second exchange is what actually produces a usable token.

        Endpoint: POST /oauth/locationToken, form-encoded, authenticated with
        the *company* token (not client_id/secret).
        """
        logger.info(
            "GHL Marketplace OAuth | exchanging company token for location token | location_id=%s",
            location_id,
        )
        return self._request(
            "POST",
            "/oauth/locationToken",
            data={"companyId": company_id, "locationId": location_id},
            headers={
                "Authorization": f"Bearer {company_access_token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GhlConversationsClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
