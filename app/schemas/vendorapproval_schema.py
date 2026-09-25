from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ApprovalFetchRequest(BaseModel):
    ProspectId: str
    UserId: int


class ApprovalSubmitRequest(BaseModel):
    ProspectId: str
    ApprovalAssignmentId: int
    UserId: int
    Decision: ApprovalDecision
    Remarks: Optional[str] = None