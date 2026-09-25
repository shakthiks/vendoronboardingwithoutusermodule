from pydantic import BaseModel

class VendorRFQRequest(BaseModel):
    vendor_account: str 