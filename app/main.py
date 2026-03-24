"""
API service entrypoint.

Registers route groups.  Business logic is in app.services.
External adapters are in app.adapters.

Lifespan:
  On startup, attempts to connect to Redis and stores the default RQ
  queue on app.state.default_queue for use by the webhook route.
  If Redis is unavailable (e.g. in tests or during outages), the queue
  is set to None and webhook jobs are stored in Postgres only — the
  worker recovery loop enqueues them when Redis comes back.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import app.compat  # noqa: F401 — Windows fork→spawn patch; must precede rq imports
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin as admin_routes
from app.api.routes import exceptions as exception_routes
from app.api.routes import messages as messages_routes
from app.api.routes import webhooks
from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources at startup; clean up at shutdown."""
    settings = get_settings()
    app.state.default_queue = None

    try:
        import redis as redis_lib
        from rq import Queue

        url = (
            settings.redis_url
            or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        )
        ssl_kwargs = {"ssl_cert_reqs": None} if url.startswith("rediss://") else {}
        auth_kwargs: dict = {}
        if settings.redis_username:
            auth_kwargs["username"] = settings.redis_username
        if settings.redis_password:
            auth_kwargs["password"] = settings.redis_password

        redis_conn = redis_lib.from_url(url, **ssl_kwargs, **auth_kwargs)
        redis_conn.ping()  # verify credentials before accepting traffic
        app.state.default_queue = Queue(settings.rq_default_queue, connection=redis_conn)
        logger.info("Redis connected | queue=%s", settings.rq_default_queue)
    except Exception as exc:
        logger.warning(
            "Redis unavailable at startup — webhook jobs will be stored in Postgres only | %s", exc
        )

    yield
    # No explicit teardown required; Redis connections are pooled and GC'd.


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        lifespan=lifespan,
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
    app.include_router(admin_routes.router, prefix="/v1/admin", tags=["admin"])
    app.include_router(messages_routes.router, prefix="/v1/messages", tags=["messages"])

    # Dev/staging only: test call launcher (never active in production)
    if settings.app_env != "production":
        from app.api.routes import test_calls
        app.include_router(
            test_calls.router, prefix="/v1/test/calls", tags=["test-calls"]
        )

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
