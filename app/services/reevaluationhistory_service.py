# app/services/reevaluation_history_service.py

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_secondary_connection,
)


SCHEMA = settings.DB_SCHEMA

REEVALUATION_TABLE = f"{SCHEMA}.HIQ_VendorReevaluation"
ACTION_TABLE = f"{SCHEMA}.HIQ_VendorReevaluationAction"
USER_TABLE = f"{SCHEMA}.HIQ_Users"


def _row_to_dict(cursor, row) -> dict[str, Any]:
    if row is None:
        return {}

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return dict(zip(columns, row))


def _rows_to_dict(cursor) -> list[dict[str, Any]]:
    if not cursor.description:
        return []

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]


def _format_trigger_type(
    trigger_type: str | None,
) -> str | None:

    if not trigger_type:
        return None

    value = str(trigger_type).strip().upper()

    if value == "AUTO":
        return "Auto Triggered"

    if value == "MANUAL":
        return "Manually Triggered"

    return (
        str(trigger_type)
        .strip()
        .replace("_", " ")
        .title()
    )


def _format_risk_level(
    risk_level: str | None,
) -> str | None:

    if not risk_level:
        return None

    return str(risk_level).strip().title()


def _format_status(
    status: str | None,
) -> str | None:

    if not status:
        return None

    return (
        str(status)
        .strip()
        .replace("_", " ")
        .title()
    )

def _get_user_name(
    user_reference,
) -> str | None:

    if user_reference is None:
        return None

    reference = str(
        user_reference
    ).strip()

    if not reference:
        return None

    # ========================================================
    # SYSTEM USER
    # ========================================================

    if reference.upper() == "SYSTEM":
        return "System"


    try:

        with get_secondary_connection() as conn:

            cursor = conn.cursor()

            try:

                # =================================================
                # CHECK BY:
                #
                # 1. UserId
                # 2. Username
                #
                # Example:
                #
                # ActionBy = 27
                #     → match HIQ_Users.UserId = 27
                #
                # TriggeredBy = Admin
                #     → match HIQ_Users.Username = Admin
                # =================================================

                cursor.execute(
                    f"""
                    SELECT TOP 1

                        UserId,

                        Username,

                        FullName

                    FROM {USER_TABLE}

                    WHERE
                        (
                            CAST(UserId AS VARCHAR(50)) = ?

                            OR

                            Username = ?
                        )

                        AND IsActive = 1;
                    """,

                    reference,
                    reference,
                )


                row = cursor.fetchone()


                if not row:

                    # No user mapping found
                    return reference


                full_name = row[2]


                if full_name:

                    return str(
                        full_name
                    ).strip()


                # If FullName is NULL,
                # fallback to Username

                username = row[1]


                if username:

                    return str(
                        username
                    ).strip()


                return reference


            finally:

                cursor.close()


    except Exception:

        return reference



def _get_cycle_action_summary(
    cursor,
    reevaluation_id: int,
) -> dict[str, Any]:
    """
    Get the most useful action for the cycle.

    Priority:
    RISK_ASSESSED
    VALIDATION_COMPLETED
    COMPLETED
    Otherwise latest action.
    """

    cursor.execute(
        f"""
        SELECT
            ReevaluationActionId,
            ActionType,
            FromStatus,
            ToStatus,
            Remarks,
            ActionBy,
            ActionAt,
            EmailTriggered,
            EmailStatus,
            EmailSentAt,
            EmailError
        FROM {ACTION_TABLE}
        WHERE ReevaluationId = ?
        ORDER BY
            ActionAt DESC,
            ReevaluationActionId DESC;
        """,
        reevaluation_id,
    )

    actions = _rows_to_dict(cursor)

    if not actions:
        return {
            "assessed_by_id": None,
            "assessed_by_name": None,
            "comments": None,
            "action_type": None,
            "action_at": None,
        }

    preferred_actions = [
        "RISK_ASSESSED",
        "VALIDATION_COMPLETED",
        "COMPLETED",
    ]

    selected_action = None

    for preferred in preferred_actions:
        selected_action = next(
            (
                item
                for item in actions
                if (
                    item.get("actiontype")
                    or ""
                ).strip().upper() == preferred
            ),
            None,
        )

        if selected_action:
            break

    if not selected_action:
        selected_action = actions[0]

    action_by_id = selected_action.get("actionby")

    return {
        "assessed_by_id": (
            str(action_by_id)
            if action_by_id is not None
            else None
        ),
        "assessed_by_name": _get_user_name(
            action_by_id
        ),
        "comments": selected_action.get(
            "remarks"
        ),
        "action_type": selected_action.get(
            "actiontype"
        ),
        "action_at": selected_action.get(
            "actionat"
        ),
    }


