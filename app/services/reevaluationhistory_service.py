# app/services/reevaluation_history_service.py

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_secondary_connection,
)


# ============================================================
# SCHEMA / TABLES
# ============================================================

SCHEMA = settings.DB_SCHEMA

REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

ACTION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationAction"
)

USER_TABLE = (
    f"{SCHEMA}.HIQ_Users"
)


# ============================================================
# COMMON HELPERS
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
# FORMAT HELPERS
# ============================================================

def _format_trigger_type(
    trigger_type: str | None,
) -> str | None:

    if not trigger_type:
        return None

    value = (
        str(trigger_type)
        .strip()
        .upper()
    )

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

    return (
        str(risk_level)
        .strip()
        .title()
    )


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


# ============================================================
# USER NAME LOOKUP
#
# ActionBy may contain:
#   UserId:   27
#   Username: Admin
#   SYSTEM
# ============================================================

def _get_user_name(
    user_reference,
) -> str | None:

    if user_reference is None:
        return None

    reference = (
        str(user_reference)
        .strip()
    )

    if not reference:
        return None

    if reference.upper() == "SYSTEM":
        return "System"

    try:

        with get_secondary_connection() as conn:

            cursor = conn.cursor()

            try:

                cursor.execute(
                    f"""
                    SELECT TOP 1
                        UserId,
                        Username,
                        FullName

                    FROM {USER_TABLE}

                    WHERE
                        (
                            CAST(
                                UserId
                                AS VARCHAR(50)
                            ) = ?

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
                    return reference

                full_name = row[2]

                if full_name:
                    return (
                        str(full_name)
                        .strip()
                    )

                username = row[1]

                if username:
                    return (
                        str(username)
                        .strip()
                    )

                return reference

            finally:

                cursor.close()

    except Exception:

        # Do not break history API if user-name lookup fails.
        return reference


# ============================================================
# GET ALL REEVALUATION CYCLES FOR PROSPECT
# ============================================================

def _get_reevaluation_cycles(
    cursor,
    prospect_id: str,
) -> list[dict[str, Any]]:

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
            ReevaluationId ASC;
        """,
        prospect_id,
    )

    return _rows_to_dict(
        cursor
    )


# ============================================================
# GET RISK HISTORY ACTIONS FOR ONE REEVALUATION
#
# Existing DB design:
#
# RISK_LEVEL_CHANGED:
#   FromStatus = previous risk
#   ToStatus   = new risk
#
# Example:
#   CRITICAL -> HIGH
#
# RISK_ASSESSED:
#   FromStatus / ToStatus are workflow statuses.
# ============================================================

