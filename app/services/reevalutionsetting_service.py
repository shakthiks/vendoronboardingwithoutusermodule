# app/services/reevaluation_settings_service.py

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import get_connection


# ============================================================
# SCHEMA
# ============================================================

SCHEMA = settings.DB_SCHEMA


# ============================================================
# TABLES
# ============================================================

RISK_PERIOD_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationRiskPeriod"
)

NOTIFICATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationNotificationSetting"
)

CC_MASTER_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationCCMaster"
)

EMAIL_TEMPLATE_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationEmailTemplate"
)


# ============================================================
# HELPERS
# ============================================================

def _rows_to_dict(
    cursor
) -> list[dict[str, Any]]:

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]


def _row_to_dict(
    cursor
) -> dict[str, Any] | None:

    row = cursor.fetchone()

    if not row:
        return None

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return dict(
        zip(columns, row)
    )


# ============================================================
# CONVERT UI PERIOD → MONTHS
#
# 3 Months  -> 3
# 6 Months  -> 6
# 1 Year    -> 12
# 1.5 Years -> 18
# 2 Years   -> 24
# ============================================================

def _period_to_months(
    value: float,
    unit: str,
) -> int:

    normalized_unit = (
        unit
        .strip()
        .upper()
    )

    if normalized_unit in [
        "MONTH",
        "MONTHS",
    ]:

        months = value

    elif normalized_unit in [
        "YEAR",
        "YEARS",
    ]:

        months = value * 12

    else:

        raise ValueError(
            "Unit must be Months or Years."
        )

    months = int(
        round(months)
    )

    if months <= 0:

        raise ValueError(
            "Evaluation period must be greater than zero."
        )

    return months


# ============================================================
# MONTHS → UI VALUE/UNIT
# ============================================================

def _months_to_ui(
    months: int,
):

    if months < 12:

        return {
            "value": months,
            "unit": "Months",
        }

    years = months / 12

    if float(years).is_integer():

        years = int(years)

    return {
        "value": years,
        "unit": "Years",
    }


# ============================================================
# GET COMPLETE SETTINGS PAGE
# ============================================================

def get_reevaluation_settings_sync():

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # REEVALUATION PERIOD
            # =================================================

            cursor.execute(
                f"""
                SELECT
                    RiskPeriodId,
                    RiskLevel,
                    EvaluationPeriodMonths,
                    ReminderDaysBefore

                FROM {RISK_PERIOD_TABLE}

                WHERE IsActive = 1

                ORDER BY
                    CASE UPPER(RiskLevel)

                        WHEN 'CRITICAL'
                            THEN 1

                        WHEN 'HIGH'
                            THEN 2

                        WHEN 'ELEVATED'
                            THEN 3

                        WHEN 'MEDIUM'
                            THEN 4

                        WHEN 'LOW'
                            THEN 5

                        ELSE 6

                    END;
                """
            )

            risk_rows = _rows_to_dict(
                cursor
            )

            reevaluation_periods = []


            for row in risk_rows:

                months = int(
                    row.get(
                        "evaluationperiodmonths"
                    )
                    or 0
                )

                ui_period = (
                    _months_to_ui(
                        months
                    )
                )

                reevaluation_periods.append(
                    {
                        "risk_period_id":
                            row.get(
                                "riskperiodid"
                            ),

                        "risk_level":
                            row.get(
                                "risklevel"
                            ),

                        "value":
                            ui_period["value"],

                        "unit":
                            ui_period["unit"],

                        "evaluation_period_months":
                            months,

                        "reminder_days_before":
                            row.get(
                                "reminderdaysbefore"
                            ),
                    }
                )


            # =================================================
            # NOTIFICATIONS
            # =================================================

            cursor.execute(
                f"""
                SELECT
                    NotificationSettingId,

                    NotificationType,

                    NotifyBeforeValue,

                    NotifyBeforeUnit

                FROM {NOTIFICATION_TABLE}

                WHERE IsActive = 1

                ORDER BY
                    NotificationSettingId;
                """
            )

            notification_rows = (
                _rows_to_dict(
                    cursor
                )
            )


            # =================================================
            # EMAIL CC RECIPIENTS
            # =================================================

            cursor.execute(
                f"""
                SELECT
                    CCMasterId,

                    DisplayName,

                    EmailAddress

                FROM {CC_MASTER_TABLE}

                WHERE IsActive = 1

                ORDER BY
                    CCMasterId;
                """
            )

            recipients = _rows_to_dict(
                cursor
            )


            # =================================================
            # DEFAULT EMAIL TEMPLATE
            # =================================================

            cursor.execute(
                f"""
                SELECT TOP 1

                    TemplateId,

                    TemplateName,

                    Subject,

                    EmailBody

                FROM {EMAIL_TEMPLATE_TABLE}

                WHERE IsActive = 1

                ORDER BY
                    TemplateId DESC;
                """
            )

            email_template = (
                _row_to_dict(
                    cursor
                )
            )


            return {

                "status":
                    True,

                "message":
                    "Reevaluation settings retrieved successfully.",

                "data": {

                    "reevaluation_periods":
                        reevaluation_periods,

                    "notification_settings":
                        notification_rows,

                    "email_recipients":
                        recipients,

                    "email_template":
                        email_template,
                },
            }


        finally:

            cursor.close()


