from fastapi import APIRouter,HTTPException
from app.schemas.vendormat_schema import VendorMaterialRequest
from app.services.vendordetails_service import fetch_bid_materials,fetch_vendor_profile,fetch_vendor_rfqs,fetch_rfq_detail,fetch_po_list,get_po_details,get_po_list,get_vendor_dashboard_kpis
from app.schemas.rfqdetailschema import VendorRFQDetailRequest
from app.schemas.po_lineschema import PODetails
from app.schemas.rfq_schema import VendorRFQRequest
from fastapi.concurrency import run_in_threadpool
router = APIRouter(prefix="/vendor", tags=["Vendors"])
@router.post("/dashboard")
async def vendor_dashboard(payload:VendorMaterialRequest):

    kpis =await get_vendor_dashboard_kpis(payload.vendor_account)
    profile =await fetch_vendor_profile(payload.vendor_account)

    return {
        "vendor_name": profile.get("name"),
        "vendor_account": payload.vendor_account,
        "email": profile.get("email"),
        "phone": profile.get("phone"),
        "address": profile.get("address"),
        "city":profile.get("city"),

        # ✅ KPIs
        "kpis": kpis
    }
@router.post("/bid-materials")

async def get_bid_materials(payload: VendorMaterialRequest):

    materials =await fetch_bid_materials(payload.vendor_account)

    return {
        "status": "success",
        "count": len(materials),
        "materials": materials
    }


@router.post("/profile")
async def get_vendor_profile(payload: VendorMaterialRequest):

    profile =await fetch_vendor_profile(payload.vendor_account)

    return {
        "status": "success",
        "vendor_account": payload.vendor_account,
        "profile": profile
    }


@router.post("/newrfqlist")
async def get_vendor_rfq(payload:VendorRFQRequest):
    data=await fetch_vendor_rfqs(payload.vendor_account)
    return{
        "status":"success",
        "rfqs":data,
        "count":len(data)
    } 

@router.post("/rfq-detail")
async def get_rfq_detail(payload:VendorRFQDetailRequest):
    """
    Input: rfq_id, vendor_account
    """
    return  await fetch_rfq_detail(payload.rfq_id,payload.vendor_account,payload.status)
 

 
@router.post("/list")
async def get_po_list_route(payload: VendorRFQRequest):
    data =await get_po_list(payload.vendor_account)

    return {
        "status": "success",
        "count": len(data),
        "po_list": data
    }

@router.post("/details")
async def get_po_linedetails(payload: PODetails):
    data = await run_in_threadpool(get_po_details, payload.purch_id, payload.vendor_account)
    # data =await get_po_details(
    #     payload.purch_id,
    #     payload.vendor_account
    # )

    if not data:
        raise HTTPException(status_code=404, detail="PO not found")

    return {
        "status": "success",
        "data": data
    }


