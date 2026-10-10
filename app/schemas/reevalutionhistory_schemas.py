# app/schemas/reevaluation_history_schema.py

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class GetReevaluationHistoryRequest(BaseModel):
    reevaluation_id: int


class ReevaluationHistoryRow(BaseModel):
    reevaluation_id: int
    reevaluation_no: Optional[str] = None
    reevaluation_cycle: Optional[int] = None
    date: Optional[datetime] = None

    # Business workflow status for UI
    action_status: Optional[str] = None

    # Technical audit event
    action_type: Optional[str] = None

    assessed_by_id: Optional[str] = None
    assessed_by_name: Optional[str] = None

    trigger_type: Optional[str] = None
    risk_level: Optional[str] = None

    triggered_by_id: Optional[str] = None
    triggered_by_name: Optional[str] = None

    comments: Optional[str] = None


class ReevaluationHistoryData(BaseModel):
    prospect_id: Optional[str] = None
    vendor_account: Optional[str] = None
    history_count: int = 0
    history: List[
        ReevaluationHistoryRow
    ] = Field(
        default_factory=list
    )


class ReevaluationHistoryResponse(BaseModel):
    status: bool
    message: str
    data: Optional[
        ReevaluationHistoryData
    ] = None


class ReevaluationSummaryRequest(BaseModel):
    reevaluation_id: int