def _get_risk_history_actions(
    cursor,
    reevaluation_id: int,
) -> list[dict[str, Any]]:

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
            AND ActionType IN
            (
                'RISK_LEVEL_CHANGED',
                'RISK_ASSESSED'
            )

        ORDER BY
            ActionAt ASC,
            ReevaluationActionId ASC;
        """,
        reevaluation_id,
    )

    return _rows_to_dict(
        cursor
    )


# ============================================================
# BUILD HISTORY FOR ONE REEVALUATION CYCLE
#
# Output format remains exactly like your existing API.
#
# Every:
#   RISK_LEVEL_CHANGED
#   RISK_ASSESSED
#
# becomes its own row in history[].
# ============================================================

def _build_cycle_history(
    cursor,
    cycle: dict[str, Any],
) -> list[dict[str, Any]]:

    cycle_reevaluation_id = int(
        cycle["reevaluationid"]
    )

    actions = (
        _get_risk_history_actions(
            cursor,
            cycle_reevaluation_id,
        )
    )

    history: list[
        dict[str, Any]
    ] = []

    # Tracks the risk that was active while processing
    # historical events from oldest -> newest.
    known_risk_level = None

    # ------------------------------------------------------------
    # TRIGGERED BY
    # ------------------------------------------------------------

    triggered_by_raw = (
        cycle.get(
            "triggeredby"
        )
    )

    triggered_by_id = (
        str(triggered_by_raw)
        if triggered_by_raw is not None
        else None
    )

    trigger_type_raw = (
        cycle.get(
            "triggertype"
        )
        or ""
    ).strip().upper()

    if trigger_type_raw == "AUTO":

        triggered_by_name = (
            "System"
        )

        if not triggered_by_id:
            triggered_by_id = (
                "SYSTEM"
            )

    else:

        triggered_by_name = (
            _get_user_name(
                triggered_by_raw
            )
        )

    # ------------------------------------------------------------
    # BUILD ONE HISTORY ROW FOR EACH ACTION
    # ------------------------------------------------------------

    for action in actions:

        action_type = (
            action.get(
                "actiontype"
            )
            or ""
        ).strip().upper()

        action_by_raw = (
            action.get(
                "actionby"
            )
        )

        assessed_by_id = (
            str(action_by_raw)
            if action_by_raw is not None
            else None
        )

        assessed_by_name = (
            _get_user_name(
                action_by_raw
            )
        )

        # ========================================================
        # RISK LEVEL CHANGED
        #
        # FromStatus = old risk
        # ToStatus   = new risk
        #
        # Example:
        #   HIGH -> ELEVATED
        #
        # Output:
        #   risk_level = Elevated
        # ========================================================

        if action_type == "RISK_LEVEL_CHANGED":

            new_risk_level_raw = (
                action.get(
                    "tostatus"
                )
            )

            risk_level = (
                _format_risk_level(
                    new_risk_level_raw
                )
            )

            if new_risk_level_raw:

                known_risk_level = (
                    str(
                        new_risk_level_raw
                    )
                    .strip()
                    .upper()
                )

            action_status = (
                "Risk Level Changed"
            )

        # ========================================================
        # RISK ASSESSED
        #
        # FromStatus / ToStatus are workflow statuses here.
        #
        # Use the latest risk value that existed before this
        # assessment action.
        # ========================================================

        elif action_type == "RISK_ASSESSED":

            risk_level = (
                _format_risk_level(
                    known_risk_level
                )
            )

            # Fallback only:
            # if there was no previous RISK_LEVEL_CHANGED event,
            # use the stored RiskLevel from reevaluation.
            if not risk_level:

                risk_level = (
                    _format_risk_level(
                        cycle.get(
                            "risklevel"
                        )
                    )
                )

            action_status = (
                _format_status(
                    action.get(
                        "tostatus"
                    )
                )
                or "Completed"
            )

        else:

            continue

        history.append(
            {
                "reevaluation_id":
                    cycle_reevaluation_id,

                "reevaluation_no":
                    cycle.get(
                        "reevaluationno"
                    ),

                "reevaluation_cycle":
                    cycle.get(
                        "reevaluationcycle"
                    ),

                "date":
                    action.get(
                        "actionat"
                    ),

                "action_status":
                    action_status,

                "action_type":
                    action_type,

                "assessed_by_id":
                    assessed_by_id,

                "assessed_by_name":
                    assessed_by_name,

                "trigger_type":
                    _format_trigger_type(
                        cycle.get(
                            "triggertype"
                        )
                    ),

                "risk_level":
                    risk_level,

                "triggered_by_id":
                    triggered_by_id,

                "triggered_by_name":
                    triggered_by_name,

                "comments":
                    (
                        action.get(
                            "remarks"
                        )
                        or cycle.get(
                            "triggerreason"
                        )
                    ),
            }
        )

    return history


# ============================================================
# MAIN HISTORY SERVICE
#
# Input:
#   reevaluation_id
#
# Output:
#   current_risk_level
#   history_count
#   history[]
#
# history[] contains every RISK_LEVEL_CHANGED and RISK_ASSESSED
# action as a separate row.
# ============================================================

def get_reevaluation_history_sync(
    reevaluation_id: int,
):

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # ====================================================
            # STEP 1
            # GET CURRENT / SELECTED REEVALUATION
            # ====================================================

            cursor.execute(
                f"""
                SELECT TOP 1
                    ReevaluationId,
                    ProspectId,
                    VendorAccount,
                    RiskLevel

                FROM {REEVALUATION_TABLE}

                WHERE
                    ReevaluationId = ?
                    AND IsActive = 1;
                """,
                reevaluation_id,
            )

            current_row = (
                cursor.fetchone()
            )

            if not current_row:

                return {
                    "status": False,

                    "message":
                        "Reevaluation not found.",

                    "data": None,
                }

            current = (
                _row_to_dict(
                    cursor,
                    current_row,
                )
            )

            prospect_id = (
                current.get(
                    "prospectid"
                )
            )

            vendor_account = (
                current.get(
                    "vendoraccount"
                )
            )

            current_risk_level = (
                _format_risk_level(
                    current.get(
                        "risklevel"
                    )
                )
            )

            # ====================================================
            # STEP 2
            # GET ALL REEVALUATION CYCLES FOR THIS PROSPECT
            # ====================================================

            reevaluation_rows = (
                _get_reevaluation_cycles(
                    cursor,
                    prospect_id,
                )
            )

            # ====================================================
            # STEP 3
            # BUILD COMPLETE EVENT-LEVEL HISTORY
            # ====================================================

            history: list[
                dict[str, Any]
            ] = []

            for cycle in reevaluation_rows:

                cycle_history = (
                    _build_cycle_history(
                        cursor,
                        cycle,
                    )
                )

                history.extend(
                    cycle_history
                )

            # ====================================================
            # STEP 4
            # NEWEST EVENT FIRST
            # ====================================================

            history.sort(
                key=lambda item: (
                    item.get(
                        "date"
                    )
                    is not None,

                    item.get(
                        "date"
                    ),
                ),
                reverse=True,
            )

            # ====================================================
            # FINAL RESPONSE
            # ====================================================

            return {
                "status": True,

                "message":
                    (
                        "Reevaluation history "
                        "retrieved successfully."
                    ),

                "data": {

                    "prospect_id":
                        prospect_id,

                    "vendor_account":
                        vendor_account,

                    "current_risk_level":
                        current_risk_level,

                    "history_count":
                        len(
                            history
                        ),

                    "history":
                        history,
                },
            }

        finally:

            cursor.close()


# ============================================================
# ASYNC WRAPPER
# ============================================================

async def get_reevaluation_history(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_history_sync,
        reevaluation_id,
    )





# ============================================================
# TABLES
# ============================================================

SCHEMA = settings.DB_SCHEMA

REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

PROSPECT_TABLE = (
    f"{SCHEMA}.d365_VendorProspect"
)



# ============================================================
# SUMMARY HELPERS
# ============================================================

def _format_risk_level(
    risk_level: str | None,
) -> str | None:

    if not risk_level:
        return None

    return (
        str(risk_level)
        .strip()
        .title()
    )


def _format_home_status(
    status: str | None,
) -> str:

    if not status:
        return "Not Started"

    value = (
        str(status)
        .strip()
        .upper()
    )

    if value in {
        "PENDING",
        "MAIL_SENT",
        "IN_PROGRESS",
    }:
        return "Awaiting Response"

    if value in {
        "SUBMITTED",
        "UNDER_REVIEW",
        "RESUBMITTED",
    }:
        return "Validation In Progress"

    if value in {
        "RETURNED",
        "RESUBMISSION_REQUESTED",
    }:
        return "Returned"

    if value == "COMPLETED":
        return "Completed"

    if value == "CANCELLED":
        return "Cancelled"

    return (
        str(status)
        .strip()
        .replace("_", " ")
        .title()
    )


def _to_date_string(
    value,
) -> str | None:

    if value is None:
        return None

    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:
            pass

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


# ============================================================
# MAIN SUMMARY SERVICE
# ============================================================

def get_reevaluation_summary_sync(
    reevaluation_id: int,
) -> dict[str, Any]:

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            cursor.execute(
                f"""
                SELECT TOP 1

                    R.ReevaluationId,
                    R.ReevaluationNo,
                    R.ProspectId,
                    R.VendorAccount,

                    P.Email,

                    R.RiskLevel,
                    R.Status,

                    (
                        SELECT TOP 1

                            COALESCE(
                                PR.CompletedAt,
                                PR.EvaluatedAt,
                                PR.SubmittedAt,
                                PR.TriggeredAt,
                                PR.CreatedAt
                            )

                        FROM {REEVALUATION_TABLE} AS PR

                        WHERE
                            PR.ProspectId = R.ProspectId

                            AND PR.ReevaluationId
                                < R.ReevaluationId

                            AND PR.IsActive = 1

                        ORDER BY
                            PR.ReevaluationId DESC

                    ) AS LastReevaluation,

                    R.NextReevaluationDate,

                    R.PreviousReevaluationId,

                    R.ReevaluationCycle

                FROM {REEVALUATION_TABLE} AS R

                LEFT JOIN {PROSPECT_TABLE} AS P
                    ON P.ProspectId = R.ProspectId

                WHERE
                    R.ReevaluationId = ?
                    AND R.IsActive = 1;
                """,
                reevaluation_id,
            )

            row = cursor.fetchone()

            if not row:

                return {
                    "status": False,
                    "message": "Reevaluation not found.",
                    "data": None,
                }

            data = _row_to_dict(
                cursor,
                row,
            )

            raw_status = (
                data.get(
                    "status"
                )
            )

            return {
                "status": True,

                "message":
                    (
                        "Reevaluation summary "
                        "retrieved successfully."
                    ),

                "data": {

                    "reevaluation_id":
                        data.get(
                            "reevaluationid"
                        ),

                    "reevaluation_no":
                        data.get(
                            "reevaluationno"
                        ),

                    "reevaluation_cycle":
                        data.get(
                            "reevaluationcycle"
                        ),

                    "vendor_account":
                        data.get(
                            "vendoraccount"
                        ),

                    "prospect_id":
                        data.get(
                            "prospectid"
                        ),

                    "email":
                        data.get(
                            "email"
                        ),

                    "risk_level":
                        _format_risk_level(
                            data.get(
                                "risklevel"
                            )
                        ),

                    "last_reevaluation":
                        _to_date_string(
                            data.get(
                                "lastreevaluation"
                            )
                        ),

                    "next_reevaluation":
                        _to_date_string(
                            data.get(
                                "nextreevaluationdate"
                            )
                        ),

                    # Raw DB workflow status
                    "status":
                        raw_status,

                    # Same mapped status used in home UI
                    "status_display":
                        _format_home_status(
                            raw_status
                        ),

                    "previous_reevaluation_id":
                        data.get(
                            "previousreevaluationid"
                        ),
                },
            }

        finally:

            cursor.close()


# ============================================================
# ASYNC WRAPPER
# ============================================================

async def get_reevaluation_summary(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_summary_sync,
        reevaluation_id,
    )
#  .# app/services/reevaluation_history_service.py

# from __future__ import annotations

# from typing import Any

# from fastapi.concurrency import run_in_threadpool

# from app.core.config import settings
# from app.db.base import (
#     get_connection,
#     get_secondary_connection,
# )


# # ============================================================
# # SCHEMA / TABLES
# # ============================================================

# SCHEMA = settings.DB_SCHEMA

# REEVALUATION_TABLE = (
#     f"{SCHEMA}.HIQ_VendorReevaluation"
# )

# ACTION_TABLE = (
#     f"{SCHEMA}.HIQ_VendorReevaluationAction"
# )

# USER_TABLE = (
#     f"{SCHEMA}.HIQ_Users"
# )


# # ============================================================
# # COMMON HELPERS
# # ============================================================

# def _row_to_dict(
#     cursor,
#     row,
# ) -> dict[str, Any]:

#     if row is None:
#         return {}

#     columns = [
#         column[0].lower()
#         for column in cursor.description
#     ]

#     return dict(
#         zip(
#             columns,
#             row,
#         )
#     )


# def _rows_to_dict(
#     cursor,
# ) -> list[dict[str, Any]]:

#     if not cursor.description:
#         return []

#     columns = [
#         column[0].lower()
#         for column in cursor.description
#     ]

#     return [
#         dict(
#             zip(
#                 columns,
#                 row,
#             )
#         )
#         for row in cursor.fetchall()
#     ]


# # ============================================================
# # FORMAT HELPERS
# # ============================================================

# def _format_trigger_type(
#     trigger_type: str | None,
# ) -> str | None:

#     if not trigger_type:
#         return None

#     value = (
#         str(trigger_type)
#         .strip()
#         .upper()
#     )

#     if value == "AUTO":
#         return "Auto Triggered"

#     if value == "MANUAL":
#         return "Manually Triggered"

#     return (
#         str(trigger_type)
#         .strip()
#         .replace("_", " ")
#         .title()
#     )


# def _format_risk_level(
#     risk_level: str | None,
# ) -> str | None:

#     if not risk_level:
#         return None

#     return (
#         str(risk_level)
#         .strip()
#         .title()
#     )


# def _format_status(
#     status: str | None,
# ) -> str | None:

#     if not status:
#         return None

#     return (
#         str(status)
#         .strip()
#         .replace("_", " ")
#         .title()
#     )


# # ============================================================
# # USER NAME LOOKUP
# #
# # ActionBy may contain UserId like:
# #   27
# #
# # TriggeredBy may contain Username like:
# #   Admin
# #
# # So check both UserId and Username.
# # ============================================================

# def _get_user_name(
#     user_reference,
# ) -> str | None:

#     if user_reference is None:
#         return None

#     reference = (
#         str(user_reference)
#         .strip()
#     )

#     if not reference:
#         return None

#     # SYSTEM generated action
#     if reference.upper() == "SYSTEM":
#         return "System"

#     try:

#         with get_secondary_connection() as conn:

#             cursor = conn.cursor()

#             try:

#                 cursor.execute(
#                     f"""
#                     SELECT TOP 1
#                         UserId,
#                         Username,
#                         FullName

