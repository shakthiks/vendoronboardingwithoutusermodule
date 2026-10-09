# app/routers/reevaluation_history_router.py

from fastapi import APIRouter

from app.schemas.reevalutionhistory_schemas import (
    GetReevaluationHistoryRequest,
)

from app.services.reevaluationhistory_service import (
    get_reevaluation_history,
)


router = APIRouter(
    prefix="/vendor-reevaluation/history",
    tags=["Vendor Reevaluation History"],
)


@router.post("/get")
async def get_history(
    payload: GetReevaluationHistoryRequest,
):

    return await get_reevaluation_history(
        payload.reevaluation_id
    )
