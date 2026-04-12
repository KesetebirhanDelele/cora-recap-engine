"""
Dashboard API service — FastAPI app running on port 8001.

Separate from the main webhook API (port 8000).
Isolation rationale: dashboard auth headers and read traffic must not
be able to interfere with Synthflow webhook ingestion.

Startup: no RQ queues; dashboard is read-heavy + operator actions only.
CORS: configured from ALLOW_ORIGINS env var (default: http://localhost:3000).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify DB connectivity at startup; nothing to clean up."""
    settings = get_settings()
    try:
        from app.db import get_sync_session
        with get_sync_session() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        logger.info("dashboard-api: Postgres connection verified")
    except Exception as exc:
        logger.warning("dashboard-api: Postgres connection check failed at startup: %s", exc)

    logger.info(
        "dashboard-api: starting | env=%s shadow=%s ghl_write=%s",
        settings.app_env,
        settings.shadow_mode_enabled,
        settings.ghl_write_mode,
    )
    yield
    logger.info("dashboard-api: shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cora Dashboard API",
        version="2.0.0",
        description="Production dashboard API for the Cora Recap Engine.",
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
        lifespan=lifespan,
    )

    # CORS — allow Next.js frontend origin only
    origins = [o.strip() for o in settings.allow_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Operator-Id"],
    )

    # Register dashboard v2 routes
    from app.api.routes.dashboard_v2 import router as dashboard_router
    app.include_router(dashboard_router)

    @app.get("/health")
    def root_health() -> dict:
        """Service liveness probe."""
        return {"status": "ok", "service": "dashboard-api"}

    return app


app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "app.api.dashboard_main:app",
        host=settings.app_host,
        port=8001,
        reload=(settings.app_env == "development"),
        log_level=settings.log_level.lower(),
    )