#                     FROM {USER_TABLE}

#                     WHERE
#                         (
#                             CAST(
#                                 UserId
#                                 AS VARCHAR(50)
#                             ) = ?

#                             OR

#                             Username = ?
#                         )

#                         AND IsActive = 1;
#                     """,
#                     reference,
#                     reference,
#                 )

#                 row = cursor.fetchone()

#                 if not row:
#                     return reference

#                 full_name = row[2]

#                 if full_name:
#                     return (
#                         str(full_name)
#                         .strip()
#                     )

#                 username = row[1]

#                 if username:
#                     return (
#                         str(username)
#                         .strip()
#                     )

#                 return reference

#             finally:

#                 cursor.close()

#     except Exception:

#         # Do not break history API if name lookup fails.
#         return reference


# # ============================================================
# # GET BEST ACTION FOR ONE REEVALUATION CYCLE
# #
# # Used to get:
# #   assessed_by_id
# #   assessed_by_name
# #   comments
# #   action_type
# #   action_at
# # ============================================================

# def _get_cycle_action_summary(
#     cursor,
#     reevaluation_id: int,
# ) -> dict[str, Any]:

#     cursor.execute(
#         f"""
#         SELECT
#             ReevaluationActionId,
#             ActionType,
#             FromStatus,
#             ToStatus,
#             Remarks,
#             ActionBy,
#             ActionAt,
#             EmailTriggered,
#             EmailStatus,
#             EmailSentAt,
#             EmailError

