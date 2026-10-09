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
#
# ACTION:
# DRAFT
# RETURNED
# COMPLETED
#
# IMPORTANT:
#
# When action = RETURNED,
# frontend sends the return reason in:
#
#     comments
#
# Database stores it in:
#
#     HIQ_VendorRiskAssessment.Comments
#
# Backend returns it to frontend as:
#
#     return_reason
# ============================================================

class SaveReevaluationRiskAssessmentRequest(BaseModel):

    reevaluation_id: int

    action: str

    # Overall risk selected by assessor.
    #
    # Allowed:
    # LOW
    # MEDIUM
    # ELEVATED
    # HIGH
    # CRITICAL
    overall_risk_level: Optional[str] = None

    # --------------------------------------------------------
    # COMMENTS
    #
    # For DRAFT:
    # normal assessment comments
    #
    # For RETURNED:
    # this value becomes the return reason.
    #
    # DB column:
    # HIQ_VendorRiskAssessment.Comments
    # --------------------------------------------------------
    comments: Optional[str] = None

    action_by: str

    details: List[
        ReevaluationRiskAssessmentDetail
    ] = Field(
        default_factory=list
    )


# ============================================================
# RETURN EMAIL
#
# IMPORTANT:
#
# return_reason IS required from frontend here.
#
# Backend stores it into:
#
# HIQ_VendorRiskAssessment.Comments
#
# API response:
#
# "return_reason": "..."
# ============================================================

class SendReturnEmailRequest(BaseModel):

    reevaluation_id: int

    subject: str

    body: str

    to_email: Optional[str] = None

    cc_emails: List[str] = Field(
        default_factory=list
    )

    return_reason: str

    action_by: str