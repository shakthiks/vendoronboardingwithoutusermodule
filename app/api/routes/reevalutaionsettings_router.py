# app/api/routes/reevaluation_settings_router.py

from fastapi import (
    APIRouter,
    Query,
)

from app.schemas.reevaluationsettings_schemas import (
    UpdateReevaluationPeriodsRequest,
    UpdateNotificationSettingsRequest,
    AddCCRecipientRequest,
    UpdateCCRecipientRequest,
    UpdateEmailTemplateRequest,
    RemoveCCRecipientRequest
)

from app.services.reevalutionsetting_service import (
    get_reevaluation_settings,
    update_reevaluation_periods,
    update_notification_settings,
    add_cc_recipient,
    update_cc_recipient,
    update_email_template,
    remove_cc_recipient
)


router = APIRouter(
    prefix="/vendor-reevaluation/settings",
    tags=["Vendor Reevaluation Settings"],
)


# ============================================================
# GET ALL SETTINGS
# ============================================================

@router.get("")
async def get_settings():

    return await get_reevaluation_settings()


# ============================================================
# SAVE / UPDATE REEVALUATION PERIODS
# ============================================================

@router.post("/reevaluation-periods")
async def save_reevaluation_periods(
    payload: UpdateReevaluationPeriodsRequest,
):

    return await update_reevaluation_periods(
        payload
    )


# ============================================================
# SAVE / UPDATE NOTIFICATION SETTINGS
# ============================================================

@router.post("/notifications")
async def save_notifications(
    payload: UpdateNotificationSettingsRequest,
):

    return await update_notification_settings(
        payload
    )


# ============================================================
# ADD CC RECIPIENT
# ============================================================

@router.post("/recipients")
async def create_recipient(
    payload: AddCCRecipientRequest,
):

    return await add_cc_recipient(
        payload
    )


# ============================================================
# UPDATE CC RECIPIENT
# ============================================================

@router.post("/recipients/update")
async def change_recipient(
    payload: UpdateCCRecipientRequest,
):
    return await update_cc_recipient(
        payload
    )


@router.post("/recipients/remove")
async def remove_recipient(
    payload: RemoveCCRecipientRequest,
):

    return await remove_cc_recipient(
        payload
    )

# ============================================================
# SAVE / UPDATE DEFAULT EMAIL TEMPLATE
# ============================================================

@router.post("/email-template")
async def save_email_template(
    payload: UpdateEmailTemplateRequest,
):

    return await update_email_template(
        payload
    )