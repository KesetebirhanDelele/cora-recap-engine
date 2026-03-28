"""
Shadow mode logging helper.

log_shadow_action() is the single write point for shadow_actions rows.
Call it from worker jobs when settings.shadow_mode_enabled is True,
immediately before completing the job without executing the real action.

This module has no side effects and no imports from other worker modules,
so it can be imported anywhere in the worker layer without circular deps.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def log_shadow_action(
    session,
    contact_id: str,
    action_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Insert a ShadowAction row recording an intercepted external action.

    Parameters
    ----------
    session     : SQLAlchemy sync Session (already open, caller manages commit)
    contact_id  : GHL contact identifier (or phone number if not yet resolved)
    action_type : "outbound_call" | "sms" | "email"
    payload     : action-specific context (campaign, message preview, etc.)

    The row is flushed but not committed — the caller's session commit covers it.
    """
    from app.models.shadow_action import ShadowAction

    row = ShadowAction(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        action_type=action_type,
        payload=payload,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(row)
    session.flush()

    logger.info(
        "shadow_mode: intercepted %s | contact_id=%s payload=%s",
        action_type, contact_id, payload,
    )
