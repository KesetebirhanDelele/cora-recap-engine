"""
Database engine and session factory.

Provides:
  get_sync_engine()   — psycopg2 sync engine (Alembic migrations)
  get_async_engine()  — asyncpg async engine (API layer)
  get_sync_session()  — sync context manager for worker jobs (Phase 6)
  get_session()       — async context manager for API routes (Phase 8)

Phase 3: engine factories defined.
Phase 6: get_sync_session() added for worker job functions.
Phase 8: async session dependency injected into dashboard routes.

Connection URL handling:
  DATABASE_URL must use the postgresql:// scheme in .env.
  The async engine automatically rewrites it to postgresql+asyncpg://.
  The sync engine uses postgresql+psycopg2:// (Alembic default driver).
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.config import get_settings


def _sync_url(database_url: str) -> str:
    """Ensure the URL uses the psycopg2 sync driver."""
    url = database_url
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def _async_url(database_url: str) -> str:
    """Ensure the URL uses the asyncpg async driver."""
    url = database_url
    for prefix in ("postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


def get_sync_engine() -> Engine:
    """Return a synchronous SQLAlchemy engine (for Alembic and seed scripts)."""
    settings = get_settings()
    url = settings.database_url
    if not url:
        url = (
            f"postgresql+psycopg2://{settings.postgres_username}:"
            f"{settings.postgres_password}@{settings.postgres_host}:"
            f"{settings.postgres_port}/{settings.postgres_database}"
        )
    return create_engine(
        _sync_url(url),
        echo=settings.postgres_echo,
        pool_size=settings.postgres_pool_size or 5,
        max_overflow=settings.postgres_max_overflow or 10,
    )


def get_async_engine():
    """Return an async SQLAlchemy engine (for API and worker use)."""
    settings = get_settings()
    url = settings.database_url
    if not url:
        url = (
            f"postgresql+asyncpg://{settings.postgres_username}:"
            f"{settings.postgres_password}@{settings.postgres_host}:"
            f"{settings.postgres_port}/{settings.postgres_database}"
        )
    return create_async_engine(
        _async_url(url),
        echo=settings.postgres_echo,
        pool_size=settings.postgres_pool_size or 5,
        max_overflow=settings.postgres_max_overflow or 10,
    )


# Module-level async engine and session factory (initialized once at startup)
_async_engine = None
_async_session_factory = None


def init_db() -> None:
    """Initialize the async engine and session factory. Call at app startup."""
    global _async_engine, _async_session_factory
    _async_engine = get_async_engine()
    _async_session_factory = async_sessionmaker(
        _async_engine, class_=AsyncSession, expire_on_commit=False
    )


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager providing a database session."""
    if _async_session_factory is None:
        raise RuntimeError(
            "Database not initialized. Call init_db() at application startup."
        )
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """
    Sync context manager yielding a database session for worker jobs.

    Each call creates a new session from the sync engine.
    Commits on clean exit, rolls back on exception.
    """
    engine = get_sync_engine()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
