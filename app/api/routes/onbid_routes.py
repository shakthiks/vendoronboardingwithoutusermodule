from fastapi import APIRouter
from app.services.onbidrfq import fetch_on_bidding_cases,get_vendor_bidrfqs
from app.schemas.rfqcaseschema import VendorRFQRequest

router = APIRouter(prefix="/RFQ",tags=["RFQ"])

@router.post("/rfq/casesonbid")
async def get_rfq_cases():
    try:
        data =await fetch_on_bidding_cases()

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
    
@router.post("/rfq/Vendoronbid")
async def vendor_rfq_id(payload:VendorRFQRequest):
    try:
        data=await get_vendor_bidrfqs(payload.rfq_case_id)
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
    

