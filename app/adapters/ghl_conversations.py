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
  user_type:       "Location" — this app's Target User is Sub-Account
                   (not "Company"/Agency), so the authorization-code exchange
                   yields a Location-level token directly, no second
                   "Get Location Access Token from Agency Token" step needed.

Retry policy: same shape as GHLClient._request() (spec/16) — retries on
429/500/502/503/504 and httpx.TimeoutException, bounded by
settings.ghl_retry_max, exponential backoff.

NOT YET IMPLEMENTED: write_outbound_call() (the actual call-log write to
POST /conversations/messages/outbound). GHL's public docs for this specific
endpoint render the request schema client-side (JS/Swagger UI) and were not
recoverable via static fetch — the exact field names for attachments,
conversationProviderId placement, and call duration/status could not be
confirmed from documentation alone. Per spec/20's decomposition (step 6),
this must be pinned empirically with one real test call against the
`Cora Sandbox` GHL account once a stored access token exists, not guessed.
Do not add this method from assumption — confirm the schema first.
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
