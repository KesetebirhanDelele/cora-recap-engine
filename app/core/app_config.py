"""
Runtime-editable app configuration — DB-first, settings fallback.

Lookup chain for every key:
  1. app_config table in Postgres (editable at runtime via dashboard)
  2. Settings field of the same name (from .env / environment)
  3. Explicit fallback value supplied by the caller

Workers must call get_config_value() (or the typed helpers below) instead
of reading settings fields directly for any key that is also in app_config.
This ensures dashboard changes take effect immediately without a restart.

Write path (dashboard only):
  set_config_value(session, key, value, updated_by) — upserts the row and
  writes an audit_log entry so every change is traceable.

Type coercion helpers:
  get_int(key, session, settings, fallback)   → int
  get_bool(key, session, settings, fallback)  → bool
  get_str(key, session, settings, fallback)   → str
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings

logger = logging.getLogger(__name__)


def get_config_value(
    key: str,
    session: Session,
    settings: Settings,
    fallback: Any = None,
) -> str | None:
    """
    Return the string value for key, applying DB → settings → fallback chain.

    Never raises. Returns fallback (default None) when the key is not found
    in any layer.
    """
    # 1. DB layer
    try:
        from app.models.app_config import AppConfig
        row = session.get(AppConfig, key)
        if row is not None:
            return row.value
    except Exception:
        logger.warning(
            "get_config_value: DB lookup failed for key=%r — falling back to settings",
            key, exc_info=True,
        )

    # 2. Settings layer
    settings_value = getattr(settings, key, None)
    if settings_value is not None:
        return str(settings_value)

    # 3. Explicit fallback
    return str(fallback) if fallback is not None else None


def get_int(key: str, session: Session, settings: Settings, fallback: int = 0) -> int:
    """Return config value coerced to int."""
    raw = get_config_value(key, session, settings, fallback)
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning(
            "get_int: could not parse %r=%r as int, using fallback=%d", key, raw, fallback
        )
        return fallback


def get_bool(key: str, session: Session, settings: Settings, fallback: bool = False) -> bool:
    """Return config value coerced to bool. Accepts 'true'/'false'/'1'/'0'."""
    raw = get_config_value(key, session, settings, fallback)
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


def get_str(key: str, session: Session, settings: Settings, fallback: str = "") -> str:
    """Return config value as str."""
    raw = get_config_value(key, session, settings, fallback)
    return raw if raw is not None else fallback


def set_config_value(
    session: Session,
    key: str,
    value: str,
    updated_by: str = "dashboard",
) -> None:
    """
    Upsert a config value and write an audit_log row.

    Caller is responsible for committing the session.
    """
    from app.models.app_config import AppConfig
    from app.models.audit import AuditLog

    now = datetime.now(tz=timezone.utc)

    row = session.get(AppConfig, key)
    old_value = row.value if row else None

    if row is None:
        row = AppConfig(key=key, value=value, updated_at=now, updated_by=updated_by)
        session.add(row)
    else:
        row.value = value
        row.updated_at = now
        row.updated_by = updated_by

    session.add(
        AuditLog(
            id=str(uuid.uuid4()),
            entity_type="app_config",
            entity_id=key,
            action="config_updated",
            operator_id=updated_by,
            context_json={"key": key, "old_value": old_value, "new_value": value},
            created_at=now,
        )
    )
    session.flush()
    logger.info(
        "set_config_value | key=%r old=%r new=%r by=%r", key, old_value, value, updated_by
    )
