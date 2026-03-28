"""
Models package — SQLAlchemy ORM models for all authoritative tables.

Import order matters for Alembic autogenerate: all models must be imported
before the metadata is inspected. This module is the single import point.

Tables:
  LeadState            — campaign state per GHL contact
  CallEvent            — idempotent call event records
  ClassificationResult — AI analysis outputs (stamped with prompt version)
  SummaryResult        — student summary and consent gate result
  TaskEvent            — GHL task creation records (one success per call)
  ScheduledJob         — durable canonical job state (Redis/RQ recovery source)
  ShadowSheetRow       — Sheets mirror data (kept in schema; Phase 9 out of scope)
  ExceptionRecord      — surfaced failures for dashboard visibility
  AuditLog             — append-only operator action trail (Phase 8)
  ShadowAction         — intercepted actions when shadow_mode_enabled=true

Reporting views are defined in migrations/versions/0002_reporting_views.py
and not represented as ORM models (read via raw SQL / reporting queries).
"""
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.call_event import CallEvent
from app.models.classification import ClassificationResult
from app.models.exception import ExceptionRecord
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob
from app.models.shadow_action import ShadowAction
from app.models.shadow_sheet import ShadowSheetRow
from app.models.summary import SummaryResult
from app.models.task_event import TaskEvent

__all__ = [
    "Base",
    "LeadState",
    "CallEvent",
    "ClassificationResult",
    "SummaryResult",
    "TaskEvent",
    "ScheduledJob",
    "ShadowSheetRow",
    "ExceptionRecord",
    "AuditLog",
    "ShadowAction",
]
