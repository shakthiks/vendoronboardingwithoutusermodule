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


PROSPECT_TABLE = (
    f"{SCHEMA}.d365_VendorProspect"
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


def _normalize_risk_level(
    risk_level: str | None,
) -> str | None:

    if not risk_level:
        return None

    value = (
        str(risk_level)
        .strip()
        .upper()
    )

    valid_risk_levels = {
        "CRITICAL",
        "HIGH",
        "ELEVATED",
        "MEDIUM",
        "LOW",
    }

    if value not in valid_risk_levels:
        return None

    return value


# ============================================================
# USER NAME LOOKUP
#
# ActionBy / TriggeredBy can contain:
#
# UserId
# Username
# SYSTEM
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

        # Do not break history API
        # if secondary user lookup fails.
        return reference


# ============================================================
# GET ALL REEVALUATION CYCLES
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
# GET RISK HISTORY ACTIONS
#
# RISK_LEVEL_CHANGED:
#
# FromStatus = previous risk
# ToStatus   = new risk
#
# RISK_ASSESSED:
#
# FromStatus / ToStatus are workflow statuses
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
# FIND HISTORICAL RISK FOR RISK_ASSESSED
#
# Used mainly for old records.
#
# If no previous RISK_LEVEL_CHANGED exists, look ahead for:
#
# RISK_LEVEL_CHANGED
# FromStatus = historical old risk
#
# Example:
#
# RISK_ASSESSED
# then later
# ELEVATED -> LOW
#
# Therefore assessment-time risk was ELEVATED.
#
# IMPORTANT:
# Never use current Reevaluation.RiskLevel here.
# ============================================================

def _find_next_previous_risk(
    actions: list[dict[str, Any]],
    current_index: int,
) -> str | None:

    for next_index in range(
        current_index + 1,
        len(actions),
    ):

        next_action = actions[next_index]

        next_action_type = (
            next_action.get(
                "actiontype"
            )
            or ""
        ).strip().upper()

        if next_action_type != "RISK_LEVEL_CHANGED":
            continue

        previous_risk = (
            _normalize_risk_level(
                next_action.get(
                    "fromstatus"
                )
            )
        )

        if previous_risk:
            return previous_risk

        # If the first following risk change is:
        #
        # NULL -> ELEVATED
        #
        # we cannot safely know what the earlier
        # RISK_ASSESSED value was.
        return None

    return None


# ============================================================
# BUILD HISTORY FOR ONE REEVALUATION CYCLE
#
# History must be immutable.
#
# Current RiskLevel must NEVER be used to reconstruct
# previous history.
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


    # ========================================================
    # HISTORICAL RISK TRACKER
    #
    # This only changes based on ACTION history.
    #
    # Never initialize from:
    #
    # cycle["risklevel"]
    #
    # because that is the current mutable value.
    # ========================================================

    known_risk_level = None


    # ========================================================
    # TRIGGERED BY
    # ========================================================

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


    # ========================================================
    # PROCESS ACTIONS
    #
    # Actions are already ordered:
    #
    # oldest -> newest
    # ========================================================

    for index, action in enumerate(
        actions
    ):

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


        # ====================================================
        # RISK LEVEL CHANGED
        #
        # FromStatus = previous risk
        # ToStatus   = new risk
        # ====================================================

        if action_type == "RISK_LEVEL_CHANGED":

            old_risk_level_raw = (
                _normalize_risk_level(
                    action.get(
                        "fromstatus"
                    )
                )
            )

            new_risk_level_raw = (
                _normalize_risk_level(
                    action.get(
                        "tostatus"
                    )
                )
            )


            # -----------------------------------------------
            # If tracker is empty and this action contains
            # a FromStatus risk, we now know the previous
            # historical risk.
            # -----------------------------------------------

            if (
                known_risk_level is None
                and old_risk_level_raw
            ):

                known_risk_level = (
                    old_risk_level_raw
                )


            # -----------------------------------------------
            # History row displays NEW risk.
            # -----------------------------------------------

            risk_level = (
                _format_risk_level(
                    new_risk_level_raw
                )
            )


            # -----------------------------------------------
            # Update tracker.
            # -----------------------------------------------

            if new_risk_level_raw:

                known_risk_level = (
                    new_risk_level_raw
                )


            action_status = (
                "Risk Level Changed"
            )


        # ====================================================
        # RISK ASSESSED
        #
        # DO NOT USE:
        #
        # cycle["risklevel"]
        #
        # because that is today's/current risk.
        # ====================================================

        elif action_type == "RISK_ASSESSED":

            historical_risk = (
                known_risk_level
            )


            # -----------------------------------------------
            # Support old data.
            #
            # If no previous risk-change action exists,
            # infer from the FromStatus of the next
            # RISK_LEVEL_CHANGED action.
            # -----------------------------------------------

            if not historical_risk:

                historical_risk = (
                    _find_next_previous_risk(
                        actions,
                        index,
                    )
                )


            risk_level = (
                _format_risk_level(
                    historical_risk
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


        # ====================================================
        # COMMENTS
        # ====================================================

        comments = (
            action.get(
                "remarks"
            )
            or cycle.get(
                "triggerreason"
            )
        )


        # ====================================================
        # ADD HISTORY ROW
        # ====================================================

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
                    comments,
            }
        )


    return history


# ============================================================
# MAIN HISTORY SERVICE
# ============================================================

def get_reevaluation_history_sync(
    reevaluation_id: int,
):

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # ====================================================
            # STEP 1
            # GET CURRENT REEVALUATION
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


            # ====================================================
            # CURRENT RISK
            #
            # Current Risk Level card SHOULD use current DB value.
            # This is not history.
            # ====================================================

            current_risk_level = (
                _format_risk_level(
                    current.get(
                        "risklevel"
                    )
                )
            )


            # ====================================================
            # STEP 2
            # GET ALL REEVALUATION CYCLES
            # ====================================================

            reevaluation_rows = (
                _get_reevaluation_cycles(
                    cursor,
                    prospect_id,
                )
            )


            # ====================================================
            # STEP 3
            # BUILD COMPLETE HISTORY
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
            # NEWEST FIRST
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
            # RESPONSE
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
# ASYNC HISTORY WRAPPER
# ============================================================

async def get_reevaluation_history(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_history_sync,
        reevaluation_id,
    )


# ============================================================
# SUMMARY HELPERS
# ============================================================

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

    if hasattr(
        value,
        "date",
    ):

        try:

            return (
                value
                .date()
                .isoformat()
            )

        except Exception:

            pass


    if hasattr(
        value,
        "isoformat",
    ):

        return value.isoformat()


    return str(
        value
    )


# ============================================================
# REEVALUATION SUMMARY
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
                    "message":
                        "Reevaluation not found.",
                    "data": None,
                }


            data = (
                _row_to_dict(
                    cursor,
                    row,
                )
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

                    "status":
                        raw_status,

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
# ASYNC SUMMARY WRAPPER
# ============================================================

async def get_reevaluation_summary(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_summary_sync,
        reevaluation_id,
    )