async def get_reevaluation_settings():

    return await run_in_threadpool(
        get_reevaluation_settings_sync
    )


# ============================================================
# UPDATE ALL REEVALUATION PERIODS
# ============================================================

def update_reevaluation_periods_sync(
    payload
):

    allowed_risk_levels = {
        "CRITICAL",
        "HIGH",
        "ELEVATED",
        "MEDIUM",
        "LOW",
    }

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            updated = []


            for item in payload.periods:

                risk_level = (
                    item.risk_level
                    .strip()
                    .upper()
                )


                if (
                    risk_level
                    not in allowed_risk_levels
                ):

                    raise ValueError(
                        f"Invalid risk level: "
                        f"{risk_level}"
                    )


                months = _period_to_months(
                    item.value,
                    item.unit,
                )


                cursor.execute(
                    f"""
                    UPDATE {RISK_PERIOD_TABLE}

                    SET
                        EvaluationPeriodMonths =
                            ?,

                        ModifiedAt =
                            SYSDATETIME(),

                        ModifiedBy =
                            ?

                    WHERE
                        UPPER(RiskLevel) =
                            ?

                        AND IsActive =
                            1;
                    """,

                    months,

                    payload.modified_by,

                    risk_level,
                )


                if cursor.rowcount == 0:

                    raise ValueError(
                        f"Active risk setting "
                        f"not found for "
                        f"{risk_level}."
                    )


                updated.append(
                    {
                        "risk_level":
                            risk_level,

                        "value":
                            item.value,

                        "unit":
                            item.unit,

                        "evaluation_period_months":
                            months,
                    }
                )


            conn.commit()


            return {

                "status":
                    True,

                "message":
                    "Reevaluation periods updated successfully.",

                "data":
                    updated,
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def update_reevaluation_periods(
    payload
):

    return await run_in_threadpool(
        update_reevaluation_periods_sync,
        payload,
    )


# ============================================================
# UPDATE NOTIFICATION SETTINGS
# ============================================================

def update_notification_settings_sync(
    payload
):

    allowed_notifications = {
        "REEVALUATION_DUE",
        "DOCUMENT_EXPIRY",
    }

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            for item in payload.settings:

                notification_type = (
                    item
                    .notification_type
                    .strip()
                    .upper()
                )


                if (
                    notification_type
                    not in allowed_notifications
                ):

                    raise ValueError(
                        f"Invalid notification type: "
                        f"{notification_type}"
                    )


                unit = (
                    item
                    .notify_before_unit
                    .strip()
                    .upper()
                )


                if unit not in [
                    "DAYS",
                    "MONTHS",
                ]:

                    raise ValueError(
                        "Notification unit must "
                        "be Days or Months."
                    )


                cursor.execute(
                    f"""
                    UPDATE {NOTIFICATION_TABLE}

                    SET
                        NotifyBeforeValue =
                            ?,

                        NotifyBeforeUnit =
                            ?,

                        ModifiedAt =
                            SYSDATETIME(),

                        ModifiedBy =
                            ?

                    WHERE
                        UPPER(NotificationType) =
                            ?

                        AND IsActive =
                            1;
                    """,

                    item.notify_before_value,

                    unit,

                    payload.modified_by,

                    notification_type,
                )


                if cursor.rowcount == 0:

                    raise ValueError(
                        f"Notification setting "
                        f"not found for "
                        f"{notification_type}."
                    )


            conn.commit()


            return {
                "status":
                    True,

                "message":
                    "Notification settings updated successfully."
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def update_notification_settings(
    payload
):

    return await run_in_threadpool(
        update_notification_settings_sync,
        payload,
    )


# ============================================================
# ADD EMAIL RECIPIENT
# ============================================================

def add_cc_recipient_sync(
    payload
):

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # CHECK EMAIL EXISTS
            # =================================================

            cursor.execute(
                f"""
                SELECT TOP 1
                    CCMasterId,
                    IsActive

                FROM {CC_MASTER_TABLE}

                WHERE
                    LOWER(
                        LTRIM(
                            RTRIM(
                                EmailAddress
                            )
                        )
                    )
                    =
                    LOWER(
                        LTRIM(
                            RTRIM(?)
                        )
                    )

                ORDER BY
                    CCMasterId DESC;
                """,

                str(
                    payload.email_address
                ),
            )


            existing = cursor.fetchone()


            if existing:

                existing_id = (
                    existing[0]
                )

                is_active = (
                    existing[1]
                )


                if is_active:

                    return {
                        "status": False,

                        "message":
                            "Email recipient already exists."
                    }


                # Reactivate old recipient
                cursor.execute(
                    f"""
                    UPDATE {CC_MASTER_TABLE}

                    SET
                        DisplayName =
                            ?,

                        IsActive =
                            1,

                        ModifiedAt =
                            SYSDATETIME(),

                        ModifiedBy =
                            ?

                    WHERE
                        CCMasterId =
                            ?;
                    """,

                    payload.display_name,

                    payload.created_by,

                    existing_id,
                )


                conn.commit()


                return {
                    "status": True,

                    "message":
                        "Email recipient reactivated successfully.",

                    "data": {
                        "cc_master_id":
                            existing_id,
                    },
                }


            # =================================================
            # INSERT NEW
            # =================================================

            cursor.execute(
                f"""
                INSERT INTO {CC_MASTER_TABLE}
                (
                    DisplayName,

                    EmailAddress,

                    IsActive,

                    CreatedAt,

                    CreatedBy
                )

                OUTPUT
                    INSERTED.CCMasterId

                VALUES
                (
                    ?,
                    ?,
                    1,
                    SYSDATETIME(),
                    ?
                );
                """,

                payload.display_name,

                str(
                    payload.email_address
                ),

                payload.created_by,
            )


            new_id = (
                cursor.fetchone()[0]
            )


            conn.commit()


            return {

                "status":
                    True,

                "message":
                    "Email recipient added successfully.",

                "data": {
                    "cc_master_id":
                        new_id,
                },
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def add_cc_recipient(
    payload
):

    return await run_in_threadpool(
        add_cc_recipient_sync,
        payload,
    )


# ============================================================
# UPDATE EMAIL RECIPIENT
# ============================================================

def update_cc_recipient_sync(
    payload,
):

    cc_master_id = payload.cc_master_id

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # Check another active row with same email
            cursor.execute(
                f"""
                SELECT TOP 1
                    CCMasterId

                FROM {CC_MASTER_TABLE}

                WHERE
                    LOWER(
                        LTRIM(
                            RTRIM(
                                EmailAddress
                            )
                        )
                    )
                    =
                    LOWER(
                        LTRIM(
                            RTRIM(?)
                        )
                    )

                    AND CCMasterId <> ?

                    AND IsActive = 1;
                """,

                str(
                    payload.email_address
                ),

                cc_master_id,
            )


            duplicate = cursor.fetchone()


            if duplicate:

                return {
                    "status": False,

                    "message":
                        "Another active recipient "
                        "already uses this email."
                }


            cursor.execute(
                f"""
                UPDATE {CC_MASTER_TABLE}

                SET
                    DisplayName =
                        ?,

                    EmailAddress =
                        ?,

                    ModifiedAt =
                        SYSDATETIME(),

                    ModifiedBy =
                        ?

                WHERE
                    CCMasterId =
                        ?

                    AND IsActive =
                        1;
                """,

                payload.display_name,

                str(
                    payload.email_address
                ),

                payload.modified_by,

                cc_master_id,
            )


            if cursor.rowcount == 0:

                conn.rollback()

                return {
                    "status":
                        False,

                    "message":
                        "Email recipient not found."
                }


            conn.commit()


            return {
                "status":
                    True,

                "message":
                    "Email recipient updated successfully."
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def update_cc_recipient(
    payload,
):
    return await run_in_threadpool(
        update_cc_recipient_sync,
        payload,
    )

# ============================================================
# REMOVE / INACTIVATE EMAIL RECIPIENT
# ============================================================

def remove_cc_recipient_sync(payload):

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            cursor.execute(
                f"""
                UPDATE {CC_MASTER_TABLE}

                SET
                    IsActive = 0,

                    ModifiedAt = SYSDATETIME(),

                    ModifiedBy = ?

                WHERE
                    CCMasterId = ?

                    AND IsActive = 1;
                """,

                payload.modified_by,

                payload.cc_master_id,
            )


            if cursor.rowcount == 0:

                conn.rollback()

                return {
                    "status": False,
                    "message":
                        "Active email recipient not found."
                }


            conn.commit()


            return {
                "status": True,
                "message":
                    "Email recipient removed successfully.",
                "data": {
                    "cc_master_id":
                        payload.cc_master_id,

                    "is_active":
                        False,
                },
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def remove_cc_recipient(payload):

    return await run_in_threadpool(
        remove_cc_recipient_sync,
        payload,
    )
# ============================================================
# UPDATE EMAIL TEMPLATE
# ============================================================
def update_email_template_sync(payload):

    subject = None
    email_body = None

    modified_by = (
        payload.modified_by
        or "SYSTEM"
    )


    if payload.subject is not None:

        subject = payload.subject.strip()

        if not subject:

            return {
                "status": False,
                "message": "Email subject cannot be empty."
            }


    if payload.email_body is not None:

        email_body = payload.email_body.strip()

        if not email_body:

            return {
                "status": False,
                "message": "Email body cannot be empty."
            }


    if subject is None and email_body is None:

        return {
            "status": False,
            "message":
                "Provide subject or email_body to update."
        }


    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # SUBJECT ONLY
            # =================================================

            if subject is not None and email_body is None:

                cursor.execute(
                    f"""
                    UPDATE {EMAIL_TEMPLATE_TABLE}

                    SET
                        Subject = ?,
                        ModifiedAt = SYSDATETIME(),
                        ModifiedBy = ?

                    WHERE
                        TemplateId = ?
                        AND IsActive = 1;
                    """,

                    subject,
                    modified_by,
                    payload.template_id,
                )


            # =================================================
            # BODY ONLY
            # =================================================

            elif subject is None and email_body is not None:

                cursor.execute(
                    f"""
                    UPDATE {EMAIL_TEMPLATE_TABLE}

                    SET
                        EmailBody = ?,
                        ModifiedAt = SYSDATETIME(),
                        ModifiedBy = ?

                    WHERE
                        TemplateId = ?
                        AND IsActive = 1;
                    """,

                    email_body,
                    modified_by,
                    payload.template_id,
                )


            # =================================================
            # BOTH
            # =================================================

            else:

                cursor.execute(
                    f"""
                    UPDATE {EMAIL_TEMPLATE_TABLE}

                    SET
                        Subject = ?,
                        EmailBody = ?,
                        ModifiedAt = SYSDATETIME(),
                        ModifiedBy = ?

                    WHERE
                        TemplateId = ?
                        AND IsActive = 1;
                    """,

                    subject,
                    email_body,
                    modified_by,
                    payload.template_id,
                )


            if cursor.rowcount == 0:

                conn.rollback()

                return {
                    "status": False,
                    "message":
                        "Active email template not found."
                }


            conn.commit()


            data = {
                "template_id":
                    payload.template_id
            }


            if subject is not None:
                data["subject"] = subject


            if email_body is not None:
                data["email_body"] = email_body


            return {
                "status": True,
                "message":
                    "Email template updated successfully.",
                "data":
                    data
            }


        except Exception:

            conn.rollback()
            raise


        finally:

            cursor.close()


async def update_email_template(payload):

    return await run_in_threadpool(
        update_email_template_sync,
        payload,
    )