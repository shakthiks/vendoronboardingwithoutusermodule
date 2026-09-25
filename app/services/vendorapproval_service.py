from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pyodbc

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_secondary_connection,
)
from app.schemas.vendorapproval_schema import (
    ApprovalFetchRequest,
    ApprovalSubmitRequest,
)
from app.services.d365_vendor_creation_service import (
    create_vendor_in_d365,
)
import logging

logger = logging.getLogger(__name__)

LOCAL_DB_SCHEMA = getattr(
    settings,
    "SECONDARY_DB_SCHEMA",
    getattr(settings, "DB_SCHEMA", "dev"),
)

CLOUD_DB_SCHEMA = getattr(
    settings,
    "DB_SCHEMA",
    "dev",
)

USERS_TABLE = f"[{LOCAL_DB_SCHEMA}].[HIQ_Users]"
ROLES_TABLE = f"[{LOCAL_DB_SCHEMA}].[HIQ_Roles]"
USER_ROLES_TABLE = f"[{LOCAL_DB_SCHEMA}].[HIQ_UserRoles]"

APPROVAL_BATCH_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
)
APPROVAL_ASSIGNMENT_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalAssignment]"
)
APPROVAL_ACTION_LOG_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalActionLog]"
)
RISK_HEADER_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
)

REGISTRATION_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[HIQ_VendorRegistration]"
)
PROSPECT_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[d365_VendorProspect]"
)

APPROVER_ROLE_NAME = os.getenv(
    "VENDOR_APPROVER_ROLE_NAME",
    "Approver",
).strip()


class ApprovalBatchNotFoundError(LookupError):
    pass


class ApprovalAssignmentNotFoundError(LookupError):
    pass


class ApprovalAuthorizationError(PermissionError):
    pass


class ApprovalDecisionError(ValueError):
    pass


class ApprovalConflictError(ValueError):
    pass


class ApprovalConfigurationError(ValueError):
    pass


ApprovalNotFoundError = ApprovalBatchNotFoundError


