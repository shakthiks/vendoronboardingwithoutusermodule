# app/services/reevalutionriskassesment_services.py

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

from app.db.base import (
    get_connection,
    get_secondary_connection,
)
from app.utils.reevalution_email_template import (
    build_reevaluation_email_html,
)
from app.services.email_service import send_email
# ============================================================
# SCHEMA
# ============================================================

SCHEMA = settings.DB_SCHEMA


# ============================================================
# PRIMARY DATABASE TABLES
# get_connection()
# ============================================================

REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

ACTION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationAction"
)

CC_MASTER_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationCCMaster"
)

VENDOR_PROSPECT_TABLE = (
    f"{SCHEMA}.d365_VendorProspect"
)


# ============================================================
# SECONDARY DATABASE TABLES
# get_secondary_connection()
# ============================================================

RISK_ASSESSMENT_TABLE = (
    f"{SCHEMA}.HIQ_VendorRiskAssessment"
)

RISK_DETAIL_TABLE = (
    f"{SCHEMA}.HIQ_VendorRiskAssessmentDetail"
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
def _format_risk_level(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    return value.capitalize()
def _complete_reevaluation(
    cursor,
    reevaluation_id: int,
    risk_assessment_id: int,
    modified_by: str,
):

    cursor.execute(
        f"""
        UPDATE {REEVALUATION_TABLE}

        SET
            RiskAssessmentId = ?,

            Status = 'COMPLETED',

            CompletedAt =
                SYSDATETIME(),

            ModifiedAt =
                SYSDATETIME(),

            ModifiedBy = ?

        WHERE
            ReevaluationId = ?

            AND IsActive = 1;
        """,

        risk_assessment_id,

        modified_by,

        reevaluation_id,
    )


    if cursor.rowcount == 0:

        raise ValueError(
            "Active reevaluation not found."
        )
# ============================================================
# PRIMARY DB
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
            Status,
            RiskAssessmentId,
            RiskLevel,
            EvaluationPeriodMonths,
            EvaluatedAt,
            NextReevaluationDate,
            ReminderDate,
            CompletedAt,
            ToEmail

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
# SECONDARY DB
# GET EXISTING RISK ASSESSMENT
# ============================================================

def _get_risk_assessment(
    cursor,
    reevaluation_id: int,
):

    cursor.execute(
        f"""
        SELECT TOP 1
            RiskAssessmentId,
            ReevaluationId,
            ProspectId,
            AssessedByUserId,
            SubmittedByUserId,
            AssessmentStatus,
            CreatedAt,
            ModifiedAt,
            SubmittedAt,
            Comments,
            VendorGroup,
            OverallRiskLevel

        FROM {RISK_ASSESSMENT_TABLE}

        WHERE
            ReevaluationId = ?

        ORDER BY
            RiskAssessmentId DESC;
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
# SECONDARY DB
# GET RISK DETAILS
# ============================================================

def _get_risk_details(
    cursor,
    risk_assessment_id: int,
):

    cursor.execute(
        f"""
        SELECT
            RiskAssessmentDetailId,
            RiskAssessmentId,
            ProspectId,
            ProcessName,
            IdentifiedRisk,
            IsApplicable,
            RiskLevel,
            Remarks,
            CreatedAt,
            ModifiedAt

        FROM {RISK_DETAIL_TABLE}

        WHERE
            RiskAssessmentId = ?

        ORDER BY
            RiskAssessmentDetailId;
        """,
        risk_assessment_id,
    )

    rows = _rows_to_dict(
        cursor
    )

    return [
        {
            "risk_assessment_detail_id":
                row.get(
                    "riskassessmentdetailid"
                ),

            "risk_assessment_id":
                row.get(
                    "riskassessmentid"
                ),

            "prospect_id":
                row.get(
                    "prospectid"
                ),

            "process_name":
                row.get(
                    "processname"
                ),

            "identified_risk":
                row.get(
                    "identifiedrisk"
                ),

            "is_applicable":
                row.get(
                    "isapplicable"
                ),

           "risk_level":
                _format_risk_level(
                    row.get(
                        "risklevel"
                    )
                ),

            "remarks":
                row.get(
                    "remarks"
                ),

            "created_at":
                row.get(
                    "createdat"
                ),

            "modified_at":
                row.get(
                    "modifiedat"
                ),
        }
        for row in rows
    ]


# ============================================================
# SECONDARY DB
# CREATE RISK ASSESSMENT HEADER
# ============================================================

def _create_risk_assessment(
    cursor,
    reevaluation,
    action: str,
    action_by: str,
    comments: str | None,
    overall_risk_level: str | None = None,
):

    cursor.execute(
        f"""
        INSERT INTO {RISK_ASSESSMENT_TABLE}
        (
            ProspectId,
            ReevaluationId,
            AssessedByUserId,
            AssessmentStatus,
            OverallRiskLevel,
            CreatedAt,
            ModifiedAt,
            Comments
        )

        OUTPUT
            INSERTED.RiskAssessmentId

        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            SYSDATETIME(),
            SYSDATETIME(),
            ?
        );
        """,

        reevaluation[
            "prospectid"
        ],

        reevaluation[
            "reevaluationid"
        ],

        action_by,

        action,

        overall_risk_level,

        comments,
    )

    row = cursor.fetchone()

    if row is None:

        raise ValueError(
            "Unable to create risk assessment."
        )

    return int(
        row[0]
    )


# ============================================================
# SECONDARY DB
# UPDATE RISK ASSESSMENT HEADER
# ============================================================

def _update_risk_assessment(
    cursor,
    risk_assessment_id: int,
    action: str,
    action_by: str,
    comments: str | None,
    overall_risk_level: str | None = None,
):

    # ========================================================
    # COMPLETE
    # ========================================================

    if action == "COMPLETED":

        cursor.execute(
            f"""
            UPDATE {RISK_ASSESSMENT_TABLE}

            SET
                AssessedByUserId = ?,

                SubmittedByUserId = ?,

                AssessmentStatus =
                    'COMPLETED',

                OverallRiskLevel = ?,

                Comments = ?,

                ModifiedAt =
                    SYSDATETIME(),

                SubmittedAt =
                    SYSDATETIME()

            WHERE
                RiskAssessmentId = ?;
            """,

            action_by,

            action_by,

            overall_risk_level,

            comments,

            risk_assessment_id,
        )

        return


    # ========================================================
    # DRAFT / RETURNED
    # ========================================================

    cursor.execute(
        f"""
        UPDATE {RISK_ASSESSMENT_TABLE}

        SET
            AssessedByUserId = ?,

            AssessmentStatus = ?,

            OverallRiskLevel =
                COALESCE(
                    ?,
                    OverallRiskLevel
                ),

            Comments = ?,

            ModifiedAt =
                SYSDATETIME()

        WHERE
            RiskAssessmentId = ?;
        """,

        action_by,

        action,

        overall_risk_level,

        comments,

        risk_assessment_id,
    )


# ============================================================
# SECONDARY DB
# SAVE RISK DETAIL ROWS
# ============================================================

def _save_risk_details(
    cursor,
    risk_assessment_id: int,
    prospect_id: str,
    details,
):

    # Current working state:
    # remove old detail rows and insert latest UI values.

    cursor.execute(
        f"""
        DELETE FROM {RISK_DETAIL_TABLE}

        WHERE
            RiskAssessmentId = ?;
        """,
        risk_assessment_id,
    )


    for item in details:

        process_name = (
            item.process_name
            or ""
        ).strip()


        if not process_name:

            raise ValueError(
                "Process name is required."
            )


        risk_level = None


        if item.risk_level:

            risk_level = (
                item.risk_level
                .strip()
                .upper()
            )


        cursor.execute(
            f"""
            INSERT INTO {RISK_DETAIL_TABLE}
            (
                RiskAssessmentId,
                ProspectId,
                ProcessName,
                IdentifiedRisk,
                IsApplicable,
                RiskLevel,
                Remarks,
                CreatedAt,
                ModifiedAt
            )

            VALUES
            (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                SYSDATETIME(),
                SYSDATETIME()
            );
            """,

            risk_assessment_id,

            prospect_id,

            process_name,

            item.identified_risk,

            item.is_applicable,

            risk_level,

            item.remarks,
        )


# ============================================================
# VALIDATE COMPLETE
# ============================================================

def _validate_completed_assessment(
    details,
):

    if not details:

        return (
            False,
            "Risk assessment details are required."
        )


    allowed_levels = {
        "LOW",
        "MEDIUM",
        "ELEVATED",
        "HIGH",
        "CRITICAL",
    }


    for item in details:

        process_name = (
            item.process_name
            or "Risk assessment row"
        )


        if item.is_applicable is None:

            return (
                False,
                (
                    "Applicable selection is required "
                    f"for {process_name}."
                ),
            )


        if item.is_applicable is True:

            if not item.risk_level:

                return (
                    False,
                    (
                        "Risk level is required "
                        f"for {process_name}."
                    ),
                )


            risk_level = (
                item.risk_level
                .strip()
                .upper()
            )


            if (
                risk_level
                not in allowed_levels
            ):

                return (
                    False,
                    (
                        f"Invalid risk level "
                        f"'{risk_level}' for "
                        f"{process_name}."
                    ),
                )


    return (
        True,
        None,
    )


# ============================================================
# CALCULATE OVERALL RISK
#
# Only stores OverallRiskLevel in RiskAssessment.
# Does NOT calculate reevaluation dates.
# ============================================================

def _calculate_overall_risk(
    details,
):

    ranking = {
        "LOW": 1,
        "MEDIUM": 2,
        "ELEVATED": 3,
        "HIGH": 4,
        "CRITICAL": 5,
    }


    highest_score = 0

    overall_risk = None


    for item in details:

        if item.is_applicable is not True:
            continue


        if not item.risk_level:
            continue


        risk_level = (
            item.risk_level
            .strip()
            .upper()
        )


        if risk_level not in ranking:

            raise ValueError(
                (
                    f"Invalid risk level "
                    f"'{risk_level}' for "
                    f"{item.process_name}."
                )
            )


        score = ranking[
            risk_level
        ]


        if score > highest_score:

            highest_score = score

            overall_risk = risk_level


    return overall_risk


# ============================================================
# PRIMARY DB
# UPDATE REEVALUATION WORKFLOW STATUS
# ============================================================

def _update_reevaluation_status(
    cursor,
    reevaluation_id: int,
    risk_assessment_id: int,
    status: str,
    modified_by: str,
):

    cursor.execute(
        f"""
        UPDATE {REEVALUATION_TABLE}

        SET
            RiskAssessmentId = ?,

            Status = ?,

            ModifiedAt =
                SYSDATETIME(),

            ModifiedBy = ?

        WHERE
            ReevaluationId = ?

            AND IsActive = 1;
        """,

        risk_assessment_id,

        status,

        modified_by,

        reevaluation_id,
    )


    if cursor.rowcount == 0:

        raise ValueError(
            "Active reevaluation not found."
        )


# ============================================================
# PRIMARY DB
# UPDATE ONLY RiskAssessmentId
# KEEP EXISTING REEVALUATION STATUS
# ============================================================

def _link_risk_assessment(
    cursor,
    reevaluation_id: int,
    risk_assessment_id: int,
    modified_by: str,
):

    cursor.execute(
        f"""
        UPDATE {REEVALUATION_TABLE}

        SET
            RiskAssessmentId = ?,

            ModifiedAt =
                SYSDATETIME(),

            ModifiedBy = ?

        WHERE
            ReevaluationId = ?

            AND IsActive = 1;
        """,

        risk_assessment_id,

        modified_by,

        reevaluation_id,
    )


    if cursor.rowcount == 0:

        raise ValueError(
            "Active reevaluation not found."
        )


# ============================================================
# PRIMARY DB
# ACTION HISTORY
# ============================================================

def _save_action(
    cursor,
    reevaluation_id: int,
    action_type: str,
    from_status: str | None,
    to_status: str | None,
    action_by: str,
    remarks: str | None = None,
):

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
            ?,
            ?,
            ?,
            ?,
            ?,
            SYSDATETIME(),
            0
        );
        """,

        reevaluation_id,

        action_type,

        from_status,

        to_status,

        remarks,

        action_by,
    )


# ============================================================
# GET RISK ASSESSMENT
#
# Primary:
#   Reevaluation
#
# Secondary:
#   RiskAssessment
#   RiskAssessmentDetail
# ============================================================

def get_reevaluation_risk_assessment_sync(
    reevaluation_id: int,
):

    # ========================================================
    # PRIMARY DB
    # ========================================================

    with get_connection() as primary_conn:

        primary_cursor = (
            primary_conn.cursor()
        )

        try:

            reevaluation = (
                _get_reevaluation(
                    primary_cursor,
                    reevaluation_id,
                )
            )

        finally:

            primary_cursor.close()


    if not reevaluation:

        return {
            "status": False,

            "message":
                "Reevaluation not found.",

            "data": None,
        }


    # ========================================================
    # SECONDARY DB
    # ========================================================

    with get_secondary_connection() as secondary_conn:

        secondary_cursor = (
            secondary_conn.cursor()
        )

        try:

            assessment = (
                _get_risk_assessment(
                    secondary_cursor,
                    reevaluation_id,
                )
            )


            if not assessment:

                return {
                    "status": True,

                    "message":
                        "Risk assessment not started.",

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

                            "status":
                                reevaluation.get(
                                    "status"
                                ),
                        },

                        "assessment":
                            None,

                        "details":
                            [],
                    },
                }


            risk_assessment_id = int(
                assessment[
                    "riskassessmentid"
                ]
            )


            details = (
                _get_risk_details(
                    secondary_cursor,
                    risk_assessment_id,
                )
            )


        finally:

            secondary_cursor.close()


    return {
        "status": True,

        "message":
            (
                "Risk assessment "
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

                "status":
                    reevaluation.get(
                        "status"
                    ),
            },

            "assessment": {

                "risk_assessment_id":
                    assessment.get(
                        "riskassessmentid"
                    ),

                "assessment_status":
                    assessment.get(
                        "assessmentstatus"
                    ),

                "assessed_by_user_id":
                    assessment.get(
                        "assessedbyuserid"
                    ),

                "submitted_by_user_id":
                    assessment.get(
                        "submittedbyuserid"
                    ),


                "comments":
                    assessment.get(
                        "comments"
                    ),

                "return_reason":
                    (
                        assessment.get(
                            "comments"
                        )
                        if (
                            assessment.get(
                                "assessmentstatus"
                            )
                            or ""
                        ).strip().upper() == "RETURNED"
                        else None
                    ),

                "overall_risk_level":
                    assessment.get(
                        "overallrisklevel"
                    ),

                "created_at":
                    assessment.get(
                        "createdat"
                    ),

                "modified_at":
                    assessment.get(
                        "modifiedat"
                    ),

                "submitted_at":
                    assessment.get(
                        "submittedat"
                    ),
            },

            "details":
                details,
        },
    }


