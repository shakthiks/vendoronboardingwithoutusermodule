from app.services.Invite_service import get_vendor_details
from fastapi import APIRouter
from pydantic import BaseModel
from app.schemas.vendormat_schema import VendorMaterialRequest

router=APIRouter(prefix="/vendorlist",tags=["Vendors"])
@router.post("/invite")
async def vendorlist(): 
    data=await get_vendor_details()
    return {"status":"success","count":len(data),"data":data}