#         FROM {ACTION_TABLE}

#         WHERE
#             ReevaluationId = ?

#         ORDER BY
#             ActionAt DESC,
#             ReevaluationActionId DESC;
#         """,
#         reevaluation_id,
#     )

#     actions = (
#         _rows_to_dict(
#             cursor
#         )
#     )

#     if not actions:

#         return {
#             "assessed_by_id": None,
#             "assessed_by_name": None,
#             "comments": None,
#             "action_type": None,
#             "action_at": None,
#         }

#     # Prefer assessment/completion actions.
#     preferred_actions = [
#         "RISK_ASSESSED",
#         "VALIDATION_COMPLETED",
#         "COMPLETED",
#     ]

#     selected_action = None

#     for preferred in preferred_actions:

#         selected_action = next(
#             (
#                 item
#                 for item in actions
#                 if (
#                     item.get(
#                         "actiontype"
#                     )
#                     or ""
#                 ).strip().upper()
#                 == preferred
#             ),
#             None,
#         )

#         if selected_action:
#             break

#     # If no preferred action exists,
#     # use the latest action.
#     if not selected_action:
#         selected_action = actions[0]

#     action_by_id = (
#         selected_action.get(
#             "actionby"
#         )
#     )

#     return {
#         "assessed_by_id":
#             (
#                 str(action_by_id)
#                 if action_by_id is not None
#                 else None
#             ),

