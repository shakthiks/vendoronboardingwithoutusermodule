# app/schemas/reevaluation_settings_schema.py

from typing import List, Optional

from pydantic import BaseModel, Field


# ============================================================
# REEVALUATION PERIOD
# ============================================================

class ReevaluationPeriodItem(BaseModel):
    risk_level: str

    value: float = Field(
        ...,
        gt=0,
    )

    unit: str


class UpdateReevaluationPeriodsRequest(BaseModel):
    periods: List[ReevaluationPeriodItem]

    modified_by: str


# ============================================================
# NOTIFICATION SETTINGS
# ============================================================

class NotificationSettingItem(BaseModel):
    notification_type: str

    notify_before_value: float = Field(
        ...,
        ge=0,
    )

    notify_before_unit: str


class UpdateNotificationSettingsRequest(BaseModel):
    settings: List[NotificationSettingItem]

    modified_by: str


# ============================================================
# CC RECIPIENT - ADD
# ============================================================

class AddCCRecipientRequest(BaseModel):
    display_name: str

    email_address: str

    created_by: str


# ============================================================
# CC RECIPIENT - UPDATE
# ============================================================

class UpdateCCRecipientRequest(BaseModel):
    cc_master_id: int

    display_name: Optional[str] = None

    email_address: Optional[str] = None

    modified_by: str


# ============================================================
# EMAIL TEMPLATE
# ============================================================

class UpdateEmailTemplateRequest(BaseModel):
    template_id: int

    subject: Optional[str] = None

    email_body: Optional[str] = None

    modified_by: str