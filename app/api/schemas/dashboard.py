"""
Pydantic request/response schemas for the dashboard API.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class RetryDelayRequest(BaseModel):
    delay_minutes: int = Field(default=60, ge=0, description="Minutes to wait before retry")
    reason: str = Field(default="", description="Optional operator note")


class RetryNowResponse(BaseModel):
    success: bool = False
    conflict: bool = False
    new_job_id: Optional[str] = None
    reason: Optional[str] = None


class RetryDelayResponse(BaseModel):
    success: bool = False
    conflict: bool = False
    new_job_id: Optional[str] = None
    run_at: Optional[str] = None
    reason: Optional[str] = None


class CancelFutureJobsResponse(BaseModel):
    success: bool = False
    conflict: bool = False
    cancelled_count: int = 0
    cancelled_ids: list[str] = []
    reason: Optional[str] = None


class ForceFinalizationResponse(BaseModel):
    success: bool = False
    conflict: bool = False
    cancelled_jobs: int = 0
    lead_finalized: bool = False
    reason: Optional[str] = None


class ExceptionSummary(BaseModel):
    id: str
    type: str
    severity: str
    status: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    call_event_id: Optional[str] = None
    context_json: Optional[dict[str, Any]] = None
    version: int
    created_at: str
    updated_at: str


class ExceptionListResponse(BaseModel):
    exceptions: list[ExceptionSummary]
    total: int
    limit: int
    offset: int


class AuditEntry(BaseModel):
    id: str
    action: str
    operator_id: str
    context: dict[str, Any]
    created_at: str


class ExceptionDetailResponse(ExceptionSummary):
    resolution_reason: Optional[str] = None
    resolved_by: Optional[str] = None
    audit_trail: list[AuditEntry] = []
