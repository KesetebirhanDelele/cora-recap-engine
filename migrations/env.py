"""
Alembic migrations environment.

Loads DATABASE_URL from app settings (never hard-coded).
Imports all ORM models so Alembic can detect them for autogenerate.
Supports both offline (--sql) and online (connected) migration modes.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the project root is on sys.path so app imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import all models so their metadata is registered before autogenerate
import app.models  # noqa: F401 — side-effect import registers all ORM models
from app.config import get_settings
from app.models.base import Base

# Alembic Config object from alembic.ini
config = context.config

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object that Alembic compares against the DB for autogenerate
target_metadata = Base.metadata


def get_url() -> str:
    """Load the sync DB URL from app settings."""
    settings = get_settings()
    url = settings.database_url
    if not url:
        url = (
            f"postgresql+psycopg2://{settings.postgres_username}:"
            f"{settings.postgres_password}@{settings.postgres_host}:"
            f"{settings.postgres_port}/{settings.postgres_database}"
        )
    # Ensure the sync driver is used (alembic uses psycopg2)
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without a live DB connection."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to DB and applies changes."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
