"""
FastAPI dependencies — reusable dependency injection components.

Dashboard auth:
  require_dashboard_auth() — validates Bearer token against SECRET_KEY.
  Returns the operator_id from the X-Operator-Id header (defaults to 'dashboard').

Usage in routes:
  @router.post("/{id}/retry-now")
  def retry_now(exception_id: str, auth=Depends(require_dashboard_auth)):
      operator_id = auth["operator_id"]
      ...
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import get_settings


def require_dashboard_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_operator_id: Annotated[str | None, Header()] = None,
) -> dict:
    """
    Validate the dashboard Bearer token.

    Expected header: Authorization: Bearer {SECRET_KEY}
    Optional header: X-Operator-Id: {operator_name}

    Returns {"operator_id": str} on success.
    Raises 403 on missing or invalid token.
    """
    settings = get_settings()
    expected = f"Bearer {settings.secret_key}"

    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "forbidden",
                    "message": "Valid Authorization: Bearer <token> header required",
                    "retryable": False,
                }
            },
        )

    return {"operator_id": x_operator_id or "dashboard"}


DashboardAuth = Annotated[dict, Depends(require_dashboard_auth)]
