from pydantic import BaseModel
from app.services.directpo import get_direct_po_list,get_direct_po_details,get_invoice_lines
from app.schemas.invoice_schema import Invoicelist
from fastapi import APIRouter, HTTPException
from app.schemas.po_lineschema import PoDetailRequest
router = APIRouter(prefix="/po", tags=["PO"])

@router.post("/directlist")
async def get_po_dirlist():
    data =await get_direct_po_list()

    return {
        "status": "success",
        "count": len(data),
        "po_list": data
    } 

@router.post("/directdetails")
async def get_po_dirlinedetails(payload: PoDetailRequest):

    data = await get_direct_po_details(
        payload.purch_id,
    )

    if not data:
        raise HTTPException(status_code=404, detail="PO not found")

    return {
        "status": "success",
        "data": data
    }
# @router.post("/directdetails")
# def get_po_dirlinedetails(payload: PoDetailRequest):

#     data = get_direct_po_details(
#         payload.purch_id,
#     )

#     if not data:
#         raise HTTPException(status_code=404, detail="PO not found")

#     return {
#         "status": "success",
#         "data": data
#     }