async def get_reevaluation_risk_assessment(
    reevaluation_id: int,
):

    return await run_in_threadpool(
        get_reevaluation_risk_assessment_sync,
        reevaluation_id,
    )


# ============================================================
# SAVE RISK ASSESSMENT
# ============================================================
# ============================================================
# SAVE RISK ASSESSMENT
# ============================================================

def save_reevaluation_risk_assessment_sync(
    payload,
):

    # ========================================================
    # ACTION
    # ========================================================

    action = (
        payload.action
        .strip()
        .upper()
    )

    allowed_actions = {
        "DRAFT",
        "RETURNED",
        "COMPLETED",
    }

    if action not in allowed_actions:

        return {
            "status": False,
            "message": (
                "Action must be DRAFT, "
                "RETURNED or COMPLETED."
            ),
            "data": None,
        }


    # ========================================================
    # COMPLETED VALIDATION
    # ========================================================

    if action == "COMPLETED":

        valid, message = (
            _validate_completed_assessment(
                payload.details
            )
        )

        if not valid:

            return {
                "status": False,
                "message": message,
                "data": None,
            }


    # ========================================================
    # STEP 1
    # PRIMARY DB
    # READ REEVALUATION
    # ========================================================

    with get_connection() as primary_conn:

        primary_cursor = (
            primary_conn.cursor()
        )

        try:

            reevaluation = (
                _get_reevaluation(
                    primary_cursor,
                    payload.reevaluation_id,
                )
            )

        finally:

            primary_cursor.close()


    if not reevaluation:

        return {
            "status": False,
            "message": "Reevaluation not found.",
            "data": None,
        }


    # ========================================================
    # CURRENT REEVALUATION STATUS
    # ========================================================

    current_status = (
        reevaluation.get("status")
        or ""
    ).strip().upper()


    # ========================================================
    # CANCELLED REEVALUATION CANNOT BE ASSESSED
    # ========================================================

    if current_status == "CANCELLED":

        return {
            "status": False,
            "message": (
                "Cancelled reevaluation "
                "cannot be assessed."
            ),
            "data": None,
        }


    # ========================================================
    # OVERALL RISK LEVEL
    # ========================================================

    overall_risk_level = None

    if payload.overall_risk_level:

        overall_risk_level = (
            payload.overall_risk_level
            .strip()
            .upper()
        )


    allowed_overall_risk_levels = {
        "LOW",
        "MEDIUM",
        "ELEVATED",
        "HIGH",
        "CRITICAL",
    }


    # ========================================================
    # VALIDATE OVERALL RISK LEVEL
    # ========================================================

    if overall_risk_level:

        if (
            overall_risk_level
            not in allowed_overall_risk_levels
        ):

            return {
                "status": False,
                "message": (
                    "Overall risk level must be "
                    "LOW, MEDIUM, ELEVATED, "
                    "HIGH or CRITICAL."
                ),
                "data": None,
            }


    # ========================================================
    # COMPLETED REQUIRES OVERALL RISK LEVEL
    # ========================================================

    if action == "COMPLETED":

        if not overall_risk_level:

            return {
                "status": False,
                "message": (
                    "Overall risk level is required "
                    "when action is COMPLETED."
                ),
                "data": None,
            }


    # ========================================================
    # STEP 2
    # SECONDARY DB
    #
    # SAVE:
    #   RiskAssessment
    #   RiskAssessmentDetail
    # ========================================================

    with get_secondary_connection() as secondary_conn:

        secondary_cursor = (
            secondary_conn.cursor()
        )

        try:

            # =================================================
            # CHECK EXISTING ASSESSMENT
            # =================================================

            assessment = (
                _get_risk_assessment(
                    secondary_cursor,
                    payload.reevaluation_id,
                )
            )


            # =================================================
            # CREATE NEW RISK ASSESSMENT
            # =================================================

            if not assessment:

                risk_assessment_id = (
                    _create_risk_assessment(
                        cursor=secondary_cursor,
                        reevaluation=reevaluation,
                        action=action,
                        action_by=payload.action_by,
                        comments=payload.comments,
                        overall_risk_level=
                            overall_risk_level,
                    )
                )


                # =============================================
                # If first save itself is COMPLETED,
                # update SubmittedByUserId / SubmittedAt.
                # =============================================

                if action == "COMPLETED":

                    _update_risk_assessment(
                        cursor=secondary_cursor,
                        risk_assessment_id=
                            risk_assessment_id,
                        action=action,
                        action_by=payload.action_by,
                        comments=payload.comments,
                        overall_risk_level=
                            overall_risk_level,
                    )


            # =================================================
            # UPDATE EXISTING RISK ASSESSMENT
            # =================================================

            else:

                risk_assessment_id = int(
                    assessment[
                        "riskassessmentid"
                    ]
                )

                _update_risk_assessment(
                    cursor=secondary_cursor,
                    risk_assessment_id=
                        risk_assessment_id,
                    action=action,
                    action_by=payload.action_by,
                    comments=payload.comments,
                    overall_risk_level=
                        overall_risk_level,
                )


            # =================================================
            # SAVE RISK DETAIL ROWS
            # =================================================

            _save_risk_details(
                cursor=secondary_cursor,
                risk_assessment_id=
                    risk_assessment_id,
                prospect_id=
                    reevaluation[
                        "prospectid"
                    ],
                details=payload.details,
            )


            # =================================================
            # COMMIT SECONDARY DB
            # =================================================

            secondary_conn.commit()


        except Exception:

            secondary_conn.rollback()
            raise


        finally:

            secondary_cursor.close()


    # ========================================================
    # STEP 3
    # PRIMARY DB
    #
    # UPDATE REEVALUATION WORKFLOW
    # SAVE ACTION HISTORY
    # ========================================================

    with get_connection() as primary_conn:

        primary_cursor = (
            primary_conn.cursor()
        )

        try:

            # =================================================
            # DRAFT
            #
            # Risk assessment has started.
            #
            # Reevaluation:
            # SUBMITTED -> UNDER_REVIEW
            # =================================================

            if action == "DRAFT":

                new_reevaluation_status = (
                    "UNDER_REVIEW"
                )


                _update_reevaluation_status(
                    cursor=primary_cursor,
                    reevaluation_id=
                        payload.reevaluation_id,
                    risk_assessment_id=
                        risk_assessment_id,
                    status=
                        new_reevaluation_status,
                    modified_by=
                        payload.action_by,
                )


                _save_action(
                    cursor=primary_cursor,
                    reevaluation_id=
                        payload.reevaluation_id,
                    action_type=
                        "RISK_DRAFT_SAVED",
                    from_status=
                        current_status,
                    to_status=
                        new_reevaluation_status,
                    action_by=
                        payload.action_by,
                    remarks=
                        payload.comments,
                )


            # =================================================
            # RETURNED
            #
            # Assessor has returned the reevaluation
            # to the vendor.
            #
            # Reevaluation:
            # UNDER_REVIEW -> RESUBMISSION_REQUESTED
            # =================================================

            elif action == "RETURNED":

                new_reevaluation_status = (
                    "RESUBMISSION_REQUESTED"
                )


                _update_reevaluation_status(
                    cursor=primary_cursor,
                    reevaluation_id=
                        payload.reevaluation_id,
                    risk_assessment_id=
                        risk_assessment_id,
                    status=
                        new_reevaluation_status,
                    modified_by=
                        payload.action_by,
                )


                _save_action(
                    cursor=primary_cursor,
                    reevaluation_id=
                        payload.reevaluation_id,
                    action_type=
                        "RESUBMISSION_REQUESTED",
                    from_status=
                        current_status,
                    to_status=
                        new_reevaluation_status,
                    action_by=
                        payload.action_by,
                    remarks=
                        payload.comments,
                )


            # =================================================
            # COMPLETED
            #
            # Risk assessment is completed.
            #
            # IMPORTANT FIX:
            #
            # Previously this block only called:
            #
            #     _link_risk_assessment()
            #
            # That linked RiskAssessmentId but did NOT
            # update Reevaluation.Status.
            #
            # Therefore SUBMITTED remained SUBMITTED.
            #
            # Now:
            #
            #     Reevaluation.Status = UNDER_REVIEW
            #
            # =================================================
 
            elif action == "COMPLETED":

            # =========================================================
            # RISK ASSESSMENT COMPLETED
            #
            # Business Rule:
            # When risk assessment is completed,
            # entire reevaluation is also completed.
            # =========================================================

                new_reevaluation_status = (
                    "COMPLETED"
                )


                # =========================================================
                # COMPLETE MAIN REEVALUATION
                #
                # Updates:
                #   RiskAssessmentId
                #   Status = COMPLETED
                #   CompletedAt
                #   ModifiedAt
                #   ModifiedBy
                # =========================================================

                _complete_reevaluation(
                    cursor=primary_cursor,

                    reevaluation_id=
                        payload.reevaluation_id,

                    risk_assessment_id=
                        risk_assessment_id,

                    modified_by=
                        payload.action_by,
                )


                # =========================================================
                # SAVE HISTORY
                # =========================================================

                _save_action(
                    cursor=primary_cursor,

                    reevaluation_id=
                        payload.reevaluation_id,

                    action_type=
                        "RISK_ASSESSED",

                    from_status=
                        current_status,

                    to_status=
                        new_reevaluation_status,

                    action_by=
                        payload.action_by,

                    remarks=(
                        payload.comments
                        or
                        (
                            "Risk assessment completed. "
                            "Reevaluation completed."
                        )
                    ),
                )


            # =================================================
            # COMMIT PRIMARY DB
            # =================================================

            primary_conn.commit()


        except Exception:

            primary_conn.rollback()
            raise


        finally:

            primary_cursor.close()


    # ========================================================
    # RESPONSE
    # ========================================================

    return {
        "status": True,

        "message": (
            "Risk assessment "
            "saved successfully."
        ),

        "data": {

            "reevaluation_id":
                payload.reevaluation_id,

            "risk_assessment_id":
                risk_assessment_id,

            "assessment_status":
                action,

            "reevaluation_status":
                new_reevaluation_status,

            "overall_risk_level":
                overall_risk_level,
        },
    }


