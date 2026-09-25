from app.services.vendorlist import vendor_list
from fastapi import APIRouter
from pydantic import BaseModel

router=APIRouter(prefix="/vendorlist",tags=["Vendors"])
@router.post("/list")
async def vendorlist(): 
    data= await vendor_list()
    return {"status":"success","count":len(data),"data":data}