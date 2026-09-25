from fastapi import APIRouter

from app.services.expiryrfq import (
    get_expired_cases,
    get_vendor_expired_rfqs
)

from app.schemas.rfqcaseschema import VendorRFQRequest


router = APIRouter(
    prefix="/RFQ",
    tags=["RFQ"]
)


@router.post("/rfq/casesexpired")
async def get_rfq_expired_cases():
    try:
        data = await get_expired_cases()

        return {
            "status": "Success",
            "count": len(data),
            "data": data
        }

    except Exception as e:
        return {
            "status": "Failed",
            "message": str(e)
        }


@router.post("/rfq/vendorexpired")
async def vendor_expired_rfq_detail(
    payload: VendorRFQRequest
):
    try:
        data = await get_vendor_expired_rfqs(
            payload.rfq_case_id
        )

        return {
            "status": "Success",
            "count": len(data.get("vendors", []))
            if isinstance(data, dict)
            else 0,
            "data": data
        }

    except Exception as e:
        return {
            "status": "Failed",
            "message": str(e)
        }