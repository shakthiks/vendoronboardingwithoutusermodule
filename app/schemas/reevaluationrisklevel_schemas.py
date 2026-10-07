# app/schemas/reevaluation_risklevel_schema.py

from typing import Optional

from pydantic import BaseModel


# ============================================================
# GET PROFILE
# ============================================================

class GetReevaluationRiskLevelRequest(BaseModel):

    reevaluation_id: int


# ============================================================
# UPDATE CURRENT RISK LEVEL
# ============================================================

class UpdateReevaluationRiskLevelRequest(BaseModel):

    reevaluation_id: int

    risk_level: str

    modified_by: str

    comments: Optional[str] = None