# ============================================================
# ASYNC WRAPPER
# ============================================================

async def save_reevaluation_risk_assessment(
    payload,
):

    return await run_in_threadpool(
        save_reevaluation_risk_assessment_sync,
        payload,
    )



# ============================================================
# RETURN EMAIL
# FETCH DATA FROM BOTH DATABASES
# ============================================================

def _get_return_email_data(
    reevaluation_id: int,
):

    # ========================================================
    # PRIMARY DB
    # Reevaluation + Vendor
    # ========================================================

    with get_connection() as primary_conn:

        primary_cursor = (
            primary_conn.cursor()
        )

        try:

            primary_cursor.execute(
                f"""
                SELECT TOP 1
                    R.ReevaluationId,
                    R.ReevaluationNo,
                    R.ProspectId,
                    R.VendorAccount,
                    R.ToEmail,
                    R.Status,
                    VP.Name AS VendorName

                FROM {REEVALUATION_TABLE} R

                LEFT JOIN {VENDOR_PROSPECT_TABLE} VP
                    ON VP.ProspectId =
                       R.ProspectId

                WHERE
                    R.ReevaluationId = ?

                    AND R.IsActive = 1;
                """,

                reevaluation_id,
            )


            row = (
                primary_cursor.fetchone()
            )


            if row is None:
                return None


            primary_data = (
                _row_to_dict(
                    primary_cursor,
                    row,
                )
            )


        finally:

            primary_cursor.close()


    # ========================================================
    # SECONDARY DB
    # Risk Assessment + Return Reason
    # ========================================================

    with get_secondary_connection() as secondary_conn:

        secondary_cursor = (
            secondary_conn.cursor()
        )

        try:

            secondary_cursor.execute(
                f"""
                SELECT TOP 1
                    RiskAssessmentId,
                    AssessmentStatus

                FROM {RISK_ASSESSMENT_TABLE}

                WHERE
                    ReevaluationId = ?

                ORDER BY
                    RiskAssessmentId DESC;
                """,

                reevaluation_id,
            )


            row = (
                secondary_cursor.fetchone()
            )


            secondary_data = {}


            if row is not None:

                secondary_data = (
                    _row_to_dict(
                        secondary_cursor,
                        row,
                    )
                )


        finally:

            secondary_cursor.close()


    # Merge primary + secondary data in Python
    return {
        **primary_data,
        **secondary_data,
    }


