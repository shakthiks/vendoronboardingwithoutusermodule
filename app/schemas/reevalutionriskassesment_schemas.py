# app/schemas/reevalutionriskassesment_schemas.py

from typing import List, Optional

from pydantic import BaseModel, Field


# ============================================================
# GET RISK ASSESSMENT
# ============================================================

class GetReevaluationRiskAssessmentRequest(BaseModel):

    reevaluation_id: int


# ============================================================
# RISK ASSESSMENT DETAIL
# ============================================================

class ReevaluationRiskAssessmentDetail(BaseModel):

    process_name: str

    identified_risk: Optional[str] = None

    is_applicable: Optional[bool] = None

    risk_level: Optional[str] = None

    remarks: Optional[str] = None


# ============================================================
# SAVE RISK ASSESSMENT
# DRAFT / RETURNED / COMPLETED
# ============================================================

class SaveReevaluationRiskAssessmentRequest(BaseModel):

    reevaluation_id: int

    action: str

    # Overall risk selected by the assessor.
    # Example:
    # LOW / MEDIUM / ELEVATED / HIGH / CRITICAL
    overall_risk_level: Optional[str] = None

    comments: Optional[str] = None

    action_by: str

    details: List[
        ReevaluationRiskAssessmentDetail
    ] = Field(
        default_factory=list
    )


# ============================================================
# RETURN EMAIL
# ============================================================

class SendReturnEmailRequest(BaseModel):

    reevaluation_id: int

    subject: str

    body: str

    to_email: Optional[str] = None

    cc_emails: List[str] = Field(
        default_factory=list
    )

    action_by: str