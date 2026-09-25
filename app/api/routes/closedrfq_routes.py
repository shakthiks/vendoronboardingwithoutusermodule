from fastapi import APIRouter
from app.services.closedrfq import get_closed_cases,get_vendor_closed_rfqs
from app.schemas.rfqcaseschema import VendorRFQRequest

router = APIRouter(prefix="/RFQ",tags=["RFQ"])

@router.post("/rfq/casesclosed")
async def get_rfq_cases():
    try:
        data =await get_closed_cases()

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
    
@router.post("/rfq/vendorclosed")
async def vendor_rfq_id(payload:VendorRFQRequest):
    try:
        data=await get_vendor_closed_rfqs(payload.rfq_case_id)
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
    