def get_reevaluation_history_sync(
    reevaluation_id: int,
):
    """
    Returns one row per reevaluation cycle for the same ProspectId.

    UI fields:
    Date
    Action / Status
    Assessed By
    Trigger Type
    Risk Level
    Trigger By
    Comments
    """

    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            # ----------------------------------------------------
            # STEP 1: Resolve the vendor/prospect from input ID
            # ----------------------------------------------------
            cursor.execute(
                f"""
                SELECT TOP 1
                    ReevaluationId,
                    ProspectId,
                    VendorAccount
                FROM {REEVALUATION_TABLE}
                WHERE
                    ReevaluationId = ?
                    AND IsActive = 1;
                """,
                reevaluation_id,
            )

            current_row = cursor.fetchone()

            if not current_row:
                return {
                    "status": False,
                    "message": "Reevaluation not found.",
                    "data": None,
                }

            current = _row_to_dict(
                cursor,
                current_row,
            )

            prospect_id = current.get(
                "prospectid"
            )

            vendor_account = current.get(
                "vendoraccount"
            )

            # ----------------------------------------------------
            # STEP 2: Get every reevaluation cycle for this vendor
            # ----------------------------------------------------
            cursor.execute(
                f"""
                SELECT
                    ReevaluationId,
                    ReevaluationNo,
                    ReevaluationCycle,
                    ProspectId,
                    VendorAccount,
                    TriggerType,
                    TriggerReason,
                    TriggeredBy,
                    TriggeredAt,
                    Status,
                    RiskLevel,
                    EvaluatedAt,
                    CompletedAt,
                    CreatedAt,
                    ModifiedAt
                FROM {REEVALUATION_TABLE}
                WHERE
                    ProspectId = ?
                    AND IsActive = 1
                ORDER BY
                    COALESCE(
                        CompletedAt,
                        EvaluatedAt,
                        TriggeredAt,
                        CreatedAt
                    ) DESC,
                    ReevaluationId DESC;
                """,
                prospect_id,
            )

            reevaluation_rows = _rows_to_dict(
                cursor
            )

            # ----------------------------------------------------
            # STEP 3: Build frontend history rows
            # ----------------------------------------------------
            history = []

            for row in reevaluation_rows:
                cycle_reevaluation_id = row.get(
                    "reevaluationid"
                )

                action_summary = (
                    _get_cycle_action_summary(
                        cursor,
                        cycle_reevaluation_id,
                    )
                )

                history_date = (
                    row.get("completedat")
                    or row.get("evaluatedat")
                    or action_summary.get(
                        "action_at"
                    )
                    or row.get("triggeredat")
                    or row.get("createdat")
                )

                action_status = _format_status(
                    row.get("status")
                )

                assessed_by_id = (
                    action_summary.get(
                        "assessed_by_id"
                    )
                )

                assessed_by_name = (
                    action_summary.get(
                        "assessed_by_name"
                    )
                )

                triggered_by_raw = row.get(
                    "triggeredby"
                )

                triggered_by_id = (
                    str(triggered_by_raw)
                    if triggered_by_raw is not None
                    else None
                )

                trigger_type_raw = (
                    row.get("triggertype")
                    or ""
                ).strip().upper()

                if trigger_type_raw == "AUTO":
                    triggered_by_name = "System"

                    if not triggered_by_id:
                        triggered_by_id = "SYSTEM"
                else:
                    triggered_by_name = (
                        _get_user_name(
                            triggered_by_raw
                        )
                    )

                comments = (
                    action_summary.get(
                        "comments"
                    )
                    or row.get(
                        "triggerreason"
                    )
                )

                history.append(
                    {
                        "reevaluation_id":
                            cycle_reevaluation_id,

                        "reevaluation_no":
                            row.get(
                                "reevaluationno"
                            ),

                        "reevaluation_cycle":
                            row.get(
                                "reevaluationcycle"
                            ),

                        "date":
                            history_date,

                        "action_status":
                            action_status,

                        "action_type":
                            action_summary.get(
                                "action_type"
                            ),

                        "assessed_by_id":
                            assessed_by_id,

                        "assessed_by_name":
                            assessed_by_name,

                        "trigger_type":
                            _format_trigger_type(
                                row.get(
                                    "triggertype"
                                )
                            ),

                        "risk_level":
                            _format_risk_level(
                                row.get(
                                    "risklevel"
                                )
                            ),

                        "triggered_by_id":
                            triggered_by_id,

                        "triggered_by_name":
                            triggered_by_name,

                        "comments":
                            comments,
                    }
                )

            return {
                "status": True,
                "message":
                    "Reevaluation history retrieved successfully.",
                "data": {
                    "prospect_id":
                        prospect_id,

                    "vendor_account":
                        vendor_account,

                    "history_count":
                        len(history),

                    "history":
                        history,
                },
            }

        finally:
            cursor.close()


async def get_reevaluation_history(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_history_sync,
        reevaluation_id,
    )
