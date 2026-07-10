"""
GHL Marketplace OAuth token storage and refresh — spec/19, spec/20.

Backs the OAuth Marketplace app (app/adapters/ghl_conversations.py), separate
from the Private Integration token used by app/adapters/ghl.py (GHLClient).

One row per GHL location in ghl_oauth_tokens. Access tokens expire ~24h;
refresh happens lazily at read time via get_valid_access_token(), not on a
schedule — simplest thing that works, per spec/20's stated preference.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.adapters.ghl_conversations import GhlConversationsClient
from app.config import Settings
from app.models.ghl_oauth_token import GhlOAuthToken

logger = logging.getLogger(__name__)

# Refresh this many seconds before actual expiry, to avoid racing a token
# that expires mid-request.
_REFRESH_SKEW_SECONDS = 120


def store_tokens(session: Session, token_response: dict) -> GhlOAuthToken:
    """
    Upsert a GhlOAuthToken row from a raw GHL /oauth/token response.

    Used after both the initial authorization-code exchange and every
    refresh — GHL rotates the refresh_token on each use, so the full pair
    is always overwritten together, never just the access_token alone.

    Caller is responsible for committing the session.
    """
    location_id = token_response.get("locationId")
    if not location_id:
        raise ValueError(
            f"GHL token response missing locationId — cannot store token. "
            f"Response keys: {list(token_response.keys())}"
        )

    access_token = token_response["access_token"]
    refresh_token = token_response["refresh_token"]
    expires_in = int(token_response.get("expires_in", 0))
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)

    row = session.get(GhlOAuthToken, location_id)
    if row is None:
        row = GhlOAuthToken(
            location_id=location_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        session.add(row)
    else:
        row.access_token = access_token
        row.refresh_token = refresh_token
        row.expires_at = expires_at

    return row


def complete_oauth_install(
    session: Session,
    code: str,
    settings: Settings,
    *,
    _client: GhlConversationsClient | None = None,
) -> GhlOAuthToken:
    """
    Full install flow: exchange an authorization code, transparently convert
    a Company-level token to a Location-level one if needed, and store it.

    This is what the /oauth/callback route should call — never call
    exchange_code_for_token() + store_tokens() directly, since a raw
    Company-level response would fail store_tokens()'s locationId check
    (spec/20 §7 — confirmed this app can return either depending on how the
    install was authorized, regardless of the app's configured Target User).

    Raises ValueError if a Company-level token is returned and no target
    location is configured (ghl_oauth_target_location_id / ghl_location_id).
    """
    client = _client or GhlConversationsClient(settings=settings)
    token_response = client.exchange_code_for_token(code)

    if token_response.get("userType") == "Company":
        company_id = token_response.get("companyId")
        target_location_id = settings.ghl_oauth_effective_target_location_id
        if not target_location_id:
            raise ValueError(
                "GHL returned a Company-level token but no target location is "
                "configured (set GHL_OAUTH_TARGET_LOCATION_ID or GHL_LOCATION_ID) "
                "— cannot convert to a usable Location-level token."
            )
        logger.info(
            "GHL OAuth install | Company token returned | company_id=%s target_location_id=%s",
            company_id, target_location_id,
        )
        token_response = client.get_location_token(
            token_response["access_token"], company_id, target_location_id
        )

    return store_tokens(session, token_response)


def get_valid_access_token(
    session: Session,
    location_id: str,
    settings: Settings,
    *,
    _client: GhlConversationsClient | None = None,
) -> str | None:
    """
    Return a valid (non-expired) access token for the given location.

    Returns None if no token has ever been stored for this location — the
    caller must treat this as "OAuth app not installed here yet" and skip
    cleanly (shadow/no-op), not as an error (spec/20 Acceptance Criterion 9).

    Refreshes and persists a new token pair if the stored one is within
    _REFRESH_SKEW_SECONDS of expiring. Raises GhlConversationsError if the
    refresh itself fails (e.g. revoked refresh_token) — the caller is
    responsible for turning that into a critical exception/alert
    (Acceptance Criterion 5), this function does not create one itself.
    """
    row = session.get(GhlOAuthToken, location_id)
    if row is None:
        return None

    now = datetime.now(tz=timezone.utc)
    if row.expires_at - timedelta(seconds=_REFRESH_SKEW_SECONDS) > now:
        return row.access_token

    client = _client or GhlConversationsClient(settings=settings)
    token_response = client.refresh_access_token(row.refresh_token)
    updated_row = store_tokens(session, token_response)
    return updated_row.access_token
