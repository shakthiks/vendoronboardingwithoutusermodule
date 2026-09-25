from app.services.dashbaord_service  import get_dashboard
from fastapi import APIRouter
from pydantic import BaseModel
from app.schemas.dashboard_schemas import dashlist

router=APIRouter(prefix="/kpi",tags=["Vendors"])

@router.post("/dashboard")
async def vendorlist(payload:dashlist): 
    data=await get_dashboard(payload.year)
    return {"status":"success","count":len(data),"data":data}