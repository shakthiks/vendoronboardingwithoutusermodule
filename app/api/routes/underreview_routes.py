from fastapi import APIRouter
from app.services.underreviewrfq import get_underreview_cases,get_vendor_underreview_rfqs
from app.schemas.rfqcaseschema import VendorRFQRequest
router = APIRouter(prefix="/RFQ",tags=["RFQ"])

@router.post("/rfq/casesunderreview")
async def get_rfq_cases():
    try:
        data = await get_underreview_cases()

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
    
@router.post("/rfq/vendorunderreview")
async def vendor_rfq_id(payload:VendorRFQRequest):
    try:
        data= await get_vendor_underreview_rfqs(payload.rfq_case_id)
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
    

