# app/services/reevaluation_history_service.py

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import get_connection


SCHEMA = settings.DB_SCHEMA


REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

ACTION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationAction"
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


def get_reevaluation_history_sync(
    reevaluation_id: int,
):

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # CHECK REEVALUATION
            # =================================================

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
                    CompletedAt

                FROM {REEVALUATION_TABLE}

                WHERE
                    ReevaluationId = ?
                    AND IsActive = 1;
                """,

                reevaluation_id,
            )

            reevaluation_row = (
                cursor.fetchone()
            )

            if not reevaluation_row:

                return {
                    "status": False,

                    "message":
                        "Reevaluation not found.",

                    "data": None,
                }

            reevaluation_columns = [
                column[0].lower()
                for column in cursor.description
            ]

            reevaluation = dict(
                zip(
                    reevaluation_columns,
                    reevaluation_row,
                )
            )


            # =================================================
            # GET COMPLETE ACTION HISTORY
            # =================================================

            cursor.execute(
                f"""
                SELECT
                    ReevaluationActionId,
                    ReevaluationId,
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

                WHERE
                    ReevaluationId = ?

                ORDER BY
                    ActionAt DESC,
                    ReevaluationActionId DESC;
                """,

                reevaluation_id,
            )

            history_rows = (
                _rows_to_dict(
                    cursor
                )
            )


            history = []

            for row in history_rows:

                history.append(
                    {
                        "reevaluation_action_id":
                            row.get(
                                "reevaluationactionid"
                            ),

                        "reevaluation_id":
                            row.get(
                                "reevaluationid"
                            ),

                        "action_type":
                            row.get(
                                "actiontype"
                            ),

                        "from_status":
                            row.get(
                                "fromstatus"
                            ),

                        "to_status":
                            row.get(
                                "tostatus"
                            ),

                        "remarks":
                            row.get(
                                "remarks"
                            ),

                        "action_by":
                            row.get(
                                "actionby"
                            ),

                        "action_at":
                            row.get(
                                "actionat"
                            ),

                        "email_triggered":
                            row.get(
                                "emailtriggered"
                            ),

                        "email_status":
                            row.get(
                                "emailstatus"
                            ),

                        "email_sent_at":
                            row.get(
                                "emailsentat"
                            ),

                        "email_error":
                            row.get(
                                "emailerror"
                            ),
                    }
                )


            return {
                "status": True,

                "message":
                    (
                        "Reevaluation history "
                        "retrieved successfully."
                    ),

                "data": {

                    "reevaluation": {

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

                        "status":
                            reevaluation.get(
                                "status"
                            ),

                        "risk_level":
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

                        "completed_at":
                            reevaluation.get(
                                "completedat"
                            ),
                    },

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