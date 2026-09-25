from fastapi import APIRouter
from app.services.rfqcase import (
    get_case_full_details,
    get_rfq_cases,get_case_statuses
)

from app.schemas.rfqcase_schema import RFQStatusRequest

from app.schemas.rfqcase_schema import VendorRFQRequest
from app.schemas.rfqklinerequestschema import RFQCaseStatusRequest
router = APIRouter(
    prefix="/rfq",
    tags=["RFQ"]
)


@router.post("/cases")
async def get_cases(payload: RFQStatusRequest):

    data = await get_rfq_cases(payload.status)

    return {
        "status": "success",
        "count": len(data),
        "data": data
    }


@router.post("/case")
async def get_case_details(payload: VendorRFQRequest):

    data = await get_case_full_details(
        payload.rfq_case_id,
        payload.status
    )

    return {
        "status": "success",
        "data": data
    }
@router.post("/case-status")
async def get_case_status(payload: RFQCaseStatusRequest):

    data = await get_case_statuses(
        payload.rfq_case_id
    )

    return {
        "status": "success",
        "data": data
    }

# from fastapi import APIRouter
# from app.services.rfqcase import get_case_full_details, get_rfq_cases
# from app.schemas.rfqcase_schema import RFQStatusRequest
# from app.schemas.rfqcase_schema import VendorRFQRequest
# router = APIRouter(prefix="/rfq", tags=["RFQ"])

# @router.post("/cases")
# async def get_cases(payload:RFQStatusRequest):
#     data =await get_rfq_cases(payload.status)

#     return {
#         "status": "success",
#         "count": len(data),
#         "data": data
#     }

# @router.post("/case")
# async def get_case_details(payload:VendorRFQRequest):

#     data =await get_case_full_details(payload.rfq_case_id)

#     return {
#         "status": "success",
#         "data": data
#     }
