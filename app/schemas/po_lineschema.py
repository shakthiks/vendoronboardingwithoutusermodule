from pydantic import BaseModel
class PoDetailRequest(BaseModel):
    purch_id: str
    # vendor_account: str

class PODetails(BaseModel):
    purch_id: str
    vendor_account: str
