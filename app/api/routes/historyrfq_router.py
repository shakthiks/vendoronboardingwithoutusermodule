from app.services.historyrfq import get_rfq_history,get_rfq_history_detail
from app.schemas.rfqhistory_schema import VendorRFQHisDetailRequest
from fastapi import APIRouter
from app.schemas.rfq_schema import VendorRFQRequest

router = APIRouter(prefix="/RFQ",tags=["RFQ"])
@router.post("/rfqhistory")
async def fetch_rfq_history_header(payload:VendorRFQRequest):
    try:
        data=await get_rfq_history(payload.vendor_account)
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
    

@router.post("/rfqhistoryline")
async def fetch_rfq_history_lines(payload: VendorRFQHisDetailRequest):
    try:
        data =await get_rfq_history_detail(
            payload.rfq_id,          # ← correct order
            payload.vendor_account,
            payload.status
        )
        return {
            "status": "Success",
            "data": data
        }
    except Exception as e:
        return {
            "status": "Failed",
            "message": str(e)
        }