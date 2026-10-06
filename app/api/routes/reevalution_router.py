from fastapi import APIRouter

from app.services.reevaluation_service import (
    get_vendor_reevaluation_home
)


router = APIRouter(
    prefix="/vendor-reevaluation",
    tags=["Vendor Reevaluation"]
)


@router.get("/home")
async def vendor_reevaluation_home():

    return await get_vendor_reevaluation_home()