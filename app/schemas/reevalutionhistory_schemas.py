# app/schemas/reevaluation_history_schema.py

from pydantic import BaseModel


class GetReevaluationHistoryRequest(BaseModel):

    reevaluation_id: int