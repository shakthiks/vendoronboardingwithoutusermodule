from pydantic import BaseModel

class VendorMaterialRequest(BaseModel):
    vendor_account: str