def _serialize(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()

    return value


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _normalize_prospect_id(value: Any) -> str:
    prospect_id = str(
        value or ""
    ).strip().upper()

    if not prospect_id:
        raise ApprovalDecisionError(
            "ProspectId is required"
        )

    return prospect_id


def _fetchone_as_dict(
    cursor: pyodbc.Cursor,
) -> dict[str, Any] | None:
    row = cursor.fetchone()

    if row is None:
        return None

    columns = [
        column[0]
        for column in cursor.description
    ]

    return dict(zip(columns, row))


def _fetchall_as_dicts(
    cursor: pyodbc.Cursor,
) -> list[dict[str, Any]]:
    rows = cursor.fetchall()

    if cursor.description is None:
        return []

    columns = [
        column[0]
        for column in cursor.description
    ]

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def _initials(
    full_name: Any,
    username: Any,
) -> str:
    source = str(
        full_name
        or username
        or "User"
    ).strip()

    parts = [
        part
        for part in source.split()
        if part
    ]

    if not parts:
        return "U"

    if len(parts) == 1:
        return parts[0][:2].upper()

    return (
        parts[0][0]
        + parts[-1][0]
    ).upper()


def _fetch_dynamic_approvers(
    cursor: pyodbc.Cursor,
) -> list[dict[str, Any]]:
    if not APPROVER_ROLE_NAME:
        raise ApprovalConfigurationError(
            "VENDOR_APPROVER_ROLE_NAME is empty"
        )

    cursor.execute(
        f"""
        SELECT TOP 1
            RoleId,
            RoleName
        FROM {ROLES_TABLE}
        WHERE UPPER(LTRIM(RTRIM(RoleName))) = UPPER(?)
          AND IsActive = 1
        ORDER BY RoleId
        """,
        APPROVER_ROLE_NAME,
    )

    role_row = cursor.fetchone()

    if role_row is None:
        raise ApprovalConfigurationError(
            "Active approval role was not found. "
            f"Expected RoleName='{APPROVER_ROLE_NAME}'"
        )

    role_id = int(role_row[0])
    role_name = str(role_row[1]).strip()

    cursor.execute(
        f"""
        SELECT DISTINCT
            approval_user.UserId,
            approval_user.Username,
            approval_user.FullName,
            approval_user.Email,
            approval_role.RoleId,
            approval_role.RoleName
        FROM {USER_ROLES_TABLE} AS user_role
        INNER JOIN {USERS_TABLE} AS approval_user
            ON approval_user.UserId = user_role.UserId
        INNER JOIN {ROLES_TABLE} AS approval_role
            ON approval_role.RoleId = user_role.RoleId
        WHERE user_role.RoleId = ?
          AND approval_user.IsActive = 1
          AND ISNULL(approval_user.IsLocked, 0) = 0
          AND approval_role.IsActive = 1
        ORDER BY approval_user.UserId
        """,
        role_id,
    )

    approvers = _fetchall_as_dicts(
        cursor
    )

    if not approvers:
        raise ApprovalConfigurationError(
            "No active users are mapped to "
            f"RoleName='{role_name}'"
        )

    # if len(approvers) < 3 or len(approvers) > 5:
    #     raise ApprovalConfigurationError(
    #         "Approval-role users must be between 3 and 5 "
    #         "because RequiredApproverCount is constrained "
    #         f"to 3-5. Current count={len(approvers)}"
    #     )

    return approvers


def _ensure_no_pending_batch(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> None:
    cursor.execute(
        f"""
        SELECT TOP 1
            ApprovalBatchId
        FROM {APPROVAL_BATCH_TABLE}
        WHERE ProspectId = ?
          AND BatchStatus = N'PENDING'
        ORDER BY ApprovalBatchId DESC
        """,
        prospect_id,
    )

    row = cursor.fetchone()

    if row is not None:
        raise ApprovalConflictError(
            "A pending approval batch already exists. "
            f"ApprovalBatchId={row[0]}"
        )



def _ensure_no_existing_batch_for_assessment(
    cursor: pyodbc.Cursor,
    risk_assessment_id: int,
) -> None:
    cursor.execute(
        f"""
        SELECT TOP 1
            ApprovalBatchId,
            BatchStatus
        FROM {APPROVAL_BATCH_TABLE}
        WITH
        (
            UPDLOCK,
            HOLDLOCK
        )
        WHERE RiskAssessmentId = ?
        ORDER BY ApprovalBatchId DESC
        """,
        risk_assessment_id,
    )

    row = cursor.fetchone()

    if row is not None:
        raise ApprovalConflictError(
            "An approval batch already exists for "
            f"RiskAssessmentId={risk_assessment_id}. "
            f"ApprovalBatchId={row[0]}, "
            f"BatchStatus={row[1]}. "
            "Create a new risk-assessment cycle before "
            "submitting again."
        )

def create_approval_workflow_in_transaction(
    cursor: pyodbc.Cursor,
    risk_assessment_id: int,
    prospect_id: str,
    initiated_by_user_id: int,
) -> dict[str, Any]:
    """Create a batch and dynamic assignments in caller transaction."""

    prospect_id = _normalize_prospect_id(
        prospect_id
    )

    if risk_assessment_id <= 0:
        raise ApprovalConflictError(
            "risk_assessment_id must be greater than zero"
        )

    if initiated_by_user_id <= 0:
        raise ApprovalConflictError(
            "initiated_by_user_id must be greater than zero"
        )

    _ensure_no_existing_batch_for_assessment(
        cursor,
        risk_assessment_id,
    )

    _ensure_no_pending_batch(
        cursor,
        prospect_id,
    )

    approvers = _fetch_dynamic_approvers(
        cursor
    )

    cursor.execute(
        f"""
        INSERT INTO {APPROVAL_BATCH_TABLE}
        (
            RiskAssessmentId,
            ProspectId,
            RequiredApproverCount,
            BatchStatus,
            AssignedByUserId,
            CreatedAt,
            CompletedAt
        )
        OUTPUT INSERTED.ApprovalBatchId
        VALUES
        (
            ?,
            ?,
            ?,
            N'PENDING',
            ?,
            SYSUTCDATETIME(),
            NULL
        )
        """,
        (
            risk_assessment_id,
            prospect_id,
            len(approvers),
            initiated_by_user_id,
        ),
    )

    approval_batch_id = int(
        cursor.fetchone()[0]
    )

    assignments: list[dict[str, Any]] = []

    for approver in approvers:
        cursor.execute(
            f"""
            INSERT INTO {APPROVAL_ASSIGNMENT_TABLE}
            (
                ApprovalBatchId,
                ProspectId,
                ApproverUserId,
                ApproverRoleCode,
                DecisionStatus,
                DecisionRemarks,
                AssignedAt,
                DecisionAt
            )
            OUTPUT INSERTED.ApprovalAssignmentId
            VALUES
            (
                ?,
                ?,
                ?,
                ?,
                N'PENDING',
                NULL,
                SYSUTCDATETIME(),
                NULL
            )
            """,
            (
                approval_batch_id,
                prospect_id,
                int(approver["UserId"]),
                str(approver["RoleName"]),
            ),
        )

        assignment_id = int(
            cursor.fetchone()[0]
        )

        assignments.append(
            {
                "APPROVAL_ASSIGNMENT_ID": assignment_id,
                "APPROVAL_BATCH_ID": approval_batch_id,
                "PROSPECT_ID": prospect_id,
                "APPROVER_USER_ID": int(
                    approver["UserId"]
                ),
                "APPROVER_ROLE_CODE": approver.get(
                    "RoleName"
                ),
                "USERNAME": approver.get(
                    "Username"
                ),
                "FULL_NAME": approver.get(
                    "FullName"
                ),
                "EMAIL": approver.get(
                    "Email"
                ),
                "DECISION_STATUS": "PENDING",
            }
        )

    return {
        "APPROVAL_BATCH_ID": approval_batch_id,
        "BATCH_STATUS": "PENDING",
        "REQUIRED_APPROVER_COUNT": len(
            approvers
        ),
        "ASSIGNMENTS": assignments,
    }


def _get_latest_approval_batch(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any]:
    cursor.execute(
        f"""
        SELECT TOP 1
            ApprovalBatchId,
            RiskAssessmentId,
            ProspectId,
            RequiredApproverCount,
            BatchStatus,
            AssignedByUserId,
            CreatedAt,
            CompletedAt
        FROM {APPROVAL_BATCH_TABLE}
        WHERE ProspectId = ?
        ORDER BY ApprovalBatchId DESC
        """,
        prospect_id,
    )

    batch = _fetchone_as_dict(
        cursor
    )

    if batch is None:
        raise ApprovalBatchNotFoundError(
            "Approval batch was not found for "
            f"ProspectId={prospect_id}"
        )

    return batch


def _get_prospect_summary(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any]:
    cursor.execute(
        f"""
        SELECT TOP 1
            prospect.ProspectId,
            prospect.VendorAccount,
            prospect.Name,
            prospect.Email,
            registration.CompanyName,
            registration.Status
                AS VendorRegistrationStatus
        FROM {PROSPECT_TABLE} AS prospect
        LEFT JOIN {REGISTRATION_TABLE} AS registration
            ON registration.ProspectId = prospect.ProspectId
        WHERE prospect.ProspectId = ?
        """,
        prospect_id,
    )

    row = _fetchone_as_dict(
        cursor
    )

    if row is not None:
        return {
            "PROSPECT_ID": row.get("ProspectId"),
            "VENDOR_ACCOUNT": row.get("VendorAccount"),
            "NAME": row.get("Name"),
            "EMAIL": row.get("Email"),
            "COMPANY_NAME": row.get("CompanyName"),
            "VENDOR_REGISTRATION_STATUS": row.get(
                "VendorRegistrationStatus"
            ),
        }

    cursor.execute(
        f"""
        SELECT TOP 1
            ProspectId,
            CompanyName,
            Status
        FROM {REGISTRATION_TABLE}
        WHERE ProspectId = ?
        """,
        prospect_id,
    )

    row = _fetchone_as_dict(
        cursor
    )

    return {
        "PROSPECT_ID": prospect_id,
        "VENDOR_ACCOUNT": None,
        "NAME": (
            row.get("CompanyName")
            if row
            else None
        ),
        "EMAIL": None,
        "COMPANY_NAME": (
            row.get("CompanyName")
            if row
            else None
        ),
        "VENDOR_REGISTRATION_STATUS": (
            row.get("Status")
            if row
            else None
        ),
    }


def _get_approval_assignments(
    cursor: pyodbc.Cursor,
    approval_batch_id: int,
) -> list[dict[str, Any]]:
    """
    Fetch the approvers that were assigned to this approval batch.

    Important:
    Do not re-check the current Approval role/user list here.
    Approval assignments are a snapshot at the time of risk submission.

    Example:
    - Batch created when 2 approvers existed -> fetch must show 2.
    - Later a 3rd approver is added -> old batch still shows 2.
    - New batch created after that -> new batch shows 3.
    """

    cursor.execute(
        f"""
        SELECT
            assignment.ApprovalAssignmentId,
            assignment.ApprovalBatchId,
            assignment.ProspectId,
            assignment.ApproverUserId,
            assignment.ApproverRoleCode,
            assignment.DecisionStatus,
            assignment.DecisionRemarks,
            assignment.AssignedAt,
            assignment.DecisionAt,
            approval_user.Username,
            approval_user.FullName,
            approval_user.Email
        FROM {APPROVAL_ASSIGNMENT_TABLE} AS assignment
        LEFT JOIN {USERS_TABLE} AS approval_user
            ON approval_user.UserId = assignment.ApproverUserId
        WHERE assignment.ApprovalBatchId = ?
        ORDER BY assignment.ApprovalAssignmentId
        """,
        approval_batch_id,
    )

    return _fetchall_as_dicts(
        cursor
    )


def _calculate_batch_counts(
    cursor: pyodbc.Cursor,
    approval_batch_id: int,
) -> dict[str, int]:
    cursor.execute(
        f"""
        SELECT
            COUNT(*) AS TotalCount,
            SUM(
                CASE
                    WHEN DecisionStatus = N'APPROVED'
                    THEN 1
                    ELSE 0
                END
            ) AS ApprovedCount,
            SUM(
                CASE
                    WHEN DecisionStatus = N'REJECTED'
                    THEN 1
                    ELSE 0
                END
            ) AS RejectedCount,
            SUM(
                CASE
                    WHEN DecisionStatus = N'PENDING'
                    THEN 1
                    ELSE 0
                END
            ) AS PendingCount
        FROM {APPROVAL_ASSIGNMENT_TABLE}
        WHERE ApprovalBatchId = ?
        """,
        approval_batch_id,
    )

    row = cursor.fetchone()

    return {
        "TOTAL": int(row[0] or 0),
        "APPROVED": int(row[1] or 0),
        "REJECTED": int(row[2] or 0),
        "PENDING": int(row[3] or 0),
    }


def fetch_approval_dashboard_sync(
    payload: ApprovalFetchRequest,
) -> dict[str, Any]:
    prospect_id = _normalize_prospect_id(
        payload.ProspectId
    )
    user_id = int(payload.UserId)

    if user_id <= 0:
        raise ApprovalDecisionError(
            "UserId must be greater than zero"
        )

    with get_secondary_connection() as connection:
        cursor = connection.cursor()

        try:
            batch = _get_latest_approval_batch(
                cursor,
                prospect_id,
            )

            batch_id = int(
                batch["ApprovalBatchId"]
            )

            with get_connection() as cloud_connection:
                cloud_cursor = cloud_connection.cursor()

                try:
                    prospect = _get_prospect_summary(
                        cloud_cursor,
                        prospect_id,
                    )
                finally:
                    cloud_cursor.close()

            rows = _get_approval_assignments(
                cursor,
                batch_id,
            )

            counts = _calculate_batch_counts(
                cursor,
                batch_id,
            )

            approvers: list[dict[str, Any]] = []
            current_user_assignment = None

            for row in rows:
                status_value = str(
                    row.get("DecisionStatus")
                    or "PENDING"
                ).strip().upper()

                card = {
                    "APPROVAL_ASSIGNMENT_ID": int(
                        row["ApprovalAssignmentId"]
                    ),
                    "APPROVAL_BATCH_ID": int(
                        row["ApprovalBatchId"]
                    ),
                    "PROSPECT_ID": row.get(
                        "ProspectId"
                    ),
                    "APPROVER_USER_ID": int(
                        row["ApproverUserId"]
                    ),
                    "APPROVER_ROLE_CODE": row.get(
                        "ApproverRoleCode"
                    ),
                    "USERNAME": row.get("Username"),
                    "FULL_NAME": row.get("FullName"),
                    "EMAIL": row.get("Email"),
                    "INITIALS": _initials(
                        row.get("FullName"),
                        row.get("Username"),
                    ),
                    "DECISION_STATUS": status_value,
                    "DECISION_REMARKS": row.get(
                        "DecisionRemarks"
                    ),
                    "ASSIGNED_AT": _serialize(
                        row.get("AssignedAt")
                    ),
                    "DECISION_AT": _serialize(
                        row.get("DecisionAt")
                    ),
                    "IS_CURRENT_USER": (
                        int(row["ApproverUserId"])
                        == user_id
                    ),
                }

                approvers.append(card)

                if card["IS_CURRENT_USER"]:
                    current_user_assignment = {
                        **card,
                        "CAN_DECIDE": (
                            status_value == "PENDING"
                            and str(
                                batch["BatchStatus"]
                            ).strip().upper()
                            == "PENDING"
                        ),
                    }

            return {
                "SUCCESS": True,
                "PROSPECT": prospect,
                "APPROVAL_BATCH": {
                    "APPROVAL_BATCH_ID": batch_id,
                    "RISK_ASSESSMENT_ID": int(
                        batch["RiskAssessmentId"]
                    ),
                    "PROSPECT_ID": batch.get(
                        "ProspectId"
                    ),
                    # "REQUIRED_APPROVER_COUNT": int(
                    #     batch["RequiredApproverCount"]
                    # ),
                    "REQUIRED_APPROVER_COUNT": counts["TOTAL"],
                    "ORIGINAL_REQUIRED_APPROVER_COUNT": int(
                        batch["RequiredApproverCount"] or counts["TOTAL"]
                    ),
                    "BATCH_STATUS": batch.get(
                        "BatchStatus"
                    ),
                    "ASSIGNED_BY_USER_ID": int(
                        batch["AssignedByUserId"]
                    ),
                    "CREATED_AT": _serialize(
                        batch.get("CreatedAt")
                    ),
                    "COMPLETED_AT": _serialize(
                        batch.get("CompletedAt")
                    ),
                },
                "SUMMARY": {
                    "TOTAL_COUNT": counts["TOTAL"],
                    "COMPLETED_COUNT": (
                        counts["APPROVED"]
                        + counts["REJECTED"]
                    ),
                    "APPROVED_COUNT": counts[
                        "APPROVED"
                    ],
                    "REJECTED_COUNT": counts[
                        "REJECTED"
                    ],
                    "PENDING_COUNT": counts[
                        "PENDING"
                    ],
                },
                "APPROVERS": approvers,
                "CURRENT_USER_DECISION": {
                    "USER_ID": user_id,
                    "HAS_ASSIGNMENT": (
                        current_user_assignment
                        is not None
                    ),
                    "CAN_DECIDE": bool(
                        current_user_assignment
                        and current_user_assignment[
                            "CAN_DECIDE"
                        ]
                    ),
                    "ASSIGNMENT": (
                        current_user_assignment
                    ),
                },
            }

        finally:
            cursor.close()


def _insert_action_log(
    cursor: pyodbc.Cursor,
    approval_assignment_id: int,
    action_type: str,
    previous_status: str,
    new_status: str,
    action_by_user_id: int,
    remarks: str | None,
) -> None:
    cursor.execute(
        f"""
        INSERT INTO {APPROVAL_ACTION_LOG_TABLE}
        (
            ApprovalAssignmentId,
            ActionType,
            PreviousStatus,
            NewStatus,
            ActionByUserId,
            Remarks,
            ActionAt
        )
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            SYSUTCDATETIME()
        )
        """,
        (
            approval_assignment_id,
            action_type,
            previous_status,
            new_status,
            action_by_user_id,
            remarks,
        ),
    )


def _update_batch_status(
    cursor: pyodbc.Cursor,
    approval_batch_id: int,
    batch_status: str,
) -> None:
    cursor.execute(
        f"""
        UPDATE {APPROVAL_BATCH_TABLE}
        SET
            BatchStatus = ?,
            CompletedAt = CASE
                WHEN ? = N'PENDING'
                THEN NULL
                ELSE SYSUTCDATETIME()
            END
        WHERE ApprovalBatchId = ?
        """,
        (
            batch_status,
            batch_status,
            approval_batch_id,
        ),
    )


def _update_risk_status(
    cursor: pyodbc.Cursor,
    risk_assessment_id: int,
    prospect_id: str,
    status_value: str,
) -> None:
    cursor.execute(
        f"""
        UPDATE {RISK_HEADER_TABLE}
        SET
            AssessmentStatus = ?,
            ModifiedAt = SYSUTCDATETIME()
        WHERE RiskAssessmentId = ?
          AND ProspectId = ?
        """,
        (
            status_value,
            risk_assessment_id,
            prospect_id,
        ),
    )


def _update_registration_status(
    cursor: pyodbc.Cursor,
    prospect_id: str,
    status_value: str,
    is_draft: int,
) -> None:
    cursor.execute(
        f"""
        UPDATE {REGISTRATION_TABLE}
        SET
            Status = ?,
            IsDraft = ?,
            ModifiedOn = SYSUTCDATETIME()
        WHERE ProspectId = ?
        """,
        (
            status_value,
            is_draft,
            prospect_id,
        ),
    )


def submit_approval_decision_sync(
    payload: ApprovalSubmitRequest,
) -> dict[str, Any]:
    prospect_id = _normalize_prospect_id(
        payload.ProspectId
    )
    assignment_id = int(
        payload.ApprovalAssignmentId
    )
    user_id = int(payload.UserId)

    decision = (
        payload.Decision.value
        if hasattr(payload.Decision, "value")
        else str(payload.Decision)
    ).strip().upper()

    remarks = _normalize_text(
        payload.Remarks
    )

    if decision not in {
        "APPROVE",
        "REJECT",
    }:
        raise ApprovalDecisionError(
            "Decision must be APPROVE or REJECT"
        )

    if decision == "REJECT" and not remarks:
        raise ApprovalDecisionError(
            "Remarks is required when Decision is REJECT"
        )

    should_trigger_d365 = False

    with get_secondary_connection() as connection:
        cursor = connection.cursor()

        try:
            cursor.execute(
                "SET XACT_ABORT ON;"
            )

            cursor.execute(
                f"""
                SELECT
                    assignment.ApprovalAssignmentId,
                    assignment.ApprovalBatchId,
                    assignment.ProspectId,
                    assignment.ApproverUserId,
                    assignment.DecisionStatus,
                    batch.RiskAssessmentId,
                    batch.BatchStatus
                FROM {APPROVAL_ASSIGNMENT_TABLE}
                     AS assignment
                INNER JOIN {APPROVAL_BATCH_TABLE}
                           AS batch
                    ON batch.ApprovalBatchId =
                       assignment.ApprovalBatchId
                   AND batch.ProspectId =
                       assignment.ProspectId
                WHERE assignment.ApprovalAssignmentId = ?
                  AND assignment.ProspectId = ?
                """,
                (
                    assignment_id,
                    prospect_id,
                ),
            )

            assignment = _fetchone_as_dict(
                cursor
            )

            if assignment is None:
                raise ApprovalAssignmentNotFoundError(
                    "Approval assignment was not found: "
                    f"{assignment_id}"
                )

            if int(
                assignment["ApproverUserId"]
            ) != user_id:
                raise ApprovalAuthorizationError(
                    "This assignment does not belong to "
                    "the supplied UserId"
                )

            previous_status = str(
                assignment["DecisionStatus"]
                or "PENDING"
            ).strip().upper()

            current_batch_status = str(
                assignment["BatchStatus"]
                or "PENDING"
            ).strip().upper()

            if current_batch_status != "PENDING":
                raise ApprovalDecisionError(
                    "This batch is already completed. "
                    f"Current status={current_batch_status}"
                )

            if previous_status != "PENDING":
                raise ApprovalDecisionError(
                    "This assignment is already decided. "
                    f"Current status={previous_status}"
                )

            new_status = (
                "APPROVED"
                if decision == "APPROVE"
                else "REJECTED"
            )

            cursor.execute(
                f"""
                UPDATE {APPROVAL_ASSIGNMENT_TABLE}
                SET
                    DecisionStatus = ?,
                    DecisionRemarks = ?,
                    DecisionAt = SYSUTCDATETIME()
                WHERE ApprovalAssignmentId = ?
                  AND ApproverUserId = ?
                  AND DecisionStatus = N'PENDING'
                """,
                (
                    new_status,
                    remarks,
                    assignment_id,
                    user_id,
                ),
            )

            if cursor.rowcount != 1:
                raise ApprovalConflictError(
                    "The assignment changed in another request. "
                    "Refresh and retry."
                )

            _insert_action_log(
                cursor=cursor,
                approval_assignment_id=(
                    assignment_id
                ),
                action_type=decision,
                previous_status=previous_status,
                new_status=new_status,
                action_by_user_id=user_id,
                remarks=remarks,
            )

            approval_batch_id = int(
                assignment["ApprovalBatchId"]
            )
            risk_assessment_id = int(
                assignment["RiskAssessmentId"]
            )

            counts = _calculate_batch_counts(
                cursor,
                approval_batch_id,
            )

            if counts["PENDING"] > 0:
                final_batch_status = "PENDING"
                final_vendor_status = None
                final_risk_status = None
                final_is_draft = None

            elif counts["REJECTED"] > 0:
                final_batch_status = "RETURNED"
                final_vendor_status = "RETURNED"
                final_risk_status = "RETURNED"
                final_is_draft = 0

            else:
                final_batch_status = "APPROVED"
                final_vendor_status = "APPROVED"
                final_risk_status = "APPROVED"
                final_is_draft = 1
                should_trigger_d365 = True

            _update_batch_status(
                cursor,
                approval_batch_id,
                final_batch_status,
            )

            if final_risk_status is not None:
                _update_risk_status(
                    cursor,
                    risk_assessment_id,
                    prospect_id,
                    final_risk_status,
                )

            if final_vendor_status is not None:
                with get_connection() as cloud_connection:
                    cloud_cursor = cloud_connection.cursor()

                    try:
                        _update_registration_status(
                            cloud_cursor,
                            prospect_id,
                            final_vendor_status,
                            final_is_draft,
                        )

                        cloud_connection.commit()

                    except Exception:
                        cloud_connection.rollback()
                        raise

                    finally:
                        cloud_cursor.close()

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()

    d365_result = None

    if should_trigger_d365:
        d365_result = create_vendor_in_d365(
            prospect_id
        )

        if d365_result.get("SUCCESS"):
            final_vendor_status = "VENDOR_CREATED"

    return {
        "SUCCESS": True,
        "MESSAGE": (
            "Approval decision saved successfully"
        ),
        "PROSPECT_ID": prospect_id,
        "APPROVAL_ASSIGNMENT_ID": assignment_id,
        "APPROVAL_BATCH_ID": approval_batch_id,
        "APPROVER_USER_ID": user_id,
        "YOUR_DECISION": new_status,
        "COUNTS": counts,
        "FINAL_BATCH_STATUS": final_batch_status,
        "FINAL_VENDOR_REGISTRATION_STATUS": (
            final_vendor_status
        ),
        "FINAL_RISK_ASSESSMENT_STATUS": (
            final_risk_status
        ),
        "ISDRAFT": final_is_draft,
        "D365_TRIGGER_RESULT": d365_result,
    }


def retry_d365_vendor_creation_sync(
    prospect_id: str,
) -> dict[str, Any]:

    prospect_id = str(
        prospect_id or ""
    ).strip().upper()

    if not prospect_id:
        raise ApprovalConflictError(
            "ProspectId is required"
        )

    approval_batch_id: int | None = None
    batch_status: str | None = None

    # ========================================================
    # VALIDATE APPROVAL STATUS
    # ========================================================
    with get_secondary_connection() as connection:
        cursor = connection.cursor()

        try:
            cursor.execute(
                f"""
                SELECT TOP 1
                    ApprovalBatchId,
                    BatchStatus
                FROM {APPROVAL_BATCH_TABLE}
                WHERE ProspectId = ?
                ORDER BY ApprovalBatchId DESC
                """,
                prospect_id,
            )

            batch_row = cursor.fetchone()

            if batch_row is None:
                raise ApprovalConflictError(
                    "No approval batch was found for "
                    f"ProspectId={prospect_id}"
                )

            approval_batch_id = int(
                batch_row[0]
            )

            batch_status = str(
                batch_row[1] or ""
            ).strip().upper()

            if batch_status != "APPROVED":
                raise ApprovalConflictError(
                    "D365 vendor creation can only be retried "
                    "after final approval. "
                    f"Current batch status={batch_status}"
                )

        finally:
            cursor.close()

    # ========================================================
    # RETRY D365 VENDOR CREATION
    # ========================================================
    try:
        logger.info(
            "Retrying D365 vendor creation for "
            "ProspectId=%s, ApprovalBatchId=%s",
            prospect_id,
            approval_batch_id,
        )

        d365_result = create_vendor_in_d365(
            prospect_id=prospect_id,
        )

    except Exception as exc:
        logger.exception(
            "D365 retry raised an exception for "
            "ProspectId=%s",
            prospect_id,
        )

        return {
            "SUCCESS": False,
            "MESSAGE": "D365 vendor creation retry failed",
            "PROSPECT_ID": prospect_id,
            "APPROVAL_BATCH_ID": approval_batch_id,
            "APPROVAL_STATUS": batch_status,
            "D365_TRIGGERED": True,
            "D365_RESULT": None,
            "HTTP_STATUS": None,
            "ERROR": str(exc),
        }

    # ========================================================
    # VALIDATE D365 SERVICE RESPONSE
    # ========================================================
    if not isinstance(d365_result, dict):
        return {
            "SUCCESS": False,
            "MESSAGE": (
                "D365 vendor creation returned an invalid response"
            ),
            "PROSPECT_ID": prospect_id,
            "APPROVAL_BATCH_ID": approval_batch_id,
            "APPROVAL_STATUS": batch_status,
            "D365_TRIGGERED": True,
            "D365_RESULT": d365_result,
            "HTTP_STATUS": None,
            "ERROR": (
                "Expected D365 response to be a dictionary"
            ),
        }

    d365_success = bool(
        d365_result.get("SUCCESS", False)
    )

    d365_http_status = d365_result.get(
        "HTTP_STATUS"
    )

    d365_error = d365_result.get(
        "ERROR"
    )

    if not d365_success:
        logger.error(
            "D365 vendor creation retry failed for "
            "ProspectId=%s. HTTP_STATUS=%s, ERROR=%s",
            prospect_id,
            d365_http_status,
            d365_error,
        )

        return {
            "SUCCESS": False,
            "MESSAGE": "D365 vendor creation retry failed",
            "PROSPECT_ID": prospect_id,
            "APPROVAL_BATCH_ID": approval_batch_id,
            "APPROVAL_STATUS": batch_status,
            "D365_TRIGGERED": bool(
                d365_result.get("TRIGGERED", True)
            ),
            "D365_RESULT": d365_result,
            "HTTP_STATUS": d365_http_status,
            "ERROR": (
                d365_error
                or "D365 vendor creation was unsuccessful"
            ),
        }

    logger.info(
        "D365 vendor creation retry succeeded for "
        "ProspectId=%s",
        prospect_id,
    )

    return {
        "SUCCESS": True,
        "MESSAGE": (
            "D365 vendor creation completed successfully"
        ),
        "PROSPECT_ID": prospect_id,
        "APPROVAL_BATCH_ID": approval_batch_id,
        "APPROVAL_STATUS": batch_status,
        "D365_TRIGGERED": bool(
            d365_result.get("TRIGGERED", True)
        ),
        "D365_RESULT": d365_result,
        "HTTP_STATUS": d365_http_status,
        "ERROR": None,
    }

# from __future__ import annotations

# import os
# from datetime import date, datetime
# from typing import Any

# import pyodbc

# from app.core.config import settings
# from app.db.base import get_secondary_connection
# from app.schemas.vendorapproval_schema import (
#     ApprovalFetchRequest,
#     ApprovalSubmitRequest,
# )
# from app.services.d365_vendor_creation_service import (
#     create_vendor_in_d365,
# )
# import logging

# logger = logging.getLogger(__name__)

# DB_SCHEMA = getattr(
#     settings,
#     "SECONDARY_DB_SCHEMA",
#     getattr(settings, "DB_SCHEMA", "dev"),
# )

# USERS_TABLE = f"[{DB_SCHEMA}].[HIQ_Users]"
# ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_Roles]"
# USER_ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_UserRoles]"

# APPROVAL_BATCH_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
# )
# APPROVAL_ASSIGNMENT_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorApprovalAssignment]"
# )
# APPROVAL_ACTION_LOG_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorApprovalActionLog]"
# )
# RISK_HEADER_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
# )
# REGISTRATION_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
# )
# PROSPECT_TABLE = (
#     f"[{DB_SCHEMA}].[d365_VendorProspect]"
# )

# APPROVER_ROLE_NAME = os.getenv(
#     "VENDOR_APPROVER_ROLE_NAME",
#     "Approval",
# ).strip()


# class ApprovalBatchNotFoundError(LookupError):
#     pass


# class ApprovalAssignmentNotFoundError(LookupError):
#     pass


# class ApprovalAuthorizationError(PermissionError):
#     pass


# class ApprovalDecisionError(ValueError):
#     pass


# class ApprovalConflictError(ValueError):
#     pass


# class ApprovalConfigurationError(ValueError):
#     pass


# ApprovalNotFoundError = ApprovalBatchNotFoundError


# def _serialize(value: Any) -> Any:
#     if isinstance(value, (date, datetime)):
#         return value.isoformat()

#     return value


# def _normalize_text(value: Any) -> str | None:
#     if value is None:
#         return None

#     text = str(value).strip()
#     return text or None


# def _normalize_prospect_id(value: Any) -> str:
#     prospect_id = str(
#         value or ""
#     ).strip().upper()

#     if not prospect_id:
#         raise ApprovalDecisionError(
#             "ProspectId is required"
#         )

#     return prospect_id


# def _fetchone_as_dict(
#     cursor: pyodbc.Cursor,
# ) -> dict[str, Any] | None:
#     row = cursor.fetchone()

#     if row is None:
#         return None

#     columns = [
#         column[0]
#         for column in cursor.description
#     ]

#     return dict(zip(columns, row))


# def _fetchall_as_dicts(
#     cursor: pyodbc.Cursor,
# ) -> list[dict[str, Any]]:
#     rows = cursor.fetchall()

#     if cursor.description is None:
#         return []

#     columns = [
#         column[0]
#         for column in cursor.description
#     ]

#     return [
#         dict(zip(columns, row))
#         for row in rows
#     ]


# def _initials(
#     full_name: Any,
#     username: Any,
# ) -> str:
#     source = str(
#         full_name
#         or username
#         or "User"
#     ).strip()

#     parts = [
#         part
#         for part in source.split()
#         if part
#     ]

#     if not parts:
#         return "U"

#     if len(parts) == 1:
#         return parts[0][:2].upper()

#     return (
#         parts[0][0]
#         + parts[-1][0]
#     ).upper()


# def _fetch_dynamic_approvers(
#     cursor: pyodbc.Cursor,
# ) -> list[dict[str, Any]]:
#     if not APPROVER_ROLE_NAME:
#         raise ApprovalConfigurationError(
#             "VENDOR_APPROVER_ROLE_NAME is empty"
#         )

#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             RoleId,
#             RoleName
#         FROM {ROLES_TABLE}
#         WHERE UPPER(LTRIM(RTRIM(RoleName))) = UPPER(?)
#           AND IsActive = 1
#         ORDER BY RoleId
#         """,
#         APPROVER_ROLE_NAME,
#     )

#     role_row = cursor.fetchone()

#     if role_row is None:
#         raise ApprovalConfigurationError(
#             "Active approval role was not found. "
#             f"Expected RoleName='{APPROVER_ROLE_NAME}'"
#         )

#     role_id = int(role_row[0])
#     role_name = str(role_row[1]).strip()

#     cursor.execute(
#         f"""
#         SELECT DISTINCT
#             approval_user.UserId,
#             approval_user.Username,
#             approval_user.FullName,
#             approval_user.Email,
#             approval_role.RoleId,
#             approval_role.RoleName
#         FROM {USER_ROLES_TABLE} AS user_role
#         INNER JOIN {USERS_TABLE} AS approval_user
#             ON approval_user.UserId = user_role.UserId
#         INNER JOIN {ROLES_TABLE} AS approval_role
#             ON approval_role.RoleId = user_role.RoleId
#         WHERE user_role.RoleId = ?
#           AND approval_user.IsActive = 1
#           AND ISNULL(approval_user.IsLocked, 0) = 0
#           AND approval_role.IsActive = 1
#         ORDER BY approval_user.UserId
#         """,
#         role_id,
#     )

#     approvers = _fetchall_as_dicts(
#         cursor
#     )

#     if not approvers:
#         raise ApprovalConfigurationError(
#             "No active users are mapped to "
#             f"RoleName='{role_name}'"
#         )

#     # if len(approvers) < 3 or len(approvers) > 5:
#     #     raise ApprovalConfigurationError(
#     #         "Approval-role users must be between 3 and 5 "
#     #         "because RequiredApproverCount is constrained "
#     #         f"to 3-5. Current count={len(approvers)}"
#     #     )

#     return approvers


# def _ensure_no_pending_batch(
#     cursor: pyodbc.Cursor,
#     prospect_id: str,
# ) -> None:
#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             ApprovalBatchId
#         FROM {APPROVAL_BATCH_TABLE}
#         WHERE ProspectId = ?
#           AND BatchStatus = N'PENDING'
#         ORDER BY ApprovalBatchId DESC
#         """,
#         prospect_id,
#     )

#     row = cursor.fetchone()

#     if row is not None:
#         raise ApprovalConflictError(
#             "A pending approval batch already exists. "
#             f"ApprovalBatchId={row[0]}"
#         )



# def _ensure_no_existing_batch_for_assessment(
#     cursor: pyodbc.Cursor,
#     risk_assessment_id: int,
# ) -> None:
#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             ApprovalBatchId,
#             BatchStatus
#         FROM {APPROVAL_BATCH_TABLE}
#         WITH
#         (
#             UPDLOCK,
#             HOLDLOCK
#         )
#         WHERE RiskAssessmentId = ?
#         ORDER BY ApprovalBatchId DESC
#         """,
#         risk_assessment_id,
#     )

#     row = cursor.fetchone()

#     if row is not None:
#         raise ApprovalConflictError(
#             "An approval batch already exists for "
#             f"RiskAssessmentId={risk_assessment_id}. "
#             f"ApprovalBatchId={row[0]}, "
#             f"BatchStatus={row[1]}. "
#             "Create a new risk-assessment cycle before "
#             "submitting again."
#         )

# def create_approval_workflow_in_transaction(
#     cursor: pyodbc.Cursor,
#     risk_assessment_id: int,
#     prospect_id: str,
#     initiated_by_user_id: int,
# ) -> dict[str, Any]:
#     """Create a batch and dynamic assignments in caller transaction."""

#     prospect_id = _normalize_prospect_id(
#         prospect_id
#     )

#     if risk_assessment_id <= 0:
#         raise ApprovalConflictError(
#             "risk_assessment_id must be greater than zero"
#         )

#     if initiated_by_user_id <= 0:
#         raise ApprovalConflictError(
#             "initiated_by_user_id must be greater than zero"
#         )

#     _ensure_no_existing_batch_for_assessment(
#         cursor,
#         risk_assessment_id,
#     )

#     _ensure_no_pending_batch(
#         cursor,
#         prospect_id,
#     )

#     approvers = _fetch_dynamic_approvers(
#         cursor
#     )

#     cursor.execute(
#         f"""
#         INSERT INTO {APPROVAL_BATCH_TABLE}
#         (
#             RiskAssessmentId,
#             ProspectId,
#             RequiredApproverCount,
#             BatchStatus,
#             AssignedByUserId,
#             CreatedAt,
#             CompletedAt
#         )
#         OUTPUT INSERTED.ApprovalBatchId
#         VALUES
#         (
#             ?,
#             ?,
#             ?,
#             N'PENDING',
#             ?,
#             SYSUTCDATETIME(),
#             NULL
#         )
#         """,
#         (
#             risk_assessment_id,
#             prospect_id,
#             len(approvers),
#             initiated_by_user_id,
#         ),
#     )

#     approval_batch_id = int(
#         cursor.fetchone()[0]
#     )

#     assignments: list[dict[str, Any]] = []

#     for approver in approvers:
#         cursor.execute(
#             f"""
#             INSERT INTO {APPROVAL_ASSIGNMENT_TABLE}
#             (
#                 ApprovalBatchId,
#                 ProspectId,
#                 ApproverUserId,
#                 ApproverRoleCode,
#                 DecisionStatus,
#                 DecisionRemarks,
#                 AssignedAt,
#                 DecisionAt
#             )
#             OUTPUT INSERTED.ApprovalAssignmentId
#             VALUES
#             (
#                 ?,
#                 ?,
#                 ?,
#                 ?,
#                 N'PENDING',
#                 NULL,
#                 SYSUTCDATETIME(),
#                 NULL
#             )
#             """,
#             (
#                 approval_batch_id,
#                 prospect_id,
#                 int(approver["UserId"]),
#                 str(approver["RoleName"]),
#             ),
#         )

#         assignment_id = int(
#             cursor.fetchone()[0]
#         )

#         assignments.append(
#             {
#                 "APPROVAL_ASSIGNMENT_ID": assignment_id,
#                 "APPROVAL_BATCH_ID": approval_batch_id,
#                 "PROSPECT_ID": prospect_id,
#                 "APPROVER_USER_ID": int(
#                     approver["UserId"]
#                 ),
#                 "APPROVER_ROLE_CODE": approver.get(
#                     "RoleName"
#                 ),
#                 "USERNAME": approver.get(
#                     "Username"
#                 ),
#                 "FULL_NAME": approver.get(
#                     "FullName"
#                 ),
#                 "EMAIL": approver.get(
#                     "Email"
#                 ),
#                 "DECISION_STATUS": "PENDING",
#             }
#         )

#     return {
#         "APPROVAL_BATCH_ID": approval_batch_id,
#         "BATCH_STATUS": "PENDING",
#         "get_secondary_connection": len(
#             approvers
#         ),
#         "ASSIGNMENTS": assignments,
#     }


# def _get_latest_approval_batch(
#     cursor: pyodbc.Cursor,
#     prospect_id: str,
# ) -> dict[str, Any]:
#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             ApprovalBatchId,
#             RiskAssessmentId,
#             ProspectId,
#             RequiredApproverCount,
#             BatchStatus,
#             AssignedByUserId,
#             CreatedAt,
#             CompletedAt
#         FROM {APPROVAL_BATCH_TABLE}
#         WHERE ProspectId = ?
#         ORDER BY ApprovalBatchId DESC
#         """,
#         prospect_id,
#     )

#     batch = _fetchone_as_dict(
#         cursor
#     )

#     if batch is None:
#         raise ApprovalBatchNotFoundError(
#             "Approval batch was not found for "
#             f"ProspectId={prospect_id}"
#         )

#     return batch


# def _get_prospect_summary(
#     cursor: pyodbc.Cursor,
#     prospect_id: str,
# ) -> dict[str, Any]:
#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             prospect.ProspectId,
#             prospect.VendorAccount,
#             prospect.Name,
#             prospect.Email,
#             registration.CompanyName,
#             registration.Status
#                 AS VendorRegistrationStatus
#         FROM {PROSPECT_TABLE} AS prospect
#         LEFT JOIN {REGISTRATION_TABLE} AS registration
#             ON registration.ProspectId = prospect.ProspectId
#         WHERE prospect.ProspectId = ?
#         """,
#         prospect_id,
#     )

#     row = _fetchone_as_dict(
#         cursor
#     )

#     if row is not None:
#         return {
#             "PROSPECT_ID": row.get("ProspectId"),
#             "VENDOR_ACCOUNT": row.get("VendorAccount"),
#             "NAME": row.get("Name"),
#             "EMAIL": row.get("Email"),
#             "COMPANY_NAME": row.get("CompanyName"),
#             "VENDOR_REGISTRATION_STATUS": row.get(
#                 "VendorRegistrationStatus"
#             ),
#         }

#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             ProspectId,
#             CompanyName,
#             Status
#         FROM {REGISTRATION_TABLE}
#         WHERE ProspectId = ?
#         """,
#         prospect_id,
#     )

#     row = _fetchone_as_dict(
#         cursor
#     )

#     return {
#         "PROSPECT_ID": prospect_id,
#         "VENDOR_ACCOUNT": None,
#         "NAME": (
#             row.get("CompanyName")
#             if row
#             else None
#         ),
#         "EMAIL": None,
#         "COMPANY_NAME": (
#             row.get("CompanyName")
#             if row
#             else None
#         ),
#         "VENDOR_REGISTRATION_STATUS": (
#             row.get("Status")
#             if row
#             else None
#         ),
#     }


# def _get_approval_assignments(
#     cursor: pyodbc.Cursor,
#     approval_batch_id: int,
# ) -> list[dict[str, Any]]:
#     cursor.execute(
#     f"""
#     SELECT
#         assignment.ApprovalAssignmentId,
#         assignment.ApprovalBatchId,
#         assignment.ProspectId,
#         assignment.ApproverUserId,
#         assignment.ApproverRoleCode,
#         assignment.DecisionStatus,
#         assignment.DecisionRemarks,
#         assignment.AssignedAt,
#         assignment.DecisionAt,
#         approval_user.Username,
#         approval_user.FullName,
#         approval_user.Email
#     FROM {APPROVAL_ASSIGNMENT_TABLE} assignment
#     LEFT JOIN {USERS_TABLE} approval_user
#         ON approval_user.UserId = assignment.ApproverUserId
#     LEFT JOIN {USER_ROLES_TABLE} ur
#         ON ur.UserId = approval_user.UserId
#     LEFT JOIN {ROLES_TABLE} r
#         ON r.RoleId = ur.RoleId
#     WHERE assignment.ApprovalBatchId = ?
#       AND (
#             assignment.DecisionStatus IN ('APPROVED','REJECTED')
#          OR (
#                 assignment.DecisionStatus = 'PENDING'
#             AND approval_user.IsActive = 1
#             AND ISNULL(approval_user.IsLocked,0) = 0
#             AND r.RoleName = ?
#             AND r.IsActive = 1
#          )
#       )
#     ORDER BY assignment.ApprovalAssignmentId
#     """,
#     (
#         approval_batch_id,
#         APPROVER_ROLE_NAME,
#     ),
# )
#     # cursor.execute(
#     #     f"""
#     #     SELECT
#     #         assignment.ApprovalAssignmentId,
#     #         assignment.ApprovalBatchId,
#     #         assignment.ProspectId,
#     #         assignment.ApproverUserId,
#     #         assignment.ApproverRoleCode,
#     #         assignment.DecisionStatus,
#     #         assignment.DecisionRemarks,
#     #         assignment.AssignedAt,
#     #         assignment.DecisionAt,
#     #         approval_user.Username,
#     #         approval_user.FullName,
#     #         approval_user.Email
#     #     FROM {APPROVAL_ASSIGNMENT_TABLE} AS assignment
#     #     LEFT JOIN {USERS_TABLE} AS approval_user
#     #         ON approval_user.UserId = assignment.ApproverUserId
#     #     WHERE assignment.ApprovalBatchId = ?
#     #     ORDER BY assignment.ApprovalAssignmentId
#     #     """,
#     #     approval_batch_id,
#     # )

#     return _fetchall_as_dicts(
#         cursor
#     )


# def _calculate_batch_counts(
#     cursor: pyodbc.Cursor,
#     approval_batch_id: int,
# ) -> dict[str, int]:
#     cursor.execute(
#         f"""
#         SELECT
#             COUNT(*) AS TotalCount,
#             SUM(
#                 CASE
#                     WHEN DecisionStatus = N'APPROVED'
#                     THEN 1
#                     ELSE 0
#                 END
#             ) AS ApprovedCount,
#             SUM(
#                 CASE
#                     WHEN DecisionStatus = N'REJECTED'
#                     THEN 1
#                     ELSE 0
#                 END
#             ) AS RejectedCount,
#             SUM(
#                 CASE
#                     WHEN DecisionStatus = N'PENDING'
#                     THEN 1
#                     ELSE 0
#                 END
#             ) AS PendingCount
#         FROM {APPROVAL_ASSIGNMENT_TABLE}
#         WHERE ApprovalBatchId = ?
#         """,
#         approval_batch_id,
#     )

#     row = cursor.fetchone()

#     return {
#         "TOTAL": int(row[0] or 0),
#         "APPROVED": int(row[1] or 0),
#         "REJECTED": int(row[2] or 0),
#         "PENDING": int(row[3] or 0),
#     }


# def fetch_approval_dashboard_sync(
#     payload: ApprovalFetchRequest,
# ) -> dict[str, Any]:
#     prospect_id = _normalize_prospect_id(
#         payload.ProspectId
#     )
#     user_id = int(payload.UserId)

#     if user_id <= 0:
#         raise ApprovalDecisionError(
#             "UserId must be greater than zero"
#         )

#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             batch = _get_latest_approval_batch(
#                 cursor,
#                 prospect_id,
#             )

#             batch_id = int(
#                 batch["ApprovalBatchId"]
#             )

#             prospect = _get_prospect_summary(
#                 cursor,
#                 prospect_id,
#             )

#             rows = _get_approval_assignments(
#                 cursor,
#                 batch_id,
#             )

#             counts = _calculate_batch_counts(
#                 cursor,
#                 batch_id,
#             )

#             approvers: list[dict[str, Any]] = []
#             current_user_assignment = None

#             for row in rows:
#                 status_value = str(
#                     row.get("DecisionStatus")
#                     or "PENDING"
#                 ).strip().upper()

#                 card = {
#                     "APPROVAL_ASSIGNMENT_ID": int(
#                         row["ApprovalAssignmentId"]
#                     ),
#                     "APPROVAL_BATCH_ID": int(
#                         row["ApprovalBatchId"]
#                     ),
#                     "PROSPECT_ID": row.get(
#                         "ProspectId"
#                     ),
#                     "APPROVER_USER_ID": int(
#                         row["ApproverUserId"]
#                     ),
#                     "APPROVER_ROLE_CODE": row.get(
#                         "ApproverRoleCode"
#                     ),
#                     "USERNAME": row.get("Username"),
#                     "FULL_NAME": row.get("FullName"),
#                     "EMAIL": row.get("Email"),
#                     "INITIALS": _initials(
#                         row.get("FullName"),
#                         row.get("Username"),
#                     ),
#                     "DECISION_STATUS": status_value,
#                     "DECISION_REMARKS": row.get(
#                         "DecisionRemarks"
#                     ),
#                     "ASSIGNED_AT": _serialize(
#                         row.get("AssignedAt")
#                     ),
#                     "DECISION_AT": _serialize(
#                         row.get("DecisionAt")
#                     ),
#                     "IS_CURRENT_USER": (
#                         int(row["ApproverUserId"])
#                         == user_id
#                     ),
#                 }

#                 approvers.append(card)

#                 if card["IS_CURRENT_USER"]:
#                     current_user_assignment = {
#                         **card,
#                         "CAN_DECIDE": (
#                             status_value == "PENDING"
#                             and str(
#                                 batch["BatchStatus"]
#                             ).strip().upper()
#                             == "PENDING"
#                         ),
#                     }

#             return {
#                 "SUCCESS": True,
#                 "PROSPECT": prospect,
#                 "APPROVAL_BATCH": {
#                     "APPROVAL_BATCH_ID": batch_id,
#                     "RISK_ASSESSMENT_ID": int(
#                         batch["RiskAssessmentId"]
#                     ),
#                     "PROSPECT_ID": batch.get(
#                         "ProspectId"
#                     ),
#                     # "get_secondary_connection": int(
#                     #     batch["RequiredApproverCount"]
#                     # ),
#                     "get_secondary_connection": counts["TOTAL"],
#                     "ORIGINAL_get_secondary_connection": int(
#                         batch["RequiredApproverCount"] or counts["TOTAL"]
#                     ),
#                     "BATCH_STATUS": batch.get(
#                         "BatchStatus"
#                     ),
#                     "ASSIGNED_BY_USER_ID": int(
#                         batch["AssignedByUserId"]
#                     ),
#                     "CREATED_AT": _serialize(
#                         batch.get("CreatedAt")
#                     ),
#                     "COMPLETED_AT": _serialize(
#                         batch.get("CompletedAt")
#                     ),
#                 },
#                 "SUMMARY": {
#                     "TOTAL_COUNT": counts["TOTAL"],
#                     "COMPLETED_COUNT": (
#                         counts["APPROVED"]
#                         + counts["REJECTED"]
#                     ),
#                     "APPROVED_COUNT": counts[
#                         "APPROVED"
#                     ],
#                     "REJECTED_COUNT": counts[
#                         "REJECTED"
#                     ],
#                     "PENDING_COUNT": counts[
#                         "PENDING"
#                     ],
#                 },
#                 "APPROVERS": approvers,
#                 "CURRENT_USER_DECISION": {
#                     "USER_ID": user_id,
#                     "HAS_ASSIGNMENT": (
#                         current_user_assignment
#                         is not None
#                     ),
#                     "CAN_DECIDE": bool(
#                         current_user_assignment
#                         and current_user_assignment[
#                             "CAN_DECIDE"
#                         ]
#                     ),
#                     "ASSIGNMENT": (
#                         current_user_assignment
#                     ),
#                 },
#             }

#         finally:
#             cursor.close()


# def _insert_action_log(
#     cursor: pyodbc.Cursor,
#     approval_assignment_id: int,
#     action_type: str,
#     previous_status: str,
#     new_status: str,
#     action_by_user_id: int,
#     remarks: str | None,
# ) -> None:
#     cursor.execute(
#         f"""
#         INSERT INTO {APPROVAL_ACTION_LOG_TABLE}
#         (
#             ApprovalAssignmentId,
#             ActionType,
#             PreviousStatus,
#             NewStatus,
#             ActionByUserId,
#             Remarks,
#             ActionAt
#         )
#         VALUES
#         (
#             ?,
#             ?,
#             ?,
#             ?,
#             ?,
#             ?,
#             SYSUTCDATETIME()
#         )
#         """,
#         (
#             approval_assignment_id,
#             action_type,
#             previous_status,
#             new_status,
#             action_by_user_id,
#             remarks,
#         ),
#     )


# def _update_batch_status(
#     cursor: pyodbc.Cursor,
#     approval_batch_id: int,
#     batch_status: str,
# ) -> None:
#     cursor.execute(
#         f"""
#         UPDATE {APPROVAL_BATCH_TABLE}
#         SET
#             BatchStatus = ?,
#             CompletedAt = CASE
#                 WHEN ? = N'PENDING'
#                 THEN NULL
#                 ELSE SYSUTCDATETIME()
#             END
#         WHERE ApprovalBatchId = ?
#         """,
#         (
#             batch_status,
#             batch_status,
#             approval_batch_id,
#         ),
#     )


# def _update_risk_status(
#     cursor: pyodbc.Cursor,
#     risk_assessment_id: int,
#     prospect_id: str,
#     status_value: str,
# ) -> None:
#     cursor.execute(
#         f"""
#         UPDATE {RISK_HEADER_TABLE}
#         SET
#             AssessmentStatus = ?,
#             ModifiedAt = SYSUTCDATETIME()
#         WHERE RiskAssessmentId = ?
#           AND ProspectId = ?
#         """,
#         (
#             status_value,
#             risk_assessment_id,
#             prospect_id,
#         ),
#     )


# def _update_registration_status(
#     cursor: pyodbc.Cursor,
#     prospect_id: str,
#     status_value: str,
#     is_draft: int,
# ) -> None:
#     cursor.execute(
#         f"""
#         UPDATE {REGISTRATION_TABLE}
#         SET
#             Status = ?,
#             IsDraft = ?,
#             ModifiedOn = SYSUTCDATETIME()
#         WHERE ProspectId = ?
#         """,
#         (
#             status_value,
#             is_draft,
#             prospect_id,
#         ),
#     )


# def submit_approval_decision_sync(
#     payload: ApprovalSubmitRequest,
# ) -> dict[str, Any]:
#     prospect_id = _normalize_prospect_id(
#         payload.ProspectId
#     )
#     assignment_id = int(
#         payload.ApprovalAssignmentId
#     )
#     user_id = int(payload.UserId)

#     decision = (
#         payload.Decision.value
#         if hasattr(payload.Decision, "value")
#         else str(payload.Decision)
#     ).strip().upper()

#     remarks = _normalize_text(
#         payload.Remarks
#     )

#     if decision not in {
#         "APPROVE",
#         "REJECT",
#     }:
#         raise ApprovalDecisionError(
#             "Decision must be APPROVE or REJECT"
#         )

#     if decision == "REJECT" and not remarks:
#         raise ApprovalDecisionError(
#             "Remarks is required when Decision is REJECT"
#         )

#     should_trigger_d365 = False

#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 "SET XACT_ABORT ON;"
#             )

#             cursor.execute(
#                 f"""
#                 SELECT
#                     assignment.ApprovalAssignmentId,
#                     assignment.ApprovalBatchId,
#                     assignment.ProspectId,
#                     assignment.ApproverUserId,
#                     assignment.DecisionStatus,
#                     batch.RiskAssessmentId,
#                     batch.BatchStatus
#                 FROM {APPROVAL_ASSIGNMENT_TABLE}
#                      AS assignment
#                 INNER JOIN {APPROVAL_BATCH_TABLE}
#                            AS batch
#                     ON batch.ApprovalBatchId =
#                        assignment.ApprovalBatchId
#                    AND batch.ProspectId =
#                        assignment.ProspectId
#                 WHERE assignment.ApprovalAssignmentId = ?
#                   AND assignment.ProspectId = ?
#                 """,
#                 (
#                     assignment_id,
#                     prospect_id,
#                 ),
#             )

#             assignment = _fetchone_as_dict(
#                 cursor
#             )

#             if assignment is None:
#                 raise ApprovalAssignmentNotFoundError(
#                     "Approval assignment was not found: "
#                     f"{assignment_id}"
#                 )

#             if int(
#                 assignment["ApproverUserId"]
#             ) != user_id:
#                 raise ApprovalAuthorizationError(
#                     "This assignment does not belong to "
#                     "the supplied UserId"
#                 )

#             previous_status = str(
#                 assignment["DecisionStatus"]
#                 or "PENDING"
#             ).strip().upper()

#             current_batch_status = str(
#                 assignment["BatchStatus"]
#                 or "PENDING"
#             ).strip().upper()

#             if current_batch_status != "PENDING":
#                 raise ApprovalDecisionError(
#                     "This batch is already completed. "
#                     f"Current status={current_batch_status}"
#                 )

#             if previous_status != "PENDING":
#                 raise ApprovalDecisionError(
#                     "This assignment is already decided. "
#                     f"Current status={previous_status}"
#                 )

#             new_status = (
#                 "APPROVED"
#                 if decision == "APPROVE"
#                 else "REJECTED"
#             )

#             cursor.execute(
#                 f"""
#                 UPDATE {APPROVAL_ASSIGNMENT_TABLE}
#                 SET
#                     DecisionStatus = ?,
#                     DecisionRemarks = ?,
#                     DecisionAt = SYSUTCDATETIME()
#                 WHERE ApprovalAssignmentId = ?
#                   AND ApproverUserId = ?
#                   AND DecisionStatus = N'PENDING'
#                 """,
#                 (
#                     new_status,
#                     remarks,
#                     assignment_id,
#                     user_id,
#                 ),
#             )

#             if cursor.rowcount != 1:
#                 raise ApprovalConflictError(
#                     "The assignment changed in another request. "
#                     "Refresh and retry."
#                 )

#             _insert_action_log(
#                 cursor=cursor,
#                 approval_assignment_id=(
#                     assignment_id
#                 ),
#                 action_type=decision,
#                 previous_status=previous_status,
#                 new_status=new_status,
#                 action_by_user_id=user_id,
#                 remarks=remarks,
#             )

#             approval_batch_id = int(
#                 assignment["ApprovalBatchId"]
#             )
#             risk_assessment_id = int(
#                 assignment["RiskAssessmentId"]
#             )

#             counts = _calculate_batch_counts(
#                 cursor,
#                 approval_batch_id,
#             )

#             if counts["PENDING"] > 0:
#                 final_batch_status = "PENDING"
#                 final_vendor_status = None
#                 final_risk_status = None
#                 final_is_draft = None

#             elif counts["REJECTED"] > 0:
#                 final_batch_status = "RETURNED"
#                 final_vendor_status = "RETURNED"
#                 final_risk_status = "RETURNED"
#                 final_is_draft = 0

#             else:
#                 final_batch_status = "APPROVED"
#                 final_vendor_status = "APPROVED"
#                 final_risk_status = "APPROVED"
#                 final_is_draft = 1
#                 should_trigger_d365 = True

#             _update_batch_status(
#                 cursor,
#                 approval_batch_id,
#                 final_batch_status,
#             )

#             if final_risk_status is not None:
#                 _update_risk_status(
#                     cursor,
#                     risk_assessment_id,
#                     prospect_id,
#                     final_risk_status,
#                 )

#             if final_vendor_status is not None:
#                 _update_registration_status(
#                     cursor,
#                     prospect_id,
#                     final_vendor_status,
#                     final_is_draft,
#                 )

#             connection.commit()

#         except Exception:
#             connection.rollback()
#             raise

#         finally:
#             cursor.close()

#     d365_result = None

#     if should_trigger_d365:
#         d365_result = create_vendor_in_d365(
#             prospect_id
#         )

#         if d365_result.get("SUCCESS"):
#             final_vendor_status = "VENDOR_CREATED"

#     return {
#         "SUCCESS": True,
#         "MESSAGE": (
#             "Approval decision saved successfully"
#         ),
#         "PROSPECT_ID": prospect_id,
#         "APPROVAL_ASSIGNMENT_ID": assignment_id,
#         "APPROVAL_BATCH_ID": approval_batch_id,
#         "APPROVER_USER_ID": user_id,
#         "YOUR_DECISION": new_status,
#         "COUNTS": counts,
#         "FINAL_BATCH_STATUS": final_batch_status,
#         "FINAL_VENDOR_REGISTRATION_STATUS": (
#             final_vendor_status
#         ),
#         "FINAL_RISK_ASSESSMENT_STATUS": (
#             final_risk_status
#         ),
#         "ISDRAFT": final_is_draft,
#         "D365_TRIGGER_RESULT": d365_result,
#     }


# def retry_d365_vendor_creation_sync(
#     prospect_id: str,
# ) -> dict[str, Any]:

#     prospect_id = str(
#         prospect_id or ""
#     ).strip().upper()

#     if not prospect_id:
#         raise ApprovalConflictError(
#             "ProspectId is required"
#         )

#     approval_batch_id: int | None = None
#     batch_status: str | None = None

#     # ========================================================
#     # VALIDATE APPROVAL STATUS
#     # ========================================================
#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 f"""
#                 SELECT TOP 1
#                     ApprovalBatchId,
#                     BatchStatus
#                 FROM {APPROVAL_BATCH_TABLE}
#                 WHERE ProspectId = ?
#                 ORDER BY ApprovalBatchId DESC
#                 """,
#                 prospect_id,
#             )

#             batch_row = cursor.fetchone()

#             if batch_row is None:
#                 raise ApprovalConflictError(
#                     "No approval batch was found for "
#                     f"ProspectId={prospect_id}"
#                 )

#             approval_batch_id = int(
#                 batch_row[0]
#             )

#             batch_status = str(
#                 batch_row[1] or ""
#             ).strip().upper()

#             if batch_status != "APPROVED":
#                 raise ApprovalConflictError(
#                     "D365 vendor creation can only be retried "
#                     "after final approval. "
#                     f"Current batch status={batch_status}"
#                 )

#         finally:
#             cursor.close()

#     # ========================================================
#     # RETRY D365 VENDOR CREATION
#     # ========================================================
#     try:
#         logger.info(
#             "Retrying D365 vendor creation for "
#             "ProspectId=%s, ApprovalBatchId=%s",
#             prospect_id,
#             approval_batch_id,
#         )

#         d365_result = create_vendor_in_d365(
#             prospect_id=prospect_id,
#         )

#     except Exception as exc:
#         logger.exception(
#             "D365 retry raised an exception for "
#             "ProspectId=%s",
#             prospect_id,
#         )

#         return {
#             "SUCCESS": False,
#             "MESSAGE": "D365 vendor creation retry failed",
#             "PROSPECT_ID": prospect_id,
#             "APPROVAL_BATCH_ID": approval_batch_id,
#             "APPROVAL_STATUS": batch_status,
#             "D365_TRIGGERED": True,
#             "D365_RESULT": None,
#             "HTTP_STATUS": None,
#             "ERROR": str(exc),
#         }

#     # ========================================================
#     # VALIDATE D365 SERVICE RESPONSE
#     # ========================================================
#     if not isinstance(d365_result, dict):
#         return {
#             "SUCCESS": False,
#             "MESSAGE": (
#                 "D365 vendor creation returned an invalid response"
#             ),
#             "PROSPECT_ID": prospect_id,
#             "APPROVAL_BATCH_ID": approval_batch_id,
#             "APPROVAL_STATUS": batch_status,
#             "D365_TRIGGERED": True,
#             "D365_RESULT": d365_result,
#             "HTTP_STATUS": None,
#             "ERROR": (
#                 "Expected D365 response to be a dictionary"
#             ),
#         }

#     d365_success = bool(
#         d365_result.get("SUCCESS", False)
#     )

#     d365_http_status = d365_result.get(
#         "HTTP_STATUS"
#     )

#     d365_error = d365_result.get(
#         "ERROR"
#     )

#     if not d365_success:
#         logger.error(
#             "D365 vendor creation retry failed for "
#             "ProspectId=%s. HTTP_STATUS=%s, ERROR=%s",
#             prospect_id,
#             d365_http_status,
#             d365_error,
#         )

#         return {
#             "SUCCESS": False,
#             "MESSAGE": "D365 vendor creation retry failed",
#             "PROSPECT_ID": prospect_id,
#             "APPROVAL_BATCH_ID": approval_batch_id,
#             "APPROVAL_STATUS": batch_status,
#             "D365_TRIGGERED": bool(
#                 d365_result.get("TRIGGERED", True)
#             ),
#             "D365_RESULT": d365_result,
#             "HTTP_STATUS": d365_http_status,
#             "ERROR": (
#                 d365_error
#                 or "D365 vendor creation was unsuccessful"
#             ),
#         }

#     logger.info(
#         "D365 vendor creation retry succeeded for "
#         "ProspectId=%s",
#         prospect_id,
#     )

#     return {
#         "SUCCESS": True,
#         "MESSAGE": (
#             "D365 vendor creation completed successfully"
#         ),
#         "PROSPECT_ID": prospect_id,
#         "APPROVAL_BATCH_ID": approval_batch_id,
#         "APPROVAL_STATUS": batch_status,
#         "D365_TRIGGERED": bool(
#             d365_result.get("TRIGGERED", True)
#         ),
#         "D365_RESULT": d365_result,
#         "HTTP_STATUS": d365_http_status,
#         "ERROR": None,
#     }



# # from __future__ import annotations

# # import os
# # from datetime import date, datetime
# # from typing import Any

# # import pyodbc

# # from app.core.config import settings
# # from app.db.base import get_secondary_connection
# # from app.schemas.vendorapproval_schema import (
# #     ApprovalFetchRequest,
# #     ApprovalSubmitRequest,
# # )
# # from app.services.d365_vendor_creation_service import (
# #     create_vendor_in_d365,
# # )
# # import logging

# # logger = logging.getLogger(__name__)

# # DB_SCHEMA = getattr(
# #     settings,
# #     "SECONDARY_DB_SCHEMA",
# #     getattr(settings, "DB_SCHEMA", "dev"),
# # )

# # USERS_TABLE = f"[{DB_SCHEMA}].[HIQ_Users]"
# # ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_Roles]"
# # USER_ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_UserRoles]"

# # APPROVAL_BATCH_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
# # )
# # APPROVAL_ASSIGNMENT_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorApprovalAssignment]"
# # )
# # APPROVAL_ACTION_LOG_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorApprovalActionLog]"
# # )
# # RISK_HEADER_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
# # )
# # REGISTRATION_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
# # )
# # PROSPECT_TABLE = (
# #     f"[{DB_SCHEMA}].[d365_VendorProspect]"
# # )

# # APPROVER_ROLE_NAME = os.getenv(
# #     "VENDOR_APPROVER_ROLE_NAME",
# #     "Approval",
# # ).strip()


# # class ApprovalBatchNotFoundError(LookupError):
# #     pass


# # class ApprovalAssignmentNotFoundError(LookupError):
# #     pass


# # class ApprovalAuthorizationError(PermissionError):
# #     pass


# # class ApprovalDecisionError(ValueError):
# #     pass


# # class ApprovalConflictError(ValueError):
# #     pass


# # class ApprovalConfigurationError(ValueError):
# #     pass


# # ApprovalNotFoundError = ApprovalBatchNotFoundError


# # def _serialize(value: Any) -> Any:
# #     if isinstance(value, (date, datetime)):
# #         return value.isoformat()

# #     return value


# # def _normalize_text(value: Any) -> str | None:
# #     if value is None:
# #         return None

# #     text = str(value).strip()
# #     return text or None


# # def _normalize_prospect_id(value: Any) -> str:
# #     prospect_id = str(
# #         value or ""
# #     ).strip().upper()

# #     if not prospect_id:
# #         raise ApprovalDecisionError(
# #             "ProspectId is required"
# #         )

# #     return prospect_id


# # def _fetchone_as_dict(
# #     cursor: pyodbc.Cursor,
# # ) -> dict[str, Any] | None:
# #     row = cursor.fetchone()

# #     if row is None:
# #         return None

# #     columns = [
# #         column[0]
# #         for column in cursor.description
# #     ]

# #     return dict(zip(columns, row))


# # def _fetchall_as_dicts(
# #     cursor: pyodbc.Cursor,
# # ) -> list[dict[str, Any]]:
# #     rows = cursor.fetchall()

# #     if cursor.description is None:
# #         return []

# #     columns = [
# #         column[0]
# #         for column in cursor.description
# #     ]

# #     return [
# #         dict(zip(columns, row))
# #         for row in rows
# #     ]


# # def _initials(
# #     full_name: Any,
# #     username: Any,
# # ) -> str:
# #     source = str(
# #         full_name
# #         or username
# #         or "User"
# #     ).strip()

# #     parts = [
# #         part
# #         for part in source.split()
# #         if part
# #     ]

# #     if not parts:
# #         return "U"

# #     if len(parts) == 1:
# #         return parts[0][:2].upper()

# #     return (
# #         parts[0][0]
# #         + parts[-1][0]
# #     ).upper()


# # def _fetch_dynamic_approvers(
# #     cursor: pyodbc.Cursor,
# # ) -> list[dict[str, Any]]:
# #     if not APPROVER_ROLE_NAME:
# #         raise ApprovalConfigurationError(
# #             "VENDOR_APPROVER_ROLE_NAME is empty"
# #         )

# #     cursor.execute(
# #         f"""
# #         SELECT TOP 1
# #             RoleId,
# #             RoleName
# #         FROM {ROLES_TABLE}
# #         WHERE UPPER(LTRIM(RTRIM(RoleName))) = UPPER(?)
# #           AND IsActive = 1
# #         ORDER BY RoleId
# #         """,
# #         APPROVER_ROLE_NAME,
# #     )

# #     role_row = cursor.fetchone()

# #     if role_row is None:
# #         raise ApprovalConfigurationError(
# #             "Active approval role was not found. "
# #             f"Expected RoleName='{APPROVER_ROLE_NAME}'"
# #         )

# #     role_id = int(role_row[0])
# #     role_name = str(role_row[1]).strip()

# #     cursor.execute(
# #         f"""
# #         SELECT DISTINCT
# #             approval_user.UserId,
# #             approval_user.Username,
# #             approval_user.FullName,
# #             approval_user.Email,
# #             approval_role.RoleId,
# #             approval_role.RoleName
# #         FROM {USER_ROLES_TABLE} AS user_role
# #         INNER JOIN {USERS_TABLE} AS approval_user
# #             ON approval_user.UserId = user_role.UserId
# #         INNER JOIN {ROLES_TABLE} AS approval_role
# #             ON approval_role.RoleId = user_role.RoleId
# #         WHERE user_role.RoleId = ?
# #           AND approval_user.IsActive = 1
# #           AND ISNULL(approval_user.IsLocked, 0) = 0
# #           AND approval_role.IsActive = 1
# #         ORDER BY approval_user.UserId
# #         """,
# #         role_id,
# #     )

# #     approvers = _fetchall_as_dicts(
# #         cursor
# #     )

# #     if not approvers:
# #         raise ApprovalConfigurationError(
# #             "No active users are mapped to "
# #             f"RoleName='{role_name}'"
# #         )

# #     # if len(approvers) < 3 or len(approvers) > 5:
# #     #     raise ApprovalConfigurationError(
# #     #         "Approval-role users must be between 3 and 5 "
# #     #         "because RequiredApproverCount is constrained "
# #     #         f"to 3-5. Current count={len(approvers)}"
# #     #     )

# #     return approvers


# # def _ensure_no_pending_batch(
# #     cursor: pyodbc.Cursor,
# #     prospect_id: str,
# # ) -> None:
# #     cursor.execute(
# #         f"""
# #         SELECT TOP 1
# #             ApprovalBatchId
# #         FROM {APPROVAL_BATCH_TABLE}
# #         WHERE ProspectId = ?
# #           AND BatchStatus = N'PENDING'
# #         ORDER BY ApprovalBatchId DESC
# #         """,
# #         prospect_id,
# #     )

# #     row = cursor.fetchone()

# #     if row is not None:
# #         raise ApprovalConflictError(
# #             "A pending approval batch already exists. "
# #             f"ApprovalBatchId={row[0]}"
# #         )


# # def create_approval_workflow_in_transaction(
# #     cursor: pyodbc.Cursor,
# #     risk_assessment_id: int,
# #     prospect_id: str,
# #     initiated_by_user_id: int,
# # ) -> dict[str, Any]:
# #     """Create a batch and dynamic assignments in caller transaction."""

# #     prospect_id = _normalize_prospect_id(
# #         prospect_id
# #     )

# #     if risk_assessment_id <= 0:
# #         raise ApprovalConflictError(
# #             "risk_assessment_id must be greater than zero"
# #         )

# #     if initiated_by_user_id <= 0:
# #         raise ApprovalConflictError(
# #             "initiated_by_user_id must be greater than zero"
# #         )

# #     _ensure_no_pending_batch(
# #         cursor,
# #         prospect_id,
# #     )

# #     approvers = _fetch_dynamic_approvers(
# #         cursor
# #     )

# #     cursor.execute(
# #         f"""
# #         INSERT INTO {APPROVAL_BATCH_TABLE}
# #         (
# #             RiskAssessmentId,
# #             ProspectId,
# #             RequiredApproverCount,
# #             BatchStatus,
# #             AssignedByUserId,
# #             CreatedAt,
# #             CompletedAt
# #         )
# #         OUTPUT INSERTED.ApprovalBatchId
# #         VALUES
# #         (
# #             ?,
# #             ?,
# #             ?,
# #             N'PENDING',
# #             ?,
# #             SYSUTCDATETIME(),
# #             NULL
# #         )
# #         """,
# #         (
# #             risk_assessment_id,
# #             prospect_id,
# #             len(approvers),
# #             initiated_by_user_id,
# #         ),
# #     )

# #     approval_batch_id = int(
# #         cursor.fetchone()[0]
# #     )

# #     assignments: list[dict[str, Any]] = []

# #     for approver in approvers:
# #         cursor.execute(
# #             f"""
# #             INSERT INTO {APPROVAL_ASSIGNMENT_TABLE}
# #             (
# #                 ApprovalBatchId,
# #                 ProspectId,
# #                 ApproverUserId,
# #                 ApproverRoleCode,
# #                 DecisionStatus,
# #                 DecisionRemarks,
# #                 AssignedAt,
# #                 DecisionAt
# #             )
# #             OUTPUT INSERTED.ApprovalAssignmentId
# #             VALUES
# #             (
# #                 ?,
# #                 ?,
# #                 ?,
# #                 ?,
# #                 N'PENDING',
# #                 NULL,
# #                 SYSUTCDATETIME(),
# #                 NULL
# #             )
# #             """,
# #             (
# #                 approval_batch_id,
# #                 prospect_id,
# #                 int(approver["UserId"]),
# #                 str(approver["RoleName"]),
# #             ),
# #         )

# #         assignment_id = int(
# #             cursor.fetchone()[0]
# #         )

# #         assignments.append(
# #             {
# #                 "APPROVAL_ASSIGNMENT_ID": assignment_id,
# #                 "APPROVAL_BATCH_ID": approval_batch_id,
# #                 "PROSPECT_ID": prospect_id,
# #                 "APPROVER_USER_ID": int(
# #                     approver["UserId"]
# #                 ),
# #                 "APPROVER_ROLE_CODE": approver.get(
# #                     "RoleName"
# #                 ),
# #                 "USERNAME": approver.get(
# #                     "Username"
# #                 ),
# #                 "FULL_NAME": approver.get(
# #                     "FullName"
# #                 ),
# #                 "EMAIL": approver.get(
# #                     "Email"
# #                 ),
# #                 "DECISION_STATUS": "PENDING",
# #             }
# #         )

# #     return {
# #         "APPROVAL_BATCH_ID": approval_batch_id,
# #         "BATCH_STATUS": "PENDING",
# #         "get_secondary_connection": len(
# #             approvers
# #         ),
# #         "ASSIGNMENTS": assignments,
# #     }


# # def _get_latest_approval_batch(
# #     cursor: pyodbc.Cursor,
# #     prospect_id: str,
# # ) -> dict[str, Any]:
# #     cursor.execute(
# #         f"""
# #         SELECT TOP 1
# #             ApprovalBatchId,
# #             RiskAssessmentId,
# #             ProspectId,
# #             RequiredApproverCount,
# #             BatchStatus,
# #             AssignedByUserId,
# #             CreatedAt,
# #             CompletedAt
# #         FROM {APPROVAL_BATCH_TABLE}
# #         WHERE ProspectId = ?
# #         ORDER BY ApprovalBatchId DESC
# #         """,
# #         prospect_id,
# #     )

# #     batch = _fetchone_as_dict(
# #         cursor
# #     )

# #     if batch is None:
# #         raise ApprovalBatchNotFoundError(
# #             "Approval batch was not found for "
# #             f"ProspectId={prospect_id}"
# #         )

# #     return batch


# # def _get_prospect_summary(
# #     cursor: pyodbc.Cursor,
# #     prospect_id: str,
# # ) -> dict[str, Any]:
# #     cursor.execute(
# #         f"""
# #         SELECT TOP 1
# #             prospect.ProspectId,
# #             prospect.VendorAccount,
# #             prospect.Name,
# #             prospect.Email,
# #             registration.CompanyName,
# #             registration.Status
# #                 AS VendorRegistrationStatus
# #         FROM {PROSPECT_TABLE} AS prospect
# #         LEFT JOIN {REGISTRATION_TABLE} AS registration
# #             ON registration.ProspectId = prospect.ProspectId
# #         WHERE prospect.ProspectId = ?
# #         """,
# #         prospect_id,
# #     )

# #     row = _fetchone_as_dict(
# #         cursor
# #     )

# #     if row is not None:
# #         return {
# #             "PROSPECT_ID": row.get("ProspectId"),
# #             "VENDOR_ACCOUNT": row.get("VendorAccount"),
# #             "NAME": row.get("Name"),
# #             "EMAIL": row.get("Email"),
# #             "COMPANY_NAME": row.get("CompanyName"),
# #             "VENDOR_REGISTRATION_STATUS": row.get(
# #                 "VendorRegistrationStatus"
# #             ),
# #         }

# #     cursor.execute(
# #         f"""
# #         SELECT TOP 1
# #             ProspectId,
# #             CompanyName,
# #             Status
# #         FROM {REGISTRATION_TABLE}
# #         WHERE ProspectId = ?
# #         """,
# #         prospect_id,
# #     )

# #     row = _fetchone_as_dict(
# #         cursor
# #     )

# #     return {
# #         "PROSPECT_ID": prospect_id,
# #         "VENDOR_ACCOUNT": None,
# #         "NAME": (
# #             row.get("CompanyName")
# #             if row
# #             else None
# #         ),
# #         "EMAIL": None,
# #         "COMPANY_NAME": (
# #             row.get("CompanyName")
# #             if row
# #             else None
# #         ),
# #         "VENDOR_REGISTRATION_STATUS": (
# #             row.get("Status")
# #             if row
# #             else None
# #         ),
# #     }


# # def _get_approval_assignments(
# #     cursor: pyodbc.Cursor,
# #     approval_batch_id: int,
# # ) -> list[dict[str, Any]]:
# #     cursor.execute(
# #     f"""
# #     SELECT
# #         assignment.ApprovalAssignmentId,
# #         assignment.ApprovalBatchId,
# #         assignment.ProspectId,
# #         assignment.ApproverUserId,
# #         assignment.ApproverRoleCode,
# #         assignment.DecisionStatus,
# #         assignment.DecisionRemarks,
# #         assignment.AssignedAt,
# #         assignment.DecisionAt,
# #         approval_user.Username,
# #         approval_user.FullName,
# #         approval_user.Email
# #     FROM {APPROVAL_ASSIGNMENT_TABLE} assignment
# #     LEFT JOIN {USERS_TABLE} approval_user
# #         ON approval_user.UserId = assignment.ApproverUserId
# #     LEFT JOIN {USER_ROLES_TABLE} ur
# #         ON ur.UserId = approval_user.UserId
# #     LEFT JOIN {ROLES_TABLE} r
# #         ON r.RoleId = ur.RoleId
# #     WHERE assignment.ApprovalBatchId = ?
# #       AND (
# #             assignment.DecisionStatus IN ('APPROVED','REJECTED')
# #          OR (
# #                 assignment.DecisionStatus = 'PENDING'
# #             AND approval_user.IsActive = 1
# #             AND ISNULL(approval_user.IsLocked,0) = 0
# #             AND r.RoleName = ?
# #             AND r.IsActive = 1
# #          )
# #       )
# #     ORDER BY assignment.ApprovalAssignmentId
# #     """,
# #     (
# #         approval_batch_id,
# #         APPROVER_ROLE_NAME,
# #     ),
# # )
# #     # cursor.execute(
# #     #     f"""
# #     #     SELECT
# #     #         assignment.ApprovalAssignmentId,
# #     #         assignment.ApprovalBatchId,
# #     #         assignment.ProspectId,
# #     #         assignment.ApproverUserId,
# #     #         assignment.ApproverRoleCode,
# #     #         assignment.DecisionStatus,
# #     #         assignment.DecisionRemarks,
# #     #         assignment.AssignedAt,
# #     #         assignment.DecisionAt,
# #     #         approval_user.Username,
# #     #         approval_user.FullName,
# #     #         approval_user.Email
# #     #     FROM {APPROVAL_ASSIGNMENT_TABLE} AS assignment
# #     #     LEFT JOIN {USERS_TABLE} AS approval_user
# #     #         ON approval_user.UserId = assignment.ApproverUserId
# #     #     WHERE assignment.ApprovalBatchId = ?
# #     #     ORDER BY assignment.ApprovalAssignmentId
# #     #     """,
# #     #     approval_batch_id,
# #     # )

# #     return _fetchall_as_dicts(
# #         cursor
# #     )


# # def _calculate_batch_counts(
# #     cursor: pyodbc.Cursor,
# #     approval_batch_id: int,
# # ) -> dict[str, int]:
# #     cursor.execute(
# #         f"""
# #         SELECT
# #             COUNT(*) AS TotalCount,
# #             SUM(
# #                 CASE
# #                     WHEN DecisionStatus = N'APPROVED'
# #                     THEN 1
# #                     ELSE 0
# #                 END
# #             ) AS ApprovedCount,
# #             SUM(
# #                 CASE
# #                     WHEN DecisionStatus = N'REJECTED'
# #                     THEN 1
# #                     ELSE 0
# #                 END
# #             ) AS RejectedCount,
# #             SUM(
# #                 CASE
# #                     WHEN DecisionStatus = N'PENDING'
# #                     THEN 1
# #                     ELSE 0
# #                 END
# #             ) AS PendingCount
# #         FROM {APPROVAL_ASSIGNMENT_TABLE}
# #         WHERE ApprovalBatchId = ?
# #         """,
# #         approval_batch_id,
# #     )

# #     row = cursor.fetchone()

# #     return {
# #         "TOTAL": int(row[0] or 0),
# #         "APPROVED": int(row[1] or 0),
# #         "REJECTED": int(row[2] or 0),
# #         "PENDING": int(row[3] or 0),
# #     }


# # def fetch_approval_dashboard_sync(
# #     payload: ApprovalFetchRequest,
# # ) -> dict[str, Any]:
# #     prospect_id = _normalize_prospect_id(
# #         payload.ProspectId
# #     )
# #     user_id = int(payload.UserId)

# #     if user_id <= 0:
# #         raise ApprovalDecisionError(
# #             "UserId must be greater than zero"
# #         )

# #     with get_secondary_connection() as connection:
# #         cursor = connection.cursor()

# #         try:
# #             batch = _get_latest_approval_batch(
# #                 cursor,
# #                 prospect_id,
# #             )

# #             batch_id = int(
# #                 batch["ApprovalBatchId"]
# #             )

# #             prospect = _get_prospect_summary(
# #                 cursor,
# #                 prospect_id,
# #             )

# #             rows = _get_approval_assignments(
# #                 cursor,
# #                 batch_id,
# #             )

# #             counts = _calculate_batch_counts(
# #                 cursor,
# #                 batch_id,
# #             )

# #             approvers: list[dict[str, Any]] = []
# #             current_user_assignment = None

# #             for row in rows:
# #                 status_value = str(
# #                     row.get("DecisionStatus")
# #                     or "PENDING"
# #                 ).strip().upper()

# #                 card = {
# #                     "APPROVAL_ASSIGNMENT_ID": int(
# #                         row["ApprovalAssignmentId"]
# #                     ),
# #                     "APPROVAL_BATCH_ID": int(
# #                         row["ApprovalBatchId"]
# #                     ),
# #                     "PROSPECT_ID": row.get(
# #                         "ProspectId"
# #                     ),
# #                     "APPROVER_USER_ID": int(
# #                         row["ApproverUserId"]
# #                     ),
# #                     "APPROVER_ROLE_CODE": row.get(
# #                         "ApproverRoleCode"
# #                     ),
# #                     "USERNAME": row.get("Username"),
# #                     "FULL_NAME": row.get("FullName"),
# #                     "EMAIL": row.get("Email"),
# #                     "INITIALS": _initials(
# #                         row.get("FullName"),
# #                         row.get("Username"),
# #                     ),
# #                     "DECISION_STATUS": status_value,
# #                     "DECISION_REMARKS": row.get(
# #                         "DecisionRemarks"
# #                     ),
# #                     "ASSIGNED_AT": _serialize(
# #                         row.get("AssignedAt")
# #                     ),
# #                     "DECISION_AT": _serialize(
# #                         row.get("DecisionAt")
# #                     ),
# #                     "IS_CURRENT_USER": (
# #                         int(row["ApproverUserId"])
# #                         == user_id
# #                     ),
# #                 }

# #                 approvers.append(card)

# #                 if card["IS_CURRENT_USER"]:
# #                     current_user_assignment = {
# #                         **card,
# #                         "CAN_DECIDE": (
# #                             status_value == "PENDING"
# #                             and str(
# #                                 batch["BatchStatus"]
# #                             ).strip().upper()
# #                             == "PENDING"
# #                         ),
# #                     }

# #             return {
# #                 "SUCCESS": True,
# #                 "PROSPECT": prospect,
# #                 "APPROVAL_BATCH": {
# #                     "APPROVAL_BATCH_ID": batch_id,
# #                     "RISK_ASSESSMENT_ID": int(
# #                         batch["RiskAssessmentId"]
# #                     ),
# #                     "PROSPECT_ID": batch.get(
# #                         "ProspectId"
# #                     ),
# #                     "get_secondary_connection": int(
# #                         batch["RequiredApproverCount"]
# #                     ),
# #                     "BATCH_STATUS": batch.get(
# #                         "BatchStatus"
# #                     ),
# #                     "ASSIGNED_BY_USER_ID": int(
# #                         batch["AssignedByUserId"]
# #                     ),
# #                     "CREATED_AT": _serialize(
# #                         batch.get("CreatedAt")
# #                     ),
# #                     "COMPLETED_AT": _serialize(
# #                         batch.get("CompletedAt")
# #                     ),
# #                 },
# #                 "SUMMARY": {
# #                     "TOTAL_COUNT": counts["TOTAL"],
# #                     "COMPLETED_COUNT": (
# #                         counts["APPROVED"]
# #                         + counts["REJECTED"]
# #                     ),
# #                     "APPROVED_COUNT": counts[
# #                         "APPROVED"
# #                     ],
# #                     "REJECTED_COUNT": counts[
# #                         "REJECTED"
# #                     ],
# #                     "PENDING_COUNT": counts[
# #                         "PENDING"
# #                     ],
# #                 },
# #                 "APPROVERS": approvers,
# #                 "CURRENT_USER_DECISION": {
# #                     "USER_ID": user_id,
# #                     "HAS_ASSIGNMENT": (
# #                         current_user_assignment
# #                         is not None
# #                     ),
# #                     "CAN_DECIDE": bool(
# #                         current_user_assignment
# #                         and current_user_assignment[
# #                             "CAN_DECIDE"
# #                         ]
# #                     ),
# #                     "ASSIGNMENT": (
# #                         current_user_assignment
# #                     ),
# #                 },
# #             }

# #         finally:
# #             cursor.close()


# # def _insert_action_log(
# #     cursor: pyodbc.Cursor,
# #     approval_assignment_id: int,
# #     action_type: str,
# #     previous_status: str,
# #     new_status: str,
# #     action_by_user_id: int,
# #     remarks: str | None,
# # ) -> None:
# #     cursor.execute(
# #         f"""
# #         INSERT INTO {APPROVAL_ACTION_LOG_TABLE}
# #         (
# #             ApprovalAssignmentId,
# #             ActionType,
# #             PreviousStatus,
# #             NewStatus,
# #             ActionByUserId,
# #             Remarks,
# #             ActionAt
# #         )
# #         VALUES
# #         (
# #             ?,
# #             ?,
# #             ?,
# #             ?,
# #             ?,
# #             ?,
# #             SYSUTCDATETIME()
# #         )
# #         """,
# #         (
# #             approval_assignment_id,
# #             action_type,
# #             previous_status,
# #             new_status,
# #             action_by_user_id,
# #             remarks,
# #         ),
# #     )


# # def _update_batch_status(
# #     cursor: pyodbc.Cursor,
# #     approval_batch_id: int,
# #     batch_status: str,
# # ) -> None:
# #     cursor.execute(
# #         f"""
# #         UPDATE {APPROVAL_BATCH_TABLE}
# #         SET
# #             BatchStatus = ?,
# #             CompletedAt = CASE
# #                 WHEN ? = N'PENDING'
# #                 THEN NULL
# #                 ELSE SYSUTCDATETIME()
# #             END
# #         WHERE ApprovalBatchId = ?
# #         """,
# #         (
# #             batch_status,
# #             batch_status,
# #             approval_batch_id,
# #         ),
# #     )


# # def _update_risk_status(
# #     cursor: pyodbc.Cursor,
# #     risk_assessment_id: int,
# #     prospect_id: str,
# #     status_value: str,
# # ) -> None:
# #     cursor.execute(
# #         f"""
# #         UPDATE {RISK_HEADER_TABLE}
# #         SET
# #             AssessmentStatus = ?,
# #             ModifiedAt = SYSUTCDATETIME()
# #         WHERE RiskAssessmentId = ?
# #           AND ProspectId = ?
# #         """,
# #         (
# #             status_value,
# #             risk_assessment_id,
# #             prospect_id,
# #         ),
# #     )


# # def _update_registration_status(
# #     cursor: pyodbc.Cursor,
# #     prospect_id: str,
# #     status_value: str,
# #     is_draft: int,
# # ) -> None:
# #     cursor.execute(
# #         f"""
# #         UPDATE {REGISTRATION_TABLE}
# #         SET
# #             Status = ?,
# #             IsDraft = ?,
# #             ModifiedOn = SYSUTCDATETIME()
# #         WHERE ProspectId = ?
# #         """,
# #         (
# #             status_value,
# #             is_draft,
# #             prospect_id,
# #         ),
# #     )


# # def submit_approval_decision_sync(
# #     payload: ApprovalSubmitRequest,
# # ) -> dict[str, Any]:
# #     prospect_id = _normalize_prospect_id(
# #         payload.ProspectId
# #     )
# #     assignment_id = int(
# #         payload.ApprovalAssignmentId
# #     )
# #     user_id = int(payload.UserId)

# #     decision = (
# #         payload.Decision.value
# #         if hasattr(payload.Decision, "value")
# #         else str(payload.Decision)
# #     ).strip().upper()

# #     remarks = _normalize_text(
# #         payload.Remarks
# #     )

# #     if decision not in {
# #         "APPROVE",
# #         "REJECT",
# #     }:
# #         raise ApprovalDecisionError(
# #             "Decision must be APPROVE or REJECT"
# #         )

# #     if decision == "REJECT" and not remarks:
# #         raise ApprovalDecisionError(
# #             "Remarks is required when Decision is REJECT"
# #         )

# #     should_trigger_d365 = False

# #     with get_secondary_connection() as connection:
# #         cursor = connection.cursor()

# #         try:
# #             cursor.execute(
# #                 "SET XACT_ABORT ON;"
# #             )

# #             cursor.execute(
# #                 f"""
# #                 SELECT
# #                     assignment.ApprovalAssignmentId,
# #                     assignment.ApprovalBatchId,
# #                     assignment.ProspectId,
# #                     assignment.ApproverUserId,
# #                     assignment.DecisionStatus,
# #                     batch.RiskAssessmentId,
# #                     batch.BatchStatus
# #                 FROM {APPROVAL_ASSIGNMENT_TABLE}
# #                      AS assignment
# #                 INNER JOIN {APPROVAL_BATCH_TABLE}
# #                            AS batch
# #                     ON batch.ApprovalBatchId =
# #                        assignment.ApprovalBatchId
# #                    AND batch.ProspectId =
# #                        assignment.ProspectId
# #                 WHERE assignment.ApprovalAssignmentId = ?
# #                   AND assignment.ProspectId = ?
# #                 """,
# #                 (
# #                     assignment_id,
# #                     prospect_id,
# #                 ),
# #             )

# #             assignment = _fetchone_as_dict(
# #                 cursor
# #             )

# #             if assignment is None:
# #                 raise ApprovalAssignmentNotFoundError(
# #                     "Approval assignment was not found: "
# #                     f"{assignment_id}"
# #                 )

# #             if int(
# #                 assignment["ApproverUserId"]
# #             ) != user_id:
# #                 raise ApprovalAuthorizationError(
# #                     "This assignment does not belong to "
# #                     "the supplied UserId"
# #                 )

# #             previous_status = str(
# #                 assignment["DecisionStatus"]
# #                 or "PENDING"
# #             ).strip().upper()

# #             current_batch_status = str(
# #                 assignment["BatchStatus"]
# #                 or "PENDING"
# #             ).strip().upper()

# #             if current_batch_status != "PENDING":
# #                 raise ApprovalDecisionError(
# #                     "This batch is already completed. "
# #                     f"Current status={current_batch_status}"
# #                 )

# #             if previous_status != "PENDING":
# #                 raise ApprovalDecisionError(
# #                     "This assignment is already decided. "
# #                     f"Current status={previous_status}"
# #                 )

# #             new_status = (
# #                 "APPROVED"
# #                 if decision == "APPROVE"
# #                 else "REJECTED"
# #             )

# #             cursor.execute(
# #                 f"""
# #                 UPDATE {APPROVAL_ASSIGNMENT_TABLE}
# #                 SET
# #                     DecisionStatus = ?,
# #                     DecisionRemarks = ?,
# #                     DecisionAt = SYSUTCDATETIME()
# #                 WHERE ApprovalAssignmentId = ?
# #                   AND ApproverUserId = ?
# #                   AND DecisionStatus = N'PENDING'
# #                 """,
# #                 (
# #                     new_status,
# #                     remarks,
# #                     assignment_id,
# #                     user_id,
# #                 ),
# #             )

# #             if cursor.rowcount != 1:
# #                 raise ApprovalConflictError(
# #                     "The assignment changed in another request. "
# #                     "Refresh and retry."
# #                 )

# #             _insert_action_log(
# #                 cursor=cursor,
# #                 approval_assignment_id=(
# #                     assignment_id
# #                 ),
# #                 action_type=decision,
# #                 previous_status=previous_status,
# #                 new_status=new_status,
# #                 action_by_user_id=user_id,
# #                 remarks=remarks,
# #             )

# #             approval_batch_id = int(
# #                 assignment["ApprovalBatchId"]
# #             )
# #             risk_assessment_id = int(
# #                 assignment["RiskAssessmentId"]
# #             )

# #             counts = _calculate_batch_counts(
# #                 cursor,
# #                 approval_batch_id,
# #             )

# #             if counts["PENDING"] > 0:
# #                 final_batch_status = "PENDING"
# #                 final_vendor_status = None
# #                 final_risk_status = None
# #                 final_is_draft = None

# #             elif counts["REJECTED"] > 0:
# #                 final_batch_status = "RETURNED"
# #                 final_vendor_status = "RETURNED"
# #                 final_risk_status = "RETURNED"
# #                 final_is_draft = 0

# #             else:
# #                 final_batch_status = "APPROVED"
# #                 final_vendor_status = "APPROVED"
# #                 final_risk_status = "APPROVED"
# #                 final_is_draft = 1
# #                 should_trigger_d365 = True

# #             _update_batch_status(
# #                 cursor,
# #                 approval_batch_id,
# #                 final_batch_status,
# #             )

# #             if final_risk_status is not None:
# #                 _update_risk_status(
# #                     cursor,
# #                     risk_assessment_id,
# #                     prospect_id,
# #                     final_risk_status,
# #                 )

# #             if final_vendor_status is not None:
# #                 _update_registration_status(
# #                     cursor,
# #                     prospect_id,
# #                     final_vendor_status,
# #                     final_is_draft,
# #                 )

# #             connection.commit()

# #         except Exception:
# #             connection.rollback()
# #             raise

# #         finally:
# #             cursor.close()

# #     d365_result = None

# #     if should_trigger_d365:
# #         d365_result = create_vendor_in_d365(
# #             prospect_id
# #         )

# #         if d365_result.get("SUCCESS"):
# #             final_vendor_status = "VENDOR_CREATED"

# #     return {
# #         "SUCCESS": True,
# #         "MESSAGE": (
# #             "Approval decision saved successfully"
# #         ),
# #         "PROSPECT_ID": prospect_id,
# #         "APPROVAL_ASSIGNMENT_ID": assignment_id,
# #         "APPROVAL_BATCH_ID": approval_batch_id,
# #         "APPROVER_USER_ID": user_id,
# #         "YOUR_DECISION": new_status,
# #         "COUNTS": counts,
# #         "FINAL_BATCH_STATUS": final_batch_status,
# #         "FINAL_VENDOR_REGISTRATION_STATUS": (
# #             final_vendor_status
# #         ),
# #         "FINAL_RISK_ASSESSMENT_STATUS": (
# #             final_risk_status
# #         ),
# #         "ISDRAFT": final_is_draft,
# #         "D365_TRIGGER_RESULT": d365_result,
# #     }
# # def retry_d365_vendor_creation_sync(
# #     prospect_id: str,
# # ) -> dict[str, Any]:

# #     prospect_id = str(
# #         prospect_id or ""
# #     ).strip().upper()

# #     if not prospect_id:
# #         raise ApprovalConflictError(
# #             "ProspectId is required"
# #         )

# #     approval_batch_id: int | None = None
# #     batch_status: str | None = None

# #     # ========================================================
# #     # VALIDATE APPROVAL STATUS
# #     # ========================================================
# #     with get_secondary_connection() as connection:
# #         cursor = connection.cursor()

# #         try:
# #             cursor.execute(
# #                 f"""
# #                 SELECT TOP 1
# #                     ApprovalBatchId,
# #                     BatchStatus
# #                 FROM {APPROVAL_BATCH_TABLE}
# #                 WHERE ProspectId = ?
# #                 ORDER BY ApprovalBatchId DESC
# #                 """,
# #                 prospect_id,
# #             )

# #             batch_row = cursor.fetchone()

# #             if batch_row is None:
# #                 raise ApprovalConflictError(
# #                     "No approval batch was found for "
# #                     f"ProspectId={prospect_id}"
# #                 )

# #             approval_batch_id = int(
# #                 batch_row[0]
# #             )

# #             batch_status = str(
# #                 batch_row[1] or ""
# #             ).strip().upper()

# #             if batch_status != "APPROVED":
# #                 raise ApprovalConflictError(
# #                     "D365 vendor creation can only be retried "
# #                     "after final approval. "
# #                     f"Current batch status={batch_status}"
# #                 )

# #         finally:
# #             cursor.close()

# #     # ========================================================
# #     # RETRY D365 VENDOR CREATION
# #     # ========================================================
# #     try:
# #         logger.info(
# #             "Retrying D365 vendor creation for "
# #             "ProspectId={}, ApprovalBatchId={}",
# #             prospect_id,
# #             approval_batch_id,
# #         )

# #         d365_result = create_vendor_in_d365(
# #             prospect_id=prospect_id,
# #         )

# #     except Exception as exc:
# #         logger.exception(
# #             "D365 retry raised an exception for "
# #             "ProspectId={}",
# #             prospect_id,
# #         )

# #         return {
# #             "SUCCESS": False,
# #             "MESSAGE": "D365 vendor creation retry failed",
# #             "PROSPECT_ID": prospect_id,
# #             "APPROVAL_BATCH_ID": approval_batch_id,
# #             "APPROVAL_STATUS": batch_status,
# #             "D365_TRIGGERED": True,
# #             "D365_RESULT": None,
# #             "HTTP_STATUS": None,
# #             "ERROR": str(exc),
# #         }

# #     # ========================================================
# #     # VALIDATE D365 SERVICE RESPONSE
# #     # ========================================================
# #     if not isinstance(d365_result, dict):
# #         return {
# #             "SUCCESS": False,
# #             "MESSAGE": (
# #                 "D365 vendor creation returned an invalid response"
# #             ),
# #             "PROSPECT_ID": prospect_id,
# #             "APPROVAL_BATCH_ID": approval_batch_id,
# #             "APPROVAL_STATUS": batch_status,
# #             "D365_TRIGGERED": True,
# #             "D365_RESULT": d365_result,
# #             "HTTP_STATUS": None,
# #             "ERROR": (
# #                 "Expected D365 response to be a dictionary"
# #             ),
# #         }

# #     d365_success = bool(
# #         d365_result.get("SUCCESS", False)
# #     )

# #     d365_http_status = d365_result.get(
# #         "HTTP_STATUS"
# #     )

# #     d365_error = d365_result.get(
# #         "ERROR"
# #     )

# #     if not d365_success:
# #         logger.error(
# #             "D365 vendor creation retry failed for "
# #             "ProspectId={}. HTTP_STATUS={}, ERROR={}",
# #             prospect_id,
# #             d365_http_status,
# #             d365_error,
# #         )

# #         return {
# #             "SUCCESS": False,
# #             "MESSAGE": "D365 vendor creation retry failed",
# #             "PROSPECT_ID": prospect_id,
# #             "APPROVAL_BATCH_ID": approval_batch_id,
# #             "APPROVAL_STATUS": batch_status,
# #             "D365_TRIGGERED": bool(
# #                 d365_result.get("TRIGGERED", True)
# #             ),
# #             "D365_RESULT": d365_result,
# #             "HTTP_STATUS": d365_http_status,
# #             "ERROR": (
# #                 d365_error
# #                 or "D365 vendor creation was unsuccessful"
# #             ),
# #         }

# #     logger.info(
# #         "D365 vendor creation retry succeeded for "
# #         "ProspectId={}",
# #         prospect_id,
# #     )

# #     return {
# #         "SUCCESS": True,
# #         "MESSAGE": (
# #             "D365 vendor creation completed successfully"
# #         ),
# #         "PROSPECT_ID": prospect_id,
# #         "APPROVAL_BATCH_ID": approval_batch_id,
# #         "APPROVAL_STATUS": batch_status,
# #         "D365_TRIGGERED": bool(
# #             d365_result.get("TRIGGERED", True)
# #         ),
# #         "D365_RESULT": d365_result,
# #         "HTTP_STATUS": d365_http_status,
# #         "ERROR": None,
# #     }
# # #  from __future__ import annotations

# # # import os
# # # from datetime import date, datetime
# # # from typing import Any

# # # import pyodbc

# # # from app.core.config import settings
# # # from app.db.base import get_secondary_connection
# # # from app.schemas.vendorapproval_schema import (
# # #     ApprovalFetchRequest,
# # #     ApprovalSubmitRequest,
# # # )

# # # DB_SCHEMA = getattr(
# # #     settings,
# # #     "SECONDARY_DB_SCHEMA",
# # #     getattr(settings, "DB_SCHEMA", "dev"),
# # # )

# # # USERS_TABLE = f"[{DB_SCHEMA}].[HIQ_Users]"
# # # ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_Roles]"
# # # USER_ROLES_TABLE = f"[{DB_SCHEMA}].[HIQ_UserRoles]"
# # # APPROVAL_BATCH_TABLE = f"[{DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
# # # APPROVAL_ASSIGNMENT_TABLE = f"[{DB_SCHEMA}].[HIQ_VendorApprovalAssignment]"
# # # APPROVAL_ACTION_LOG_TABLE = f"[{DB_SCHEMA}].[HIQ_VendorApprovalActionLog]"
# # # RISK_HEADER_TABLE = f"[{DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
# # # REGISTRATION_TABLE = f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
# # # PROSPECT_TABLE = f"[{DB_SCHEMA}].[d365_VendorProspect]"

# # # APPROVER_ROLE_NAME = os.getenv(
# # #     "VENDOR_APPROVER_ROLE_NAME",
# # #     "Approval",
# # # ).strip()


# # # class ApprovalBatchNotFoundError(LookupError):
# # #     pass


# # # class ApprovalAssignmentNotFoundError(LookupError):
# # #     pass


# # # class ApprovalAuthorizationError(PermissionError):
# # #     pass


# # # class ApprovalDecisionError(ValueError):
# # #     pass


# # # class ApprovalConflictError(ValueError):
# # #     pass


# # # class ApprovalConfigurationError(ValueError):
# # #     pass


# # # # Compatibility with older router imports.
# # # ApprovalNotFoundError = ApprovalBatchNotFoundError


# # # def _serialize(value: Any) -> Any:
# # #     if isinstance(value, (date, datetime)):
# # #         return value.isoformat()
# # #     return value


# # # def _normalize_text(value: Any) -> str | None:
# # #     if value is None:
# # #         return None
# # #     text = str(value).strip()
# # #     return text or None


# # # def _normalize_prospect_id(value: Any) -> str:
# # #     prospect_id = str(value or "").strip().upper()
# # #     if not prospect_id:
# # #         raise ApprovalDecisionError("ProspectId is required")
# # #     return prospect_id


# # # def _fetchone_as_dict(cursor: pyodbc.Cursor) -> dict[str, Any] | None:
# # #     row = cursor.fetchone()
# # #     if row is None:
# # #         return None
# # #     columns = [column[0] for column in cursor.description]
# # #     return dict(zip(columns, row))


# # # def _fetchall_as_dicts(cursor: pyodbc.Cursor) -> list[dict[str, Any]]:
# # #     rows = cursor.fetchall()
# # #     if cursor.description is None:
# # #         return []
# # #     columns = [column[0] for column in cursor.description]
# # #     return [dict(zip(columns, row)) for row in rows]


# # # def _initials(full_name: Any, username: Any) -> str:
# # #     source = str(full_name or username or "User").strip()
# # #     parts = [part for part in source.split() if part]
# # #     if not parts:
# # #         return "U"
# # #     if len(parts) == 1:
# # #         return parts[0][:2].upper()
# # #     return (parts[0][0] + parts[-1][0]).upper()


# # # def _fetch_dynamic_approvers(cursor: pyodbc.Cursor) -> list[dict[str, Any]]:
# # #     if not APPROVER_ROLE_NAME:
# # #         raise ApprovalConfigurationError("VENDOR_APPROVER_ROLE_NAME is empty")

# # #     cursor.execute(
# # #         f"""
# # #         SELECT TOP 1 RoleId, RoleName
# # #         FROM {ROLES_TABLE}
# # #         WHERE UPPER(LTRIM(RTRIM(RoleName))) = UPPER(?)
# # #           AND IsActive = 1
# # #         ORDER BY RoleId
# # #         """,
# # #         APPROVER_ROLE_NAME,
# # #     )
# # #     role_row = cursor.fetchone()
# # #     if role_row is None:
# # #         raise ApprovalConfigurationError(
# # #             f"Active role '{APPROVER_ROLE_NAME}' was not found"
# # #         )

# # #     role_id = int(role_row[0])
# # #     role_name = str(role_row[1]).strip()

# # #     cursor.execute(
# # #         f"""
# # #         SELECT DISTINCT
# # #             u.UserId,
# # #             u.Username,
# # #             u.FullName,
# # #             u.Email,
# # #             r.RoleId,
# # #             r.RoleName
# # #         FROM {USER_ROLES_TABLE} ur
# # #         INNER JOIN {USERS_TABLE} u
# # #             ON u.UserId = ur.UserId
# # #         INNER JOIN {ROLES_TABLE} r
# # #             ON r.RoleId = ur.RoleId
# # #         WHERE ur.RoleId = ?
# # #           AND u.IsActive = 1
# # #           AND ISNULL(u.IsLocked, 0) = 0
# # #           AND r.IsActive = 1
# # #         ORDER BY u.UserId
# # #         """,
# # #         role_id,
# # #     )
# # #     approvers = _fetchall_as_dicts(cursor)
# # #     if not approvers:
# # #         raise ApprovalConfigurationError(
# # #             f"No active users are mapped to RoleName='{role_name}'"
# # #         )
# # #     return approvers


# # # def _ensure_no_pending_batch(cursor: pyodbc.Cursor, prospect_id: str) -> None:
# # #     cursor.execute(
# # #         f"""
# # #         SELECT TOP 1 ApprovalBatchId
# # #         FROM {APPROVAL_BATCH_TABLE}
# # #         WHERE ProspectId = ?
# # #           AND BatchStatus = N'PENDING'
# # #         ORDER BY ApprovalBatchId DESC
# # #         """,
# # #         prospect_id,
# # #     )
# # #     row = cursor.fetchone()
# # #     if row is not None:
# # #         raise ApprovalConflictError(
# # #             f"A pending batch already exists. ApprovalBatchId={row[0]}"
# # #         )


# # # def create_approval_workflow_in_transaction(
# # #     cursor: pyodbc.Cursor,
# # #     risk_assessment_id: int,
# # #     prospect_id: str,
# # #     initiated_by_user_id: int,
# # # ) -> dict[str, Any]:
# # #     prospect_id = _normalize_prospect_id(prospect_id)
# # #     if risk_assessment_id <= 0:
# # #         raise ApprovalConflictError("risk_assessment_id must be greater than zero")
# # #     if initiated_by_user_id <= 0:
# # #         raise ApprovalConflictError("initiated_by_user_id must be greater than zero")

# # #     _ensure_no_pending_batch(cursor, prospect_id)
# # #     approvers = _fetch_dynamic_approvers(cursor)

# # #     cursor.execute(
# # #         f"""
# # #         INSERT INTO {APPROVAL_BATCH_TABLE}
# # #         (
# # #             RiskAssessmentId,
# # #             ProspectId,
# # #             RequiredApproverCount,
# # #             BatchStatus,
# # #             AssignedByUserId,
# # #             CreatedAt,
# # #             CompletedAt
# # #         )
# # #         OUTPUT INSERTED.ApprovalBatchId
# # #         VALUES (?, ?, ?, N'PENDING', ?, SYSUTCDATETIME(), NULL)
# # #         """,
# # #         (
# # #             risk_assessment_id,
# # #             prospect_id,
# # #             len(approvers),
# # #             initiated_by_user_id,
# # #         ),
# # #     )
# # #     approval_batch_id = int(cursor.fetchone()[0])

# # #     assignments: list[dict[str, Any]] = []
# # #     for approver in approvers:
# # #         cursor.execute(
# # #             f"""
# # #             INSERT INTO {APPROVAL_ASSIGNMENT_TABLE}
# # #             (
# # #                 ApprovalBatchId,
# # #                 ProspectId,
# # #                 ApproverUserId,
# # #                 ApproverRoleCode,
# # #                 DecisionStatus,
# # #                 DecisionRemarks,
# # #                 AssignedAt,
# # #                 DecisionAt
# # #             )
# # #             OUTPUT INSERTED.ApprovalAssignmentId
# # #             VALUES (?, ?, ?, ?, N'PENDING', NULL, SYSUTCDATETIME(), NULL)
# # #             """,
# # #             (
# # #                 approval_batch_id,
# # #                 prospect_id,
# # #                 int(approver["UserId"]),
# # #                 str(approver["RoleName"]),
# # #             ),
# # #         )
# # #         assignment_id = int(cursor.fetchone()[0])
# # #         assignments.append(
# # #             {
# # #                 "APPROVAL_ASSIGNMENT_ID": assignment_id,
# # #                 "APPROVAL_BATCH_ID": approval_batch_id,
# # #                 "PROSPECT_ID": prospect_id,
# # #                 "APPROVER_USER_ID": int(approver["UserId"]),
# # #                 "APPROVER_ROLE_CODE": approver.get("RoleName"),
# # #                 "USERNAME": approver.get("Username"),
# # #                 "FULL_NAME": approver.get("FullName"),
# # #                 "EMAIL": approver.get("Email"),
# # #                 "DECISION_STATUS": "PENDING",
# # #             }
# # #         )

# # #     return {
# # #         "APPROVAL_BATCH_ID": approval_batch_id,
# # #         "BATCH_STATUS": "PENDING",
# # #         "get_secondary_connection": len(approvers),
# # #         "ASSIGNMENTS": assignments,
# # #     }


# # # def _get_latest_approval_batch(
# # #     cursor: pyodbc.Cursor,
# # #     prospect_id: str,
# # # ) -> dict[str, Any]:
# # #     cursor.execute(
# # #         f"""
# # #         SELECT TOP 1
# # #             ApprovalBatchId,
# # #             RiskAssessmentId,
# # #             ProspectId,
# # #             RequiredApproverCount,
# # #             BatchStatus,
# # #             AssignedByUserId,
# # #             CreatedAt,
# # #             CompletedAt
# # #         FROM {APPROVAL_BATCH_TABLE}
# # #         WHERE ProspectId = ?
# # #         ORDER BY ApprovalBatchId DESC
# # #         """,
# # #         prospect_id,
# # #     )
# # #     batch = _fetchone_as_dict(cursor)
# # #     if batch is None:
# # #         raise ApprovalBatchNotFoundError(
# # #             f"Approval batch was not found for ProspectId={prospect_id}"
# # #         )
# # #     return batch


# # # def _get_prospect_summary(
# # #     cursor: pyodbc.Cursor,
# # #     prospect_id: str,
# # # ) -> dict[str, Any]:
# # #     cursor.execute(
# # #         f"""
# # #         SELECT TOP 1
# # #             p.ProspectId,
# # #             p.VendorAccount,
# # #             p.Name,
# # #             p.Email,
# # #             r.CompanyName,
# # #             r.Status AS VendorRegistrationStatus
# # #         FROM {PROSPECT_TABLE} p
# # #         LEFT JOIN {REGISTRATION_TABLE} r
# # #             ON r.ProspectId = p.ProspectId
# # #         WHERE p.ProspectId = ?
# # #         """,
# # #         prospect_id,
# # #     )
# # #     row = _fetchone_as_dict(cursor)
# # #     if row is not None:
# # #         return {
# # #             "PROSPECT_ID": row.get("ProspectId"),
# # #             "VENDOR_ACCOUNT": row.get("VendorAccount"),
# # #             "NAME": row.get("Name"),
# # #             "EMAIL": row.get("Email"),
# # #             "COMPANY_NAME": row.get("CompanyName"),
# # #             "VENDOR_REGISTRATION_STATUS": row.get("VendorRegistrationStatus"),
# # #         }

# # #     cursor.execute(
# # #         f"""
# # #         SELECT TOP 1 ProspectId, CompanyName, Status
# # #         FROM {REGISTRATION_TABLE}
# # #         WHERE ProspectId = ?
# # #         """,
# # #         prospect_id,
# # #     )
# # #     row = _fetchone_as_dict(cursor)
# # #     return {
# # #         "PROSPECT_ID": prospect_id,
# # #         "VENDOR_ACCOUNT": None,
# # #         "NAME": row.get("CompanyName") if row else None,
# # #         "EMAIL": None,
# # #         "COMPANY_NAME": row.get("CompanyName") if row else None,
# # #         "VENDOR_REGISTRATION_STATUS": row.get("Status") if row else None,
# # #     }


# # # def _get_approval_assignments(
# # #     cursor: pyodbc.Cursor,
# # #     approval_batch_id: int,
# # # ) -> list[dict[str, Any]]:
# # #     cursor.execute(
# # #         f"""
# # #         SELECT
# # #             a.ApprovalAssignmentId,
# # #             a.ApprovalBatchId,
# # #             a.ProspectId,
# # #             a.ApproverUserId,
# # #             a.ApproverRoleCode,
# # #             a.DecisionStatus,
# # #             a.DecisionRemarks,
# # #             a.AssignedAt,
# # #             a.DecisionAt,
# # #             u.Username,
# # #             u.FullName,
# # #             u.Email
# # #         FROM {APPROVAL_ASSIGNMENT_TABLE} a
# # #         LEFT JOIN {USERS_TABLE} u
# # #             ON u.UserId = a.ApproverUserId
# # #         WHERE a.ApprovalBatchId = ?
# # #         ORDER BY a.ApprovalAssignmentId
# # #         """,
# # #         approval_batch_id,
# # #     )
# # #     return _fetchall_as_dicts(cursor)


# # # def _calculate_batch_counts(
# # #     cursor: pyodbc.Cursor,
# # #     approval_batch_id: int,
# # # ) -> dict[str, int]:
# # #     cursor.execute(
# # #         f"""
# # #         SELECT
# # #             COUNT(*) AS TotalCount,
# # #             SUM(CASE WHEN DecisionStatus = N'APPROVED' THEN 1 ELSE 0 END),
# # #             SUM(CASE WHEN DecisionStatus = N'REJECTED' THEN 1 ELSE 0 END),
# # #             SUM(CASE WHEN DecisionStatus = N'PENDING' THEN 1 ELSE 0 END)
# # #         FROM {APPROVAL_ASSIGNMENT_TABLE}
# # #         WHERE ApprovalBatchId = ?
# # #         """,
# # #         approval_batch_id,
# # #     )
# # #     row = cursor.fetchone()
# # #     return {
# # #         "TOTAL": int(row[0] or 0),
# # #         "APPROVED": int(row[1] or 0),
# # #         "REJECTED": int(row[2] or 0),
# # #         "PENDING": int(row[3] or 0),
# # #     }


# # # def fetch_approval_dashboard_sync(
# # #     payload: ApprovalFetchRequest,
# # # ) -> dict[str, Any]:
# # #     prospect_id = _normalize_prospect_id(payload.ProspectId)
# # #     user_id = int(payload.UserId)
# # #     if user_id <= 0:
# # #         raise ApprovalDecisionError("UserId must be greater than zero")

# # #     with get_secondary_connection() as connection:
# # #         cursor = connection.cursor()
# # #         try:
# # #             batch = _get_latest_approval_batch(cursor, prospect_id)
# # #             batch_id = int(batch["ApprovalBatchId"])
# # #             prospect = _get_prospect_summary(cursor, prospect_id)
# # #             rows = _get_approval_assignments(cursor, batch_id)
# # #             counts = _calculate_batch_counts(cursor, batch_id)

# # #             approvers: list[dict[str, Any]] = []
# # #             current_user_assignment = None

# # #             for row in rows:
# # #                 status_value = str(row.get("DecisionStatus") or "PENDING").upper()
# # #                 card = {
# # #                     "APPROVAL_ASSIGNMENT_ID": int(row["ApprovalAssignmentId"]),
# # #                     "APPROVAL_BATCH_ID": int(row["ApprovalBatchId"]),
# # #                     "PROSPECT_ID": row.get("ProspectId"),
# # #                     "APPROVER_USER_ID": int(row["ApproverUserId"]),
# # #                     "APPROVER_ROLE_CODE": row.get("ApproverRoleCode"),
# # #                     "USERNAME": row.get("Username"),
# # #                     "FULL_NAME": row.get("FullName"),
# # #                     "EMAIL": row.get("Email"),
# # #                     "INITIALS": _initials(row.get("FullName"), row.get("Username")),
# # #                     "DECISION_STATUS": status_value,
# # #                     "DECISION_REMARKS": row.get("DecisionRemarks"),
# # #                     "ASSIGNED_AT": _serialize(row.get("AssignedAt")),
# # #                     "DECISION_AT": _serialize(row.get("DecisionAt")),
# # #                     "IS_CURRENT_USER": int(row["ApproverUserId"]) == user_id,
# # #                 }
# # #                 approvers.append(card)

# # #                 if card["IS_CURRENT_USER"]:
# # #                     current_user_assignment = {
# # #                         **card,
# # #                         "CAN_DECIDE": (
# # #                             status_value == "PENDING"
# # #                             and str(batch["BatchStatus"]).upper() == "PENDING"
# # #                         ),
# # #                     }

# # #             return {
# # #                 "SUCCESS": True,
# # #                 "PROSPECT": prospect,
# # #                 "APPROVAL_BATCH": {
# # #                     "APPROVAL_BATCH_ID": batch_id,
# # #                     "RISK_ASSESSMENT_ID": int(batch["RiskAssessmentId"]),
# # #                     "PROSPECT_ID": batch.get("ProspectId"),
# # #                     "get_secondary_connection": int(batch["RequiredApproverCount"]),
# # #                     "BATCH_STATUS": batch.get("BatchStatus"),
# # #                     "ASSIGNED_BY_USER_ID": int(batch["AssignedByUserId"]),
# # #                     "CREATED_AT": _serialize(batch.get("CreatedAt")),
# # #                     "COMPLETED_AT": _serialize(batch.get("CompletedAt")),
# # #                 },
# # #                 "SUMMARY": {
# # #                     "TOTAL_COUNT": counts["TOTAL"],
# # #                     "COMPLETED_COUNT": counts["APPROVED"] + counts["REJECTED"],
# # #                     "APPROVED_COUNT": counts["APPROVED"],
# # #                     "REJECTED_COUNT": counts["REJECTED"],
# # #                     "PENDING_COUNT": counts["PENDING"],
# # #                 },
# # #                 "APPROVERS": approvers,
# # #                 "CURRENT_USER_DECISION": {
# # #                     "USER_ID": user_id,
# # #                     "HAS_ASSIGNMENT": current_user_assignment is not None,
# # #                     "CAN_DECIDE": bool(
# # #                         current_user_assignment
# # #                         and current_user_assignment["CAN_DECIDE"]
# # #                     ),
# # #                     "ASSIGNMENT": current_user_assignment,
# # #                 },
# # #             }
# # #         finally:
# # #             cursor.close()


# # # def _insert_action_log(
# # #     cursor: pyodbc.Cursor,
# # #     approval_assignment_id: int,
# # #     action_type: str,
# # #     previous_status: str,
# # #     new_status: str,
# # #     action_by_user_id: int,
# # #     remarks: str | None,
# # # ) -> None:
# # #     cursor.execute(
# # #         f"""
# # #         INSERT INTO {APPROVAL_ACTION_LOG_TABLE}
# # #         (
# # #             ApprovalAssignmentId,
# # #             ActionType,
# # #             PreviousStatus,
# # #             NewStatus,
# # #             ActionByUserId,
# # #             Remarks,
# # #             ActionAt
# # #         )
# # #         VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME())
# # #         """,
# # #         (
# # #             approval_assignment_id,
# # #             action_type,
# # #             previous_status,
# # #             new_status,
# # #             action_by_user_id,
# # #             remarks,
# # #         ),
# # #     )


# # # def _update_batch_status(
# # #     cursor: pyodbc.Cursor,
# # #     approval_batch_id: int,
# # #     batch_status: str,
# # # ) -> None:
# # #     cursor.execute(
# # #         f"""
# # #         UPDATE {APPROVAL_BATCH_TABLE}
# # #         SET
# # #             BatchStatus = ?,
# # #             CompletedAt = CASE
# # #                 WHEN ? = N'PENDING' THEN NULL
# # #                 ELSE SYSUTCDATETIME()
# # #             END
# # #         WHERE ApprovalBatchId = ?
# # #         """,
# # #         (batch_status, batch_status, approval_batch_id),
# # #     )


# # # def _update_risk_status(
# # #     cursor: pyodbc.Cursor,
# # #     risk_assessment_id: int,
# # #     prospect_id: str,
# # #     status_value: str,
# # # ) -> None:
# # #     cursor.execute(
# # #         f"""
# # #         UPDATE {RISK_HEADER_TABLE}
# # #         SET
# # #             AssessmentStatus = ?,
# # #             ModifiedAt = SYSUTCDATETIME()
# # #         WHERE RiskAssessmentId = ?
# # #           AND ProspectId = ?
# # #         """,
# # #         (status_value, risk_assessment_id, prospect_id),
# # #     )


# # # def _update_registration_status(
# # #     cursor: pyodbc.Cursor,
# # #     prospect_id: str,
# # #     status_value: str,
# # #     is_draft: int,
# # # ) -> None:
# # #     cursor.execute(
# # #         f"""
# # #         UPDATE {REGISTRATION_TABLE}
# # #         SET
# # #             Status = ?,
# # #             IsDraft = ?,
# # #             ModifiedOn = SYSUTCDATETIME()
# # #         WHERE ProspectId = ?
# # #         """,
# # #         (status_value, is_draft, prospect_id),
# # #     )


# # # def _trigger_d365_vendor_creation(prospect_id: str) -> dict[str, Any]:
# # #     # Replace this placeholder with your real D365 vendor-creation call.
# # #     return {
# # #         "TRIGGERED": True,
# # #         "SUCCESS": False,
# # #         "PROSPECT_ID": prospect_id,
# # #         "MESSAGE": "Approval completed; real D365 call is not configured yet.",
# # #     }


# # # def submit_approval_decision_sync(
# # #     payload: ApprovalSubmitRequest,
# # # ) -> dict[str, Any]:
# # #     prospect_id = _normalize_prospect_id(payload.ProspectId)
# # #     assignment_id = int(payload.ApprovalAssignmentId)
# # #     user_id = int(payload.UserId)
# # #     decision = (
# # #         payload.Decision.value
# # #         if hasattr(payload.Decision, "value")
# # #         else str(payload.Decision)
# # #     ).strip().upper()
# # #     remarks = _normalize_text(payload.Remarks)

# # #     if decision not in {"APPROVE", "REJECT"}:
# # #         raise ApprovalDecisionError("Decision must be APPROVE or REJECT")
# # #     if decision == "REJECT" and not remarks:
# # #         raise ApprovalDecisionError("Remarks is required when Decision is REJECT")

# # #     should_trigger_d365 = False

# # #     with get_secondary_connection() as connection:
# # #         cursor = connection.cursor()
# # #         try:
# # #             cursor.execute("SET XACT_ABORT ON;")
# # #             cursor.execute(
# # #                 f"""
# # #                 SELECT
# # #                     a.ApprovalAssignmentId,
# # #                     a.ApprovalBatchId,
# # #                     a.ProspectId,
# # #                     a.ApproverUserId,
# # #                     a.DecisionStatus,
# # #                     b.RiskAssessmentId,
# # #                     b.BatchStatus
# # #                 FROM {APPROVAL_ASSIGNMENT_TABLE} a
# # #                 INNER JOIN {APPROVAL_BATCH_TABLE} b
# # #                     ON b.ApprovalBatchId = a.ApprovalBatchId
# # #                    AND b.ProspectId = a.ProspectId
# # #                 WHERE a.ApprovalAssignmentId = ?
# # #                   AND a.ProspectId = ?
# # #                 """,
# # #                 (assignment_id, prospect_id),
# # #             )
# # #             assignment = _fetchone_as_dict(cursor)

# # #             if assignment is None:
# # #                 raise ApprovalAssignmentNotFoundError(
# # #                     f"Approval assignment was not found: {assignment_id}"
# # #                 )
# # #             if int(assignment["ApproverUserId"]) != user_id:
# # #                 raise ApprovalAuthorizationError(
# # #                     "This assignment does not belong to the supplied UserId"
# # #                 )

# # #             previous_status = str(assignment["DecisionStatus"] or "PENDING").upper()
# # #             batch_status = str(assignment["BatchStatus"] or "PENDING").upper()

# # #             if batch_status != "PENDING":
# # #                 raise ApprovalDecisionError(
# # #                     f"This batch is already completed. Current status={batch_status}"
# # #                 )
# # #             if previous_status != "PENDING":
# # #                 raise ApprovalDecisionError(
# # #                     f"This assignment is already decided. Current status={previous_status}"
# # #                 )

# # #             new_status = "APPROVED" if decision == "APPROVE" else "REJECTED"

# # #             cursor.execute(
# # #                 f"""
# # #                 UPDATE {APPROVAL_ASSIGNMENT_TABLE}
# # #                 SET
# # #                     DecisionStatus = ?,
# # #                     DecisionRemarks = ?,
# # #                     DecisionAt = SYSUTCDATETIME()
# # #                 WHERE ApprovalAssignmentId = ?
# # #                   AND ApproverUserId = ?
# # #                   AND DecisionStatus = N'PENDING'
# # #                 """,
# # #                 (new_status, remarks, assignment_id, user_id),
# # #             )
# # #             if cursor.rowcount != 1:
# # #                 raise ApprovalConflictError(
# # #                     "The assignment changed in another request. Refresh and retry."
# # #                 )

# # #             _insert_action_log(
# # #                 cursor,
# # #                 assignment_id,
# # #                 decision,
# # #                 previous_status,
# # #                 new_status,
# # #                 user_id,
# # #                 remarks,
# # #             )

# # #             approval_batch_id = int(assignment["ApprovalBatchId"])
# # #             risk_assessment_id = int(assignment["RiskAssessmentId"])
# # #             counts = _calculate_batch_counts(cursor, approval_batch_id)

# # #             if counts["PENDING"] > 0:
# # #                 final_batch_status = "PENDING"
# # #                 final_vendor_status = None
# # #                 final_risk_status = None
# # #                 final_is_draft = None
# # #             elif counts["REJECTED"] > 0:
# # #                 final_batch_status = "RETURNED"
# # #                 final_vendor_status = "RETURNED"
# # #                 final_risk_status = "RETURNED"
# # #                 final_is_draft = 0
# # #             else:
# # #                 final_batch_status = "APPROVED"
# # #                 final_vendor_status = "APPROVED"
# # #                 final_risk_status = "APPROVED"
# # #                 final_is_draft = 1
# # #                 should_trigger_d365 = True

# # #             _update_batch_status(cursor, approval_batch_id, final_batch_status)

# # #             if final_risk_status is not None:
# # #                 _update_risk_status(
# # #                     cursor,
# # #                     risk_assessment_id,
# # #                     prospect_id,
# # #                     final_risk_status,
# # #                 )
# # #             if final_vendor_status is not None:
# # #                 _update_registration_status(
# # #                     cursor,
# # #                     prospect_id,
# # #                     final_vendor_status,
# # #                     final_is_draft,
# # #                 )

# # #             connection.commit()
# # #         except Exception:
# # #             connection.rollback()
# # #             raise
# # #         finally:
# # #             cursor.close()

# # #     d365_result = (
# # #         _trigger_d365_vendor_creation(prospect_id)
# # #         if should_trigger_d365
# # #         else None
# # #     )

# # #     return {
# # #         "SUCCESS": True,
# # #         "MESSAGE": "Approval decision saved successfully",
# # #         "PROSPECT_ID": prospect_id,
# # #         "APPROVAL_ASSIGNMENT_ID": assignment_id,
# # #         "APPROVAL_BATCH_ID": approval_batch_id,
# # #         "APPROVER_USER_ID": user_id,
# # #         "YOUR_DECISION": new_status,
# # #         "COUNTS": counts,
# # #         "FINAL_BATCH_STATUS": final_batch_status,
# # #         "FINAL_VENDOR_REGISTRATION_STATUS": final_vendor_status,
# # #         "FINAL_RISK_ASSESSMENT_STATUS": final_risk_status,
# # #         "ISDRAFT": final_is_draft,
# # #         "D365_TRIGGER_RESULT": d365_result,