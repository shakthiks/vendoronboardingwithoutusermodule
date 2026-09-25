from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class RiskAssessmentHeader(BaseModel):
    # Vendor prospect identifier.
    ProspectId: str

    # User who performed the risk assessment.
    AssessedByUserId: Optional[str] = None

    # User who submitted the risk assessment.
    SubmittedByUserId: Optional[str] = None

    # Numeric HIQ_Users.UserId of the user starting approval.
    # Required only when AssessmentStatus is SUBMITTED.
    InitiatedByUserId: Optional[int] = None

    # Supported values:
    # DRAFT, SUBMITTED, RETURNED, REJECTED, RESUBMITTED
    AssessmentStatus: str = "DRAFT"

    # Required for RETURNED and REJECTED.
    Comments: Optional[str] = None
    VendorGroup: Optional[str] = None
    OverallRiskLevel: Optional[str] = None


class RiskAssessmentLine(BaseModel):
    process: str
    identifiedRisk: str
    status: Optional[str]=None
    # applicable: bool
    riskLevel: Optional[str] = None
    remarks: Optional[str] = None


class RiskAssessmentRequest(BaseModel):
    Header: List[RiskAssessmentHeader]
    lines: List[RiskAssessmentLine]


class RiskAssessmentListPageRequest(BaseModel):
    ProspectId: str
