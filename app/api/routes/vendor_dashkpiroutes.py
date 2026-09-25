from app.services.vendor_dashkpi import get_rfq_kpi,get_vendordashboard_kpi
from fastapi import APIRouter
from pydantic import BaseModel


router=APIRouter(prefix="/vendorkpi",tags=["Vendors"])

@router.post("/vendorrfqkpi")
async def vendorlist(): 
    data=await get_rfq_kpi()
    return {"status":"success","count":len(data),"data":data}

@router.post("/vendordashkpi")
async def vendorkpilist(): 
    data= await get_vendordashboard_kpi()
    return {"status":"success","count":len(data),"data":data}

