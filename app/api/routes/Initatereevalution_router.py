from fastapi import APIRouter

from app.schemas.Intaiatereevalution_schemas import (
    InitiateReevaluationRequest,
)

from app.services.Intaiterevaluation_service import (
    initiate_vendor_reevaluation,
)


router = APIRouter(
    prefix="/vendor-reevaluation",
    tags=["Vendor Reevaluation"],
)


@router.post("/initiate")
async def initiate_reevaluation(
    payload: InitiateReevaluationRequest,
):

    return await initiate_vendor_reevaluation(
        payload
    )