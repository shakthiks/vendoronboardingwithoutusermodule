# app/api/routes/reevaluation_riskassessment_router.py

from fastapi import APIRouter

from app.schemas.reevalutionriskassesment_schemas import (
    GetReevaluationRiskAssessmentRequest,
    SaveReevaluationRiskAssessmentRequest,
    SendReturnEmailRequest,
)

from app.services.reevalutionriskassesment_services import (
    get_reevaluation_risk_assessment,
    save_reevaluation_risk_assessment,
    send_return_email,
)


router = APIRouter(
    prefix="/vendor-reevaluation/risk-assessment",

    tags=[
        "Vendor Reevaluation Risk Assessment"
    ],
)


# ============================================================
# GET RISK ASSESSMENT
# ============================================================

@router.post("")
async def get_risk_assessment(
    payload: GetReevaluationRiskAssessmentRequest,
):

    return await get_reevaluation_risk_assessment(
        payload.reevaluation_id
    )


# ============================================================
# SAVE DRAFT / RETURN / COMPLETE
# ============================================================

@router.post("/save")
async def save_risk_assessment(
    payload: SaveReevaluationRiskAssessmentRequest,
):

    return await save_reevaluation_risk_assessment(
        payload
    )


# ============================================================
# SEND RETURN EMAIL
# ============================================================

@router.post("/return-email")
async def return_email(
    payload: SendReturnEmailRequest,
):

    return await send_return_email(
        payload
    )