"""
API service entrypoint — Phase 1 skeleton.

Registers route groups.  Business logic is in app.services.
External adapters are in app.adapters (all stubbed until Phase 4+).

Phase 1 state: routes registered, app boots, no real integration logic.
"""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import exceptions as exception_routes
from app.api.routes import webhooks
from app.config import get_settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cora Recap Engine",
        version="0.1.0",
        description=(
            "Outbound recap engine: webhook intake, AI-powered call analysis, "
            "GHL CRM updates, Synthflow callback scheduling, and admin dashboard."
        ),
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ── Route registration ────────────────────────────────────────────────────
    app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["webhooks"])
    app.include_router(exception_routes.router, prefix="/v1/exceptions", tags=["exceptions"])

    # ── Healthcheck ───────────────────────────────────────────────────────────
    @app.get("/health", tags=["ops"])
    async def healthcheck() -> dict:
        return {"status": "ok", "service": "cora-recap-engine"}

    logger.info(
        "Cora Recap Engine started | env=%s shadow_mode=%s ghl_write_mode=%s",
        settings.app_env,
        settings.shadow_mode_enabled,
        settings.ghl_write_mode,
    )

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
