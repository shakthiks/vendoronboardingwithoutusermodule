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

REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

RISK_PERIOD_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationRiskPeriod"
)

NOTIFICATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationNotificationSetting"
)

ACTION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationAction"
)


# ============================================================
# HELPERS
# ============================================================

def _row_to_dict(
    cursor,
    row,
) -> dict[str, Any]:

    if row is None:
        return {}

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return dict(
        zip(
            columns,
            row,
        )
    )


def _rows_to_dict(
    cursor,
) -> list[dict[str, Any]]:

    if not cursor.description:
        return []

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return [
        dict(
            zip(
                columns,
                row,
            )
        )
        for row in cursor.fetchall()
    ]


# ============================================================
# GET REEVALUATION
# ============================================================

def _get_reevaluation(
    cursor,
    reevaluation_id: int,
):

    cursor.execute(
        f"""
        SELECT
            ReevaluationId,
            ReevaluationNo,
            ProspectId,
            VendorAccount,

            TriggerType,
            TriggeredBy,
            TriggeredAt,

            Status,

            RiskLevel,
            EvaluationPeriodMonths,
            EvaluatedAt,
            NextReevaluationDate,
            ReminderDate,

            CompletedAt,

            IsActive

        FROM {REEVALUATION_TABLE}

        WHERE
            ReevaluationId = ?
            AND IsActive = 1;
        """,

        reevaluation_id,
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return _row_to_dict(
        cursor,
        row,
    )


# ============================================================
# GET RISK PERIOD
# ============================================================

def _get_risk_period(
    cursor,
    risk_level: str,
):

    cursor.execute(
        f"""
        SELECT TOP 1
            RiskPeriodId,
            RiskLevel,
            EvaluationPeriodMonths,
            ReminderDaysBefore

        FROM {RISK_PERIOD_TABLE}

        WHERE
            UPPER(RiskLevel) = ?
            AND IsActive = 1;
        """,

        risk_level,
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return _row_to_dict(
        cursor,
        row,
    )


# ============================================================
# GET REEVALUATION NOTIFICATION SETTING
# ============================================================

def _get_notification_setting(
    cursor,
):

    cursor.execute(
        f"""
        SELECT TOP 1
            NotificationSettingId,
            NotifyBeforeValue,
            NotifyBeforeUnit

        FROM {NOTIFICATION_TABLE}

        WHERE
            UPPER(NotificationType)
                = 'REEVALUATION_DUE'

            AND IsActive = 1

        ORDER BY
            NotificationSettingId DESC;
        """
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return _row_to_dict(
        cursor,
        row,
    )


# ============================================================
# SAVE RISK LEVEL CHANGE HISTORY
# ============================================================

def _save_risk_level_history(
    cursor,
    reevaluation_id: int,
    old_risk_level: str | None,
    new_risk_level: str,
    modified_by: str,
    comments: str | None,
):

    remarks_parts = []

    if old_risk_level:

        remarks_parts.append(
            (
                f"Risk level changed from "
                f"{old_risk_level} to "
                f"{new_risk_level}."
            )
        )

    else:

        remarks_parts.append(
            (
                f"Risk level set to "
                f"{new_risk_level}."
            )
        )

    if comments:

        cleaned_comments = (
            comments.strip()
        )

        if cleaned_comments:

            remarks_parts.append(
                cleaned_comments
            )

    remarks = " ".join(
        remarks_parts
    )

    cursor.execute(
        f"""
        INSERT INTO {ACTION_TABLE}
        (
            ReevaluationId,
            ActionType,

            FromStatus,
            ToStatus,

            Remarks,

            ActionBy,
            ActionAt,

            EmailTriggered
        )

        VALUES
        (
            ?,
            'RISK_LEVEL_CHANGED',
            ?,
            ?,
            ?,
            ?,
            SYSDATETIME(),
            0
        );
        """,

        reevaluation_id,

        old_risk_level,

        new_risk_level,

        remarks,

        modified_by,
    )


# ============================================================
# GET RISK LEVEL HISTORY
# ============================================================

def _get_risk_level_history(
    cursor,
    reevaluation_id: int,
):

    cursor.execute(
        f"""
        SELECT
            ReevaluationActionId,
            ActionAt,
            ActionType,

            FromStatus,
            ToStatus,

            ActionBy,
            Remarks

        FROM {ACTION_TABLE}

        WHERE
            ReevaluationId = ?

            AND ActionType =
                'RISK_LEVEL_CHANGED'

        ORDER BY
            ActionAt DESC;
        """,

        reevaluation_id,
    )

    rows = _rows_to_dict(
        cursor
    )

    history = []

    for row in rows:

        history.append(
            {
                "action_id":
                    row.get(
                        "ReevaluationActionId"
                    ),

                "date":
                    row.get(
                        "actionat"
                    ),

                "action":
                    row.get(
                        "actiontype"
                    ),

                "previous_risk_level":
                    row.get(
                        "fromstatus"
                    ),

                "risk_level":
                    row.get(
                        "tostatus"
                    ),

                "changed_by":
                    row.get(
                        "actionby"
                    ),

                "comments":
                    row.get(
                        "remarks"
                    ),
            }
        )

    return history


# ============================================================
# GET REEVALUATION RISK PROFILE
# ============================================================

def get_reevaluation_risk_level_sync(
    reevaluation_id: int,
):

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            reevaluation = (
                _get_reevaluation(
                    cursor,
                    reevaluation_id,
                )
            )

            if not reevaluation:

                return {
                    "status": False,

                    "message":
                        "Reevaluation not found.",

                    "data": None,
                }

            history = (
                _get_risk_level_history(
                    cursor,
                    reevaluation_id,
                )
            )

            return {
                "status": True,

                "message":
                    (
                        "Reevaluation risk profile "
                        "retrieved successfully."
                    ),

                "data": {

                    "reevaluation_id":
                        reevaluation.get(
                            "reevaluationid"
                        ),

                    "reevaluation_no":
                        reevaluation.get(
                            "reevaluationno"
                        ),

                    "prospect_id":
                        reevaluation.get(
                            "prospectid"
                        ),

                    "vendor_account":
                        reevaluation.get(
                            "vendoraccount"
                        ),

                    "status":
                        reevaluation.get(
                            "status"
                        ),

                    # =============================
                    # CURRENT RISK
                    # =============================

                    "current_risk_level":
                        reevaluation.get(
                            "risklevel"
                        ),

                    "evaluation_period_months":
                        reevaluation.get(
                            "evaluationperiodmonths"
                        ),

                    "evaluated_at":
                        reevaluation.get(
                            "evaluatedat"
                        ),

                    "next_reevaluation_date":
                        reevaluation.get(
                            "nextreevaluationdate"
                        ),

                    "reminder_date":
                        reevaluation.get(
                            "reminderdate"
                        ),

                    # =============================
                    # TRIGGER INFORMATION
                    # =============================

                    "trigger_type":
                        reevaluation.get(
                            "triggertype"
                        ),

                    "triggered_by":
                        reevaluation.get(
                            "triggeredby"
                        ),

                    "triggered_at":
                        reevaluation.get(
                            "triggeredat"
                        ),

                    # =============================
                    # RISK LEVEL HISTORY ONLY
                    # =============================

                    "history":
                        history,
                },
            }

        finally:

            cursor.close()


async def get_reevaluation_risk_level(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_risk_level_sync,
        reevaluation_id,
    )


# ============================================================
# UPDATE REEVALUATION RISK LEVEL
# ============================================================

def update_reevaluation_risk_level_sync(
    payload,
):

    # ========================================================
    # VALIDATE RISK LEVEL
    # ========================================================

    risk_level = (
        payload.risk_level
        .strip()
        .upper()
    )

    allowed_levels = {
        "CRITICAL",
        "HIGH",
        "ELEVATED",
        "MEDIUM",
        "LOW",
    }

    if risk_level not in allowed_levels:

        return {
            "status": False,

            "message":
                (
                    "Risk level must be "
                    "CRITICAL, HIGH, ELEVATED, "
                    "MEDIUM or LOW."
                ),

            "data": None,
        }


    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # GET CURRENT REEVALUATION
            # =================================================

            reevaluation = (
                _get_reevaluation(
                    cursor,
                    payload.reevaluation_id,
                )
            )

            if not reevaluation:

                return {
                    "status": False,

                    "message":
                        "Reevaluation not found.",

                    "data": None,
                }


            # =================================================
            # CANCELLED RECORD CANNOT BE CHANGED
            # =================================================

            current_status = (
                reevaluation.get(
                    "status"
                )
                or ""
            ).strip().upper()

            if current_status == "CANCELLED":

                return {
                    "status": False,

                    "message":
                        (
                            "Risk level cannot be "
                            "changed for a cancelled "
                            "reevaluation."
                        ),

                    "data": None,
                }


            # =================================================
            # CURRENT RISK
            # =================================================

            old_risk_level = (
                reevaluation.get(
                    "risklevel"
                )
            )

            if old_risk_level:

                old_risk_level = (
                    str(
                        old_risk_level
                    )
                    .strip()
                    .upper()
                )


            # =================================================
            # IF SAME RISK LEVEL
            # =================================================

            if old_risk_level == risk_level:

                return {
                    "status": False,

                    "message":
                        (
                            "Selected risk level is "
                            "already the current risk level."
                        ),

                    "data": None,
                }


            # =================================================
            # GET PERIOD FOR SELECTED RISK
            # =================================================

            risk_period = (
                _get_risk_period(
                    cursor,
                    risk_level,
                )
            )

            if not risk_period:

                return {
                    "status": False,

                    "message":
                        (
                            "Active reevaluation period "
                            f"not found for {risk_level}."
                        ),

                    "data": None,
                }


            evaluation_months = int(
                risk_period[
                    "evaluationperiodmonths"
                ]
            )


            # =================================================
            # GET REMINDER CONFIGURATION
            # =================================================

            notification = (
                _get_notification_setting(
                    cursor
                )
            )

            if not notification:

                return {
                    "status": False,

                    "message":
                        (
                            "Active REEVALUATION_DUE "
                            "notification setting "
                            "not found."
                        ),

                    "data": None,
                }


            notify_value = int(
                round(
                    float(
                        notification[
                            "notifybeforevalue"
                        ]
                    )
                )
            )


            notify_unit = (
                str(
                    notification[
                        "notifybeforeunit"
                    ]
                )
                .strip()
                .upper()
            )


            # =================================================
            # REMINDER DATE SQL
            # =================================================

            if notify_unit in {
                "DAY",
                "DAYS",
            }:

                reminder_sql = """
                    DATEADD(
                        DAY,
                        -?,
                        DATEADD(
                            MONTH,
                            ?,
                            CAST(
                                SYSDATETIME()
                                AS DATE
                            )
                        )
                    )
                """

            elif notify_unit in {
                "MONTH",
                "MONTHS",
            }:

                reminder_sql = """
                    DATEADD(
                        MONTH,
                        -?,
                        DATEADD(
                            MONTH,
                            ?,
                            CAST(
                                SYSDATETIME()
                                AS DATE
                            )
                        )
                    )
                """

            else:

                return {
                    "status": False,

                    "message":
                        (
                            "Notification unit must "
                            "be Days or Months."
                        ),

                    "data": None,
                }


            # =================================================
            # UPDATE REEVALUATION RISK PROFILE
            # ============================================================

            cursor.execute(
                f"""
                UPDATE {REEVALUATION_TABLE}

                SET
                    RiskLevel = ?,

                    EvaluationPeriodMonths = ?,

                    EvaluatedAt =
                        SYSDATETIME(),

                    NextReevaluationDate =
                        DATEADD(
                            MONTH,
                            ?,
                            CAST(
                                SYSDATETIME()
                                AS DATE
                            )
                        ),

                    ReminderDate =
                        {reminder_sql},

                    ModifiedAt =
                        SYSDATETIME(),

                    ModifiedBy = ?

                WHERE
                    ReevaluationId = ?

                    AND IsActive = 1;
                """,

                # RiskLevel
                risk_level,

                # EvaluationPeriodMonths
                evaluation_months,

                # NextReevaluationDate
                evaluation_months,

                # ReminderDate
                notify_value,
                evaluation_months,

                # Audit
                payload.modified_by,

                # Reevaluation
                payload.reevaluation_id,
            )


            if cursor.rowcount == 0:

                conn.rollback()

                return {
                    "status": False,

                    "message":
                        "Reevaluation not found.",

                    "data": None,
                }


            # =================================================
            # SAVE HISTORY
            # =================================================

            _save_risk_level_history(
                cursor=cursor,

                reevaluation_id=
                    payload.reevaluation_id,

                old_risk_level=
                    old_risk_level,

                new_risk_level=
                    risk_level,

                modified_by=
                    payload.modified_by,

                comments=
                    payload.comments,
            )


            # =================================================
            # GET UPDATED VALUES
            # =================================================

            cursor.execute(
                f"""
                SELECT
                    RiskLevel,
                    EvaluationPeriodMonths,
                    EvaluatedAt,
                    NextReevaluationDate,
                    ReminderDate

                FROM {REEVALUATION_TABLE}

                WHERE
                    ReevaluationId = ?
                    AND IsActive = 1;
                """,

                payload.reevaluation_id,
            )

            row = cursor.fetchone()


            # =================================================
            # COMMIT
            # =================================================

            conn.commit()


            return {
                "status": True,

                "message":
                    (
                        "Current reevaluation risk level "
                        "updated successfully."
                    ),

                "data": {

                    "reevaluation_id":
                        payload.reevaluation_id,

                    "previous_risk_level":
                        old_risk_level,

                    "current_risk_level":
                        row[0],

                    "evaluation_period_months":
                        row[1],

                    "evaluated_at":
                        row[2],

                    "next_reevaluation_date":
                        row[3],

                    "reminder_date":
                        row[4],
                },
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def update_reevaluation_risk_level(
    payload,
):

    return await run_in_threadpool(
        update_reevaluation_risk_level_sync,
        payload,
    )