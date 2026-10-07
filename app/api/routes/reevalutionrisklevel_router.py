# app/api/routes/reevaluation_risklevel_router.py

from fastapi import APIRouter

from app.schemas.reevaluationrisklevel_schemas import (
    GetReevaluationRiskLevelRequest,
    UpdateReevaluationRiskLevelRequest,
)

from app.services.reevalutionrisklevel_service import (
    get_reevaluation_risk_level,
    update_reevaluation_risk_level,
)


router = APIRouter(
    prefix="/vendor-reevaluation/risk-level",

    tags=[
        "Vendor Reevaluation Risk Level"
    ],
)


# ============================================================
# GET REEVALUATION PROFILE
# ============================================================

@router.post("")
async def get_risk_level(
    payload: GetReevaluationRiskLevelRequest,
):

    return await get_reevaluation_risk_level(
        payload.reevaluation_id
    )


# ============================================================
# UPDATE CURRENT RISK LEVEL
# ============================================================

@router.post("/update")
async def update_risk_level(
    payload: UpdateReevaluationRiskLevelRequest,
):

    return await update_reevaluation_risk_level(
        payload
    )