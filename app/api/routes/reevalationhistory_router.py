# app/api/routes/reevaluation_history_router.py

from fastapi import APIRouter

from app.schemas.reevalutionhistory_schemas import (
    GetReevaluationHistoryRequest,
    ReevaluationSummaryRequest,
)

from app.services.reevaluationhistory_service import (
    get_reevaluation_history,
    get_reevaluation_summary,
)


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/vendor-reevaluation",
    tags=["Vendor Reevaluation"],
)


# ============================================================
# REEVALUATION HISTORY API
# POST /vendor-reevaluation/history/get
# ============================================================

@router.post("/history/get")
async def get_history(
    payload: GetReevaluationHistoryRequest,
):

    return await get_reevaluation_history(
        payload.reevaluation_id
    )


# ============================================================
# REEVALUATION SUMMARY API
# POST /vendor-reevaluation/summary
# ============================================================

@router.post("/summary")
async def get_summary(
    payload: ReevaluationSummaryRequest,
):

    return await get_reevaluation_summary(
        payload.reevaluation_id
    )