#         "assessed_by_name":
#             _get_user_name(
#                 action_by_id
#             ),

#         "comments":
#             selected_action.get(
#                 "remarks"
#             ),

#         "action_type":
#             selected_action.get(
#                 "actiontype"
#             ),

#         "action_at":
#             selected_action.get(
#                 "actionat"
#             ),
#     }


# # ============================================================
# # MAIN HISTORY SERVICE
# #
# # Input:
# #   reevaluation_id
# #
# # Output:
# #   current_risk_level
# #   plus one history row per reevaluation cycle.
# # ============================================================

# def get_reevaluation_history_sync(
#     reevaluation_id: int,
# ):

#     with get_connection() as conn:

#         cursor = conn.cursor()

#         try:

#             # ====================================================
#             # STEP 1
#             # GET CURRENT REEVALUATION
#             #
#             # RiskLevel is included here so the API can return:
#             # current_risk_level
#             # ====================================================

#             cursor.execute(
#                 f"""
#                 SELECT TOP 1
#                     ReevaluationId,
#                     ProspectId,
#                     VendorAccount,
#                     RiskLevel

#                 FROM {REEVALUATION_TABLE}

#                 WHERE
#                     ReevaluationId = ?
#                     AND IsActive = 1;
#                 """,
#                 reevaluation_id,
#             )

