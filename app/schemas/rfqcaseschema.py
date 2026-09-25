from pydantic import BaseModel
 
class VendorRFQRequest(BaseModel):
    rfq_case_id: str

