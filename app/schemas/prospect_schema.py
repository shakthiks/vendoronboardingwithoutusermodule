from pydantic import BaseModel, EmailStr
from typing import Optional

class ProspectInvitationSchema(BaseModel):
    CompanyName: str
    SupplierCategory: Optional[str] = None
    Email: str
    VendGroup: Optional[str] = None


    