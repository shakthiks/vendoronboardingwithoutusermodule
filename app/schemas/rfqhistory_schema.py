from pydantic import BaseModel
 
class VendorRFQHisDetailRequest(BaseModel):
    vendor_account: str
    rfq_id:str
    status: str
