"""
Exception management routes — operator dashboard actions.

All routes require: Authorization: Bearer {SECRET_KEY}
Optional header:   X-Operator-Id: {your-name}  (defaults to 'dashboard')

Endpoints:
  GET  /v1/exceptions                         — list exceptions (filterable)
  GET  /v1/exceptions/{id}                    — exception detail + audit trail
  POST /v1/exceptions/{id}/retry-now          — re-enqueue immediately
  POST /v1/exceptions/{id}/retry-delay        — re-enqueue after a delay
  POST /v1/exceptions/{id}/cancel-future-jobs — cancel pending jobs for entity
  POST /v1/exceptions/{id}/force-finalize     — terminal state + resolve

Operator conflict rule: first successful state transition wins.
Concurrent second action returns HTTP 409 with conflict=True body.
All actions are audit-logged regardless of outcome.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DashboardAuth
from app.api.schemas.dashboard import (
    CancelFutureJobsResponse,
    ExceptionDetailResponse,
    ExceptionListResponse,
    ForceFinalizationResponse,
    RetryDelayRequest,
    RetryDelayResponse,
    RetryNowResponse,
)
from app.db import get_sync_session

logger = logging.getLogger(__name__)

router = APIRouter()


def _svc():
    from app.services import dashboard
    return dashboard


@router.get("", response_model=ExceptionListResponse, status_code=status.HTTP_200_OK)
def list_exceptions(
    auth: DashboardAuth,
    exc_status: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List exceptions with optional filtering by status, severity, or search term."""
    with get_sync_session() as session:
        return _svc().list_exceptions(
            session,
            status=exc_status,
            severity=severity,
            search=search,
            limit=limit,
            offset=offset,
        )


@router.get("/{exception_id}", response_model=ExceptionDetailResponse,
            status_code=status.HTTP_200_OK)
def get_exception(exception_id: str, auth: DashboardAuth):
    """Get exception detail including full audit trail."""
    with get_sync_session() as session:
        result = _svc().get_exception_detail(session, exception_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Exception not found")
    return result


@router.post("/{exception_id}/retry-now", response_model=RetryNowResponse,
             status_code=status.HTTP_202_ACCEPTED)
def retry_now(exception_id: str, auth: DashboardAuth):
    """Re-enqueue a new job immediately for the exception's entity."""
    with get_sync_session() as session:
        result = _svc().retry_now(session, exception_id,
                                  operator_id=auth["operator_id"])
    if result.get("conflict"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"conflict": True, "reason": result.get("reason")})
    return result


@router.post("/{exception_id}/retry-delay", response_model=RetryDelayResponse,
             status_code=status.HTTP_202_ACCEPTED)
def retry_with_delay(exception_id: str, body: RetryDelayRequest, auth: DashboardAuth):
    """Schedule a delayed retry. Default: 60 minutes."""
    with get_sync_session() as session:
        result = _svc().retry_with_delay(
            session, exception_id,
            operator_id=auth["operator_id"],
            delay_minutes=body.delay_minutes,
        )
    if result.get("conflict"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"conflict": True, "reason": result.get("reason")})
    return result


@router.post("/{exception_id}/cancel-future-jobs", response_model=CancelFutureJobsResponse,
             status_code=status.HTTP_200_OK)
def cancel_future_jobs(exception_id: str, auth: DashboardAuth):
    """Cancel all pending/claimed scheduled jobs for the exception's entity."""
    with get_sync_session() as session:
        result = _svc().cancel_future_jobs(session, exception_id,
                                           operator_id=auth["operator_id"])
    if result.get("conflict"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"conflict": True, "reason": result.get("reason")})
    return result


@router.post("/{exception_id}/force-finalize", response_model=ForceFinalizationResponse,
             status_code=status.HTTP_200_OK)
def force_finalize(exception_id: str, auth: DashboardAuth):
    """
    Force a workflow to terminal state.

    Cancels future jobs, advances lead tier to 3 (if applicable),
    executes GHL finalization write (shadow-gated), resolves the exception.
    """
    with get_sync_session() as session:
        result = _svc().force_finalize(session, exception_id,
                                       operator_id=auth["operator_id"])
    if result.get("conflict"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"conflict": True, "reason": result.get("reason")})
    return result
