from pydantic import BaseModel
from typing import Optional


class VendorReevaluationHomeRequest(BaseModel):
    search: Optional[str] = None