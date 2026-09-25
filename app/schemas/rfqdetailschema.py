from pydantic import BaseModel
 
class VendorRFQDetailRequest(BaseModel):
    vendor_account: str
    rfq_id:str
    status:str