# ============================================================
# PRIMARY DB
# GET ACTIVE CC RECIPIENTS
# ============================================================

def _get_active_cc_emails(
    cursor,
):

    cursor.execute(
        f"""
        SELECT
            EmailAddress

        FROM {CC_MASTER_TABLE}

        WHERE
            IsActive = 1

        ORDER BY
            CCMasterId;
        """
    )


    return [
        str(
            row[0]
        ).strip()

        for row in cursor.fetchall()

        if row[0]
        and str(
            row[0]
        ).strip()
    ]


# ============================================================
# BUILD RETURN EMAIL
# ============================================================
def _build_return_email(
    subject: str,
    body: str,
    vendor_name: str,
    return_reason: str,
    reevaluation_no: str,
    vendor_account: str,
    reevaluation_id: int,
):

    frontend_url = (
        settings
        .ONBOARDING_FRONTEND_URL
        .strip()
        .rstrip("/")
    )

    reevaluation_link = (
    settings.ONBOARDING_FRONTEND_URL
    .strip()
)


    replacements = {

        "[Vendor Name]":
            vendor_name or "",

        "[Return Reason]":
            return_reason or "",

        "[Reevaluation No]":
            reevaluation_no or "",

        "[Vendor Account]":
            vendor_account or "",

        "[Update Reevaluation Form]":
            reevaluation_link,

        "[Document Upload Link]":
            reevaluation_link,

        "[Company Name]":
            "Hi-Q Electronics",
    }


    rendered_subject = (
        subject or ""
    )

    rendered_body = (
        body or ""
    )


    for placeholder, value in replacements.items():

        rendered_subject = (
            rendered_subject.replace(
                placeholder,
                str(value),
            )
        )

        rendered_body = (
            rendered_body.replace(
                placeholder,
                str(value),
            )
        )


    rendered_body = (
        rendered_body
        .replace("\r\n", "<br>")
        .replace("\n", "<br>")
    )


    content = f"""
        <p style="margin:0 0 20px 0;">
            {rendered_body}
        </p>

        <div
            style="
                background:#fff7ed;
                border-left:4px solid #f59e0b;
                padding:14px 16px;
                margin:20px 0;
            "
        >
            <strong>Return Reason:</strong><br>
            {return_reason}
        </div>
    """


    html_body = (
        build_reevaluation_email_html(

            vendor_name=
                vendor_name,

            content=
                content,

            action_url=
                reevaluation_link,

            action_text=
                "Update Reevaluation Form",
        )
    )


    return (
        rendered_subject,
        html_body,
    )


