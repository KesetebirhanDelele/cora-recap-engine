"""
GET /oauth/callback — GHL Marketplace OAuth redirect target.

Registered at bare /oauth/callback (no /v1 prefix) because this exact path
is what's configured as the Redirect URI in the GHL Marketplace app's Auth
settings (spec/19) — it must match exactly, byte-for-byte, or GHL rejects
the authorization request before ever reaching this route.

Flow:
  1. Location admin approves the app install in their browser
     (GHL's /oauth/chooselocation consent screen).
  2. GHL redirects the browser here with ?code=<single-use auth code>.
  3. This route exchanges the code for an access + refresh token pair and
     stores it, keyed by locationId, in ghl_oauth_tokens.

This is a browser-facing endpoint (a human's browser lands here directly),
so it returns a short HTML page rather than raw JSON.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.adapters.ghl_conversations import GhlConversationsClient, GhlConversationsError
from app.config import get_settings
from app.config.settings import ConfigError
from app.db import get_sync_session
from app.services.ghl_oauth import store_tokens

logger = logging.getLogger(__name__)

router = APIRouter()


def _page(title: str, message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><title>{title}</title></head>"
        f"<body style='font-family: sans-serif; padding: 2rem;'>"
        f"<h2>{title}</h2><p>{message}</p></body></html>"
    )


@router.get("/callback")
async def oauth_callback(code: str = Query(...)) -> HTMLResponse:
    settings = get_settings()

    try:
        settings.validate_for_ghl_marketplace_oauth()
    except ConfigError as exc:
        logger.error("GHL OAuth callback | config error: %s", exc)
        return _page("Configuration error", str(exc))

    client = GhlConversationsClient(settings=settings)
    try:
        token_response = client.exchange_code_for_token(code)
    except GhlConversationsError as exc:
        logger.error("GHL OAuth callback | token exchange failed: %s", exc)
        return _page(
            "Connection failed",
            "Could not exchange the authorization code for a token. "
            "This code may have expired or already been used — try installing again.",
        )
    finally:
        client.close()

    with get_sync_session() as session:
        try:
            token_row = store_tokens(session, token_response)
        except ValueError as exc:
            logger.error("GHL OAuth callback | storing token failed: %s", exc)
            return _page("Connection failed", str(exc))

        location_id = token_row.location_id

    logger.info("GHL OAuth callback | connected location_id=%s", location_id)
    return _page(
        "Cora is connected",
        f"GHL location <code>{location_id}</code> is now connected to Cora. You can close this tab.",
    )