#             current_row = (
#                 cursor.fetchone()
#             )

#             if not current_row:

#                 return {
#                     "status": False,
#                     "message":
#                         "Reevaluation not found.",
#                     "data": None,
#                 }

#             current = (
#                 _row_to_dict(
#                     cursor,
#                     current_row,
#                 )
#             )

#             prospect_id = (
#                 current.get(
#                     "prospectid"
#                 )
#             )

#             vendor_account = (
#                 current.get(
#                     "vendoraccount"
#                 )
#             )

#             current_risk_level = (
#                 _format_risk_level(
#                     current.get(
#                         "risklevel"
#                     )
#                 )
#             )

#             # ====================================================
#             # STEP 2
#             # GET ALL REEVALUATION CYCLES
#             #
#             # Each cycle keeps its own RiskLevel.
#             # ====================================================

#             cursor.execute(
#                 f"""
#                 SELECT
#                     ReevaluationId,
#                     ReevaluationNo,
#                     ReevaluationCycle,
#                     ProspectId,
#                     VendorAccount,
#                     TriggerType,
#                     TriggerReason,
#                     TriggeredBy,
#                     TriggeredAt,
#                     Status,
#                     RiskLevel,
#                     EvaluatedAt,
#                     CompletedAt,
#                     CreatedAt,
#                     ModifiedAt

#                 FROM {REEVALUATION_TABLE}

#                 WHERE
#                     ProspectId = ?
#                     AND IsActive = 1

#                 ORDER BY
#                     COALESCE(
#                         CompletedAt,
#                         EvaluatedAt,
#                         TriggeredAt,
#                         CreatedAt
#                     ) DESC,
#                     ReevaluationId DESC;
#                 """,
#                 prospect_id,
#             )

#             reevaluation_rows = (
#                 _rows_to_dict(
#                     cursor
#                 )
#             )

#             # ====================================================
#             # STEP 3
#             # BUILD FRONTEND HISTORY
#             # ====================================================

#             history = []

#             for row in reevaluation_rows:

#                 cycle_reevaluation_id = (
#                     row.get(
#                         "reevaluationid"
#                     )
#                 )

#                 action_summary = (
#                     _get_cycle_action_summary(
#                         cursor,
#                         cycle_reevaluation_id,
#                     )
#                 )

#                 # -----------------------------------------------
#                 # DATE
#                 # -----------------------------------------------

