from pydantic import BaseModel
from app.services.po_service import get_admin_po_list,get_admin_po_details,get_admin_po_summary,get_invoice_lines
from fastapi import APIRouter, HTTPException
from app.schemas.po_lineschema import PoDetailRequest
from app.schemas.invoice_schema import Invoicelist
router = APIRouter(prefix="/po", tags=["PO"])

@router.post("/list")
async def get_po_alllist():
    data =await get_admin_po_list()

    return {
        "status": "success",
        "count": len(data),
        "po_list": data
    } 


@router.post("/details")
async def get_po_linedetails(payload: PoDetailRequest):

    data =await get_admin_po_details(
        payload.purch_id,
    )

    if not data:
        raise HTTPException(status_code=404, detail="PO not found")

    return {
        "status": "success",
        "data": data
    }

@router.post("/kpi")
async def po_kpi():
    data=await get_admin_po_summary()
    return {
        "status": "success",
        "data": data
    } 

@router.post("/lines")
async def invoice_lines(payload:Invoicelist):
    data =await get_invoice_lines(payload.invoice_id)

    return {
        "status": "success",
        "count": len(data),
        "data": data
    }
