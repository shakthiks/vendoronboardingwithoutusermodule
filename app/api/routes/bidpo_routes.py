from pydantic import BaseModel
from app.services.bidingpo import get_bidding_po_details,get_bidding_po_list,get_invoice_lines
from fastapi import APIRouter, HTTPException
from app.schemas.po_lineschema import PoDetailRequest
router = APIRouter(prefix="/po", tags=["PO"])

@router.post("/bidlist")
async def get_po_bidlist():
    data =await get_bidding_po_list()

    return {
        "status": "success",
        "count": len(data),
        "po_list": data
    } 


@router.post("/biddetails")
async def get_po_bidlinedetails(payload: PoDetailRequest):

    data =await get_bidding_po_details(
        payload.purch_id,
    )

    if not data:
        raise HTTPException(status_code=404, detail="PO not found")

    return {
        "status": "success",
        "data": data
    } 

