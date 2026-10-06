# app/schemas/reevaluation_schema.py

from typing import List, Optional

from pydantic import BaseModel, Field


class BatchEmailOverride(BaseModel):

    use_override: bool = False

    subject: Optional[str] = None

    body: Optional[str] = None


class InitiateReevaluationRequest(BaseModel):

    vendor_accounts: List[str] = None
    trigger_reason: Optional[str] = None

    triggered_by: str

    email_override: Optional[
        BatchEmailOverride
    ] = None