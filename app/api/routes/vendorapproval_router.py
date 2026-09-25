from __future__ import annotations

import logging

import pyodbc
from fastapi import APIRouter, HTTPException, status

from app.schemas.vendorapproval_schema import (
    ApprovalFetchRequest,
    ApprovalSubmitRequest,
)
from app.services.vendorapproval_service import (
    ApprovalAssignmentNotFoundError,
    ApprovalAuthorizationError,
    ApprovalBatchNotFoundError,
    ApprovalConfigurationError,
    ApprovalConflictError,
    ApprovalDecisionError,
    fetch_approval_dashboard_sync,
    submit_approval_decision_sync,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/vendor-approval",
    tags=["Vendor Approval"],
)


def _handle_approval_error(
    exc: Exception,
) -> None:
    if isinstance(
        exc,
        (
            ApprovalBatchNotFoundError,
            ApprovalAssignmentNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        ApprovalAuthorizationError,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        (
            ApprovalDecisionError,
            ApprovalConflictError,
            ApprovalConfigurationError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if isinstance(exc, pyodbc.Error):
        logger.exception(
            "Vendor approval database error"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "[VENDOR_APPROVAL_DB_ERROR] "
                f"{exc}"
            ),
        ) from exc

    logger.exception(
        "Unexpected vendor approval error"
    )

    raise HTTPException(
        status_code=(
            status.HTTP_500_INTERNAL_SERVER_ERROR
        ),
        detail=(
            "[VENDOR_APPROVAL_ERROR] "
            f"{exc}"
        ),
    ) from exc


@router.post(
    "/fetch",
    summary="Fetch vendor approval page",
)
def fetch_approval_dashboard(
    payload: ApprovalFetchRequest,
):
    try:
        return fetch_approval_dashboard_sync(
            payload
        )

    except Exception as exc:
        _handle_approval_error(exc)


@router.post(
    "/decision",
    summary="Submit approver decision",
)
def submit_approval_decision(
    payload: ApprovalSubmitRequest,
):
    try:
        return submit_approval_decision_sync(
            payload
        )

    except Exception as exc:
        _handle_approval_error(exc)