from pydantic import BaseModel

class VendorGroupDropdown(BaseModel):
    VendGroup: str
    Description: str