# ============================================================
# ACTUAL EMAIL SENDER
# ============================================================

def _send_return_email(
    to_email: str,
    cc_emails: list[str],
    subject: str,
    body: str,
):

    result = send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        cc_emails=cc_emails,
    )

    if result is not True:
        raise Exception(
            "Email service failed to send return email."
        )


# ============================================================
# PRIMARY DB
# SAVE RETURN EMAIL ACTION
# ============================================================

def _save_return_email_action(
    cursor,
    reevaluation_id: int,
    action_by: str,
    email_status: str,
    email_error: str | None = None,
):

    action_type = (
        "RETURN_EMAIL_SENT"

        if email_status == "SENT"

        else
        "RETURN_EMAIL_FAILED"
    )


    remarks = (
        "Return email sent successfully."

        if email_status == "SENT"

        else
        "Return email failed."
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
            EmailTriggered,
            EmailStatus,
            EmailSentAt,
            EmailError
        )

        VALUES
        (
            ?,
            ?,
            'RESUBMISSION_REQUESTED',
            'RESUBMISSION_REQUESTED',
            ?,
            ?,
            SYSDATETIME(),
            1,
            ?,

            CASE
                WHEN ? = 'SENT'
                    THEN SYSDATETIME()

                ELSE NULL
            END,

            ?
        );
        """,

        reevaluation_id,

        action_type,

        remarks,

        action_by,

        email_status,

        email_status,

        email_error,
    )


# ============================================================
# SECONDARY DB
# SAVE RETURN REASON INTO RiskAssessment.Comments
# ============================================================

def _save_return_reason(
    reevaluation_id: int,
    return_reason: str,
):

    with get_secondary_connection() as conn:

        cursor = conn.cursor()

        try:

            cursor.execute(
                f"""
                UPDATE {RISK_ASSESSMENT_TABLE}

                SET
                    Comments = ?,
                    ModifiedAt = SYSDATETIME()

                WHERE
                    RiskAssessmentId =
                    (
                        SELECT TOP 1
                            RiskAssessmentId

                        FROM {RISK_ASSESSMENT_TABLE}

                        WHERE
                            ReevaluationId = ?

                        ORDER BY
                            RiskAssessmentId DESC
                    );
                """,

                return_reason,
                reevaluation_id,
            )

            if cursor.rowcount == 0:
                raise ValueError(
                    "Risk assessment not found."
                )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            cursor.close()


# ============================================================
# SEND RETURN EMAIL
# ============================================================

def send_return_email_sync(
    payload,
):

    subject = (
        payload.subject
        or ""
    ).strip()


    body = (
        payload.body
        or ""
    ).strip()


    return_reason = (
        payload.return_reason
        or ""
    ).strip()


    if not subject:

        return {
            "status": False,

            "message":
                "Email subject is required.",

            "data": None,
        }


    if not body:

        return {
            "status": False,

            "message":
                "Email body is required.",

            "data": None,
        }


    if not return_reason:

        return {
            "status": False,

            "message":
                "Return reason is required.",

            "data": None,
        }


    # ========================================================
    # PRIMARY + SECONDARY DATA
    # ========================================================

    email_data = (
        _get_return_email_data(
            payload.reevaluation_id
        )
    )


    if not email_data:

        return {
            "status": False,

            "message":
                "Reevaluation not found.",

            "data": None,
        }


    # ========================================================
    # VALIDATE WORKFLOW STATUS
    # ========================================================

    reevaluation_status = (
        email_data.get(
            "status"
        )
        or ""
    ).strip().upper()


    if (
        reevaluation_status
        != "RESUBMISSION_REQUESTED"
    ):

        return {
            "status": False,

            "message":
                (
                    "Return email can only be sent "
                    "when reevaluation status is "
                    "RESUBMISSION_REQUESTED."
                ),

            "data": None,
        }


    # ========================================================
    # VALIDATE RISK ASSESSMENT STATUS
    # ========================================================

    assessment_status = (
        email_data.get(
            "assessmentstatus"
        )
        or ""
    ).strip().upper()


    if assessment_status != "RETURNED":

        return {
            "status": False,

            "message":
                (
                    "Risk assessment must be "
                    "RETURNED before sending "
                    "the return email."
                ),

            "data": None,
        }


    # ========================================================
    # SAVE RETURN REASON
    # Frontend key: return_reason
    # DB column: HIQ_VendorRiskAssessment.Comments
    # ========================================================

    _save_return_reason(
        reevaluation_id=payload.reevaluation_id,
        return_reason=return_reason,
    )


    # ========================================================
    # PRIMARY DB
    # CC + LOG
    # ========================================================

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # TO EMAIL
            # =================================================

            if (
                payload.to_email
                and payload.to_email.strip()
            ):

                to_email = (
                    payload.to_email
                    .strip()
                )

            else:

                to_email = (
                    email_data.get(
                        "toemail"
                    )
                    or ""
                ).strip()


            if not to_email:

                return {
                    "status": False,

                    "message":
                        "Vendor email not found.",

                    "data": None,
                }


            # =================================================
            # CC
            # =================================================

            if payload.cc_emails:

                cc_emails = [

                    email.strip()

                    for email
                    in payload.cc_emails

                    if email
                    and email.strip()
                ]

            else:

                cc_emails = (
                    _get_active_cc_emails(
                        cursor
                    )
                )


            # Remove duplicate CC entries
            cc_emails = list(
                dict.fromkeys(
                    cc_emails
                )
            )


            # Do not repeat TO address in CC
            cc_emails = [

                email

                for email in cc_emails

                if (
                    email.lower()
                    != to_email.lower()
                )
            ]


            # =================================================
            # BUILD EMAIL
            # =================================================

            vendor_name = (
                email_data.get(
                    "vendorname"
                )
                or ""
            )


            reevaluation_no = (
                email_data.get(
                    "reevaluationno"
                )
                or ""
            )


            vendor_account = (
                email_data.get(
                    "vendoraccount"
                )
                or ""
            )


            (
                rendered_subject,
                rendered_body,
            ) = _build_return_email(

                subject=
                    subject,

                body=
                    body,

                vendor_name=
                    vendor_name,

                return_reason=
                    return_reason,

                reevaluation_no=
                    reevaluation_no,

                vendor_account=
                    vendor_account,

                reevaluation_id=
                    payload.reevaluation_id,
            )


            # =================================================
            # SEND
            # =================================================

            try:

                _send_return_email(

                    to_email=
                        to_email,

                    cc_emails=
                        cc_emails,

                    subject=
                        rendered_subject,

                    body=
                        rendered_body,
                )


            except Exception as email_exception:

                error_message = str(
                    email_exception
                )


                _save_return_email_action(

                    cursor=
                        cursor,

                    reevaluation_id=
                        payload.reevaluation_id,

                    action_by=
                        payload.action_by,

                    email_status=
                        "FAILED",

                    email_error=
                        error_message,
                )


                conn.commit()


                return {
                    "status": False,

                    "message":
                        "Return email failed.",

                    "data": {

                        "reevaluation_id":
                            payload.reevaluation_id,

                        "email_status":
                            "FAILED",

                        "email_error":
                            error_message,
                    },
                }


            # =================================================
            # SUCCESS
            # =================================================

            _save_return_email_action(

                cursor=
                    cursor,

                reevaluation_id=
                    payload.reevaluation_id,

                action_by=
                    payload.action_by,

                email_status=
                    "SENT",

                email_error=
                    None,
            )


            conn.commit()


            return {
                "status": True,

                "message":
                    (
                        "Return email "
                        "sent successfully."
                    ),

                "data": {

                    "reevaluation_id":
                        payload.reevaluation_id,

                    "reevaluation_no":
                        reevaluation_no,

                    "vendor_name":
                        vendor_name,

                    "to_email":
                        to_email,

                    "cc_emails":
                        cc_emails,

                    "subject":
                        rendered_subject,

                    "return_reason":
                        return_reason,

                    "email_status":
                        "SENT",
                },
            }


        except Exception:

            conn.rollback()

            raise


        finally:

            cursor.close()


async def send_return_email(
    payload,
):

    return await run_in_threadpool(
        send_return_email_sync,
        payload,
    )