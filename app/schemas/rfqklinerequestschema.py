from pydantic import BaseModel

class RFQCaseStatusRequest(BaseModel):
    rfq_case_id: str