from app.services.materialsvendor import fetch_materials_with_vendor_count
from app.services.materialsvendor import fetch_material_detail
from app.schemas.materialsschema import Materialslist
from fastapi import APIRouter
router=APIRouter(prefix="/materilslist",tags=["Vendors"])

@router.post("/materials")
async def get_materials():

    items =await fetch_materials_with_vendor_count()
    return {"count": len(items), "data": items} 

@router.post("/materialsdetails")
async def get_material_detail(payload:Materialslist):
    return await fetch_material_detail(payload.item_id)