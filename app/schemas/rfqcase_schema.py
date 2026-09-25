from pydantic import BaseModel

class RFQStatusRequest(BaseModel):
    status: str = "all"

class VendorRFQRequest(BaseModel):
    rfq_case_id: str
    status: str