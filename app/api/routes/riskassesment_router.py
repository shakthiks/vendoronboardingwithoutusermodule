import logging

import pyodbc
from fastapi import APIRouter, HTTPException, status

from app.schemas.riskassessment_schema import (
    RiskAssessmentListPageRequest,
    RiskAssessmentRequest,
)
from app.services.riskassessment_service import (
    RiskAssessmentConflictError,
    RiskAssessmentNotFoundError,
    create_risk_assessment_sync,
    risk_assessment_listpage_sync,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/riskassessment",
    tags=["Risk Assessment"],
)


def _handle_risk_error(exc: Exception) -> None:
    if isinstance(
        exc,
        RiskAssessmentNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        RiskAssessmentConflictError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if isinstance(exc, pyodbc.Error):
        logger.exception(
            "Risk-assessment database error"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail="Risk-assessment database error",
        ) from exc

    logger.exception(
        "Unexpected risk-assessment error"
    )

    raise HTTPException(
        status_code=(
            status.HTTP_500_INTERNAL_SERVER_ERROR
        ),
        detail="Unexpected risk-assessment error",
    ) from exc


@router.post(
    "/create",
    summary="Create or save risk assessment",
)
def create_risk_assessment(
    payload: RiskAssessmentRequest,
):
    try:
        return create_risk_assessment_sync(
            payload
        )

    except Exception as exc:
        _handle_risk_error(exc)


@router.post(
    "/listpage",
    summary="Fetch risk-assessment page data",
)
def get_risk_assessment_listpage(
    payload: RiskAssessmentListPageRequest,
):
    try:
        return risk_assessment_listpage_sync(
            payload
        )

    except Exception as exc:
        _handle_risk_error(exc)