#                 history_date = (
#                     row.get(
#                         "completedat"
#                     )
#                     or row.get(
#                         "evaluatedat"
#                     )
#                     or action_summary.get(
#                         "action_at"
#                     )
#                     or row.get(
#                         "triggeredat"
#                     )
#                     or row.get(
#                         "createdat"
#                     )
#                 )

#                 # -----------------------------------------------
#                 # ACTION / STATUS
#                 # -----------------------------------------------

#                 action_status = (
#                     _format_status(
#                         row.get(
#                             "status"
#                         )
#                     )
#                 )

#                 # -----------------------------------------------
#                 # ASSESSED BY
#                 # -----------------------------------------------

#                 assessed_by_id = (
#                     action_summary.get(
#                         "assessed_by_id"
#                     )
#                 )

#                 assessed_by_name = (
#                     action_summary.get(
#                         "assessed_by_name"
#                     )
#                 )

#                 # -----------------------------------------------
#                 # TRIGGERED BY
#                 # -----------------------------------------------

#                 triggered_by_raw = (
#                     row.get(
#                         "triggeredby"
#                     )
#                 )

#                 triggered_by_id = (
#                     str(
#                         triggered_by_raw
#                     )
#                     if triggered_by_raw is not None
#                     else None
#                 )

#                 trigger_type_raw = (
#                     row.get(
#                         "triggertype"
#                     )
#                     or ""
#                 ).strip().upper()

#                 if trigger_type_raw == "AUTO":

#                     triggered_by_name = (
#                         "System"
#                     )

#                     if not triggered_by_id:
#                         triggered_by_id = (
#                             "SYSTEM"
#                         )

#                 else:

#                     triggered_by_name = (
#                         _get_user_name(
#                             triggered_by_raw
#                         )
#                     )

#                 # -----------------------------------------------
#                 # COMMENTS
#                 # Prefer action remarks.
#                 # Fallback to trigger reason.
#                 # -----------------------------------------------

#                 comments = (
#                     action_summary.get(
#                         "comments"
#                     )
#                     or row.get(
#                         "triggerreason"
#                     )
#                 )

#                 # -----------------------------------------------
#                 # HISTORY ROW
#                 # -----------------------------------------------

#                 history.append(
#                     {
#                         "reevaluation_id":
#                             cycle_reevaluation_id,

#                         "reevaluation_no":
#                             row.get(
#                                 "reevaluationno"
#                             ),

#                         "reevaluation_cycle":
#                             row.get(
#                                 "reevaluationcycle"
#                             ),

#                         "date":
#                             history_date,

#                         "action_status":
#                             action_status,

#                         "action_type":
#                             action_summary.get(
#                                 "action_type"
#                             ),

#                         "assessed_by_id":
#                             assessed_by_id,

#                         "assessed_by_name":
#                             assessed_by_name,

#                         "trigger_type":
#                             _format_trigger_type(
#                                 row.get(
#                                     "triggertype"
#                                 )
#                             ),

#                         # Risk level that belonged
#                         # to this particular cycle.
#                         "risk_level":
#                             _format_risk_level(
#                                 row.get(
#                                     "risklevel"
#                                 )
#                             ),

#                         "triggered_by_id":
#                             triggered_by_id,

#                         "triggered_by_name":
#                             triggered_by_name,

#                         "comments":
#                             comments,
#                     }
#                 )

#             # ====================================================
#             # FINAL RESPONSE
#             # ====================================================

#             return {
#                 "status": True,

#                 "message":
#                     (
#                         "Reevaluation history "
#                         "retrieved successfully."
#                     ),

#                 "data": {

#                     "prospect_id":
#                         prospect_id,

#                     "vendor_account":
#                         vendor_account,

#                     # Current selected/latest reevaluation
#                     # risk level.
#                     "current_risk_level":
#                         current_risk_level,

#                     "history_count":
#                         len(
#                             history
#                         ),

#                     "history":
#                         history,
#                 },
#             }

#         finally:

#             cursor.close()


# # ============================================================
# # ASYNC WRAPPER
# # ============================================================

# async def get_reevaluation_history(
#     reevaluation_id: int,
# ):

#     return await run_in_threadpool(
#         get_reevaluation_history_sync,
#         reevaluation_id,
#     )
