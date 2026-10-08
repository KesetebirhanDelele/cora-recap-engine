"""Resolved-report email for an exception an operator closed by hand (pure text building; Ali 2026-10-08).

The report says what the error was, what was done about it, and who closed it, so whoever reads it does not have to open the
dashboard. Sent by app/services/alerting.send_exception_resolved_email.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

_SKIP_CTX = {"traceback", "stack", "payload"}


def _fmt(ts: Any) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC") if isinstance(ts, datetime) else str(ts or "unknown")


def action_line(a: dict[str, Any]) -> str:
    """One human line per recorded operator action on the exception."""
    ctx = a.get("context") or {}
    when = _fmt(a.get("at"))
    who = a.get("operator") or "operator"
    if a["action"] in ("retry_now", "retry_with_delay"):
        delay = f" after {ctx['delay_minutes']} min" if ctx.get("delay_minutes") else " immediately"
        job = ctx.get("new_job_id") or ""
        outcome = a.get("job_status")
        tail = f"; the new job {job[:8]} {outcome}" if job and outcome else ""
        return f"- {when}: {who} re-ran the failed job{delay}{tail}."
    return f"- {when}: {who} did '{a['action']}'."


def build_resolution_email(exc: dict[str, Any], actions: list[dict[str, Any]], resolved_by: str, note: str,
                           now: datetime) -> tuple[str, str]:
    """(subject, body). `exc` has id, type, severity, entity_type, entity_id, context, created_at."""
    ctx = exc.get("context") or {}
    details = [f"{k}: {v}" for k, v in ctx.items() if k not in _SKIP_CTX and v not in (None, "")][:6]
    lines = [
        f"Alert resolved: {exc['type']}",
        "",
        "WHAT WENT WRONG",
        f"Type: {exc['type']} ({exc.get('severity', 'warning')})",
        f"Affected: {exc.get('entity_type') or 'item'} {exc.get('entity_id') or ''}".rstrip(),
        f"First seen: {_fmt(exc.get('created_at'))}",
        *details,
        "",
        "HOW IT WAS RESOLVED",
        *(action_line(a) for a in actions),
        f"- {_fmt(now)}: {resolved_by} marked it resolved" + (f": \"{note}\"" if note and note != "operator resolved" else "."),
        "",
        f"Exception ID: {exc['id']}",
        "This is an automated report from the Cora Recap Engine.",
    ]
    return f"[RESOLVED] Cora Alert: {exc['type']}", "\n".join(lines)
