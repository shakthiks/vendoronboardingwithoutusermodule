from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pyodbc
from loguru import logger

from app.core.config import settings
from app.db.base import get_connection, get_secondary_connection
from app.schemas.riskassessment_schema import (
    RiskAssessmentListPageRequest,
    RiskAssessmentRequest,
)
from app.services.riskassessment_email_service import (
    send_returned_risk_assessment_email,
)
from app.services.vendorapproval_service import (
    ApprovalConflictError,
    create_approval_workflow_in_transaction,
)


CLOUD_DB_SCHEMA = settings.DB_SCHEMA

LOCAL_DB_SCHEMA = getattr(
    settings,
    "SECONDARY_DB_SCHEMA",
    getattr(settings, "DB_SCHEMA", "dev"),
)

RISK_HEADER_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
)
RISK_DETAIL_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorRiskAssessmentDetail]"
)
REGISTRATION_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[HIQ_VendorRegistration]"
)
PROSPECT_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[d365_VendorProspect]"
)
APPROVAL_BATCH_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
)


class RiskAssessmentNotFoundError(LookupError):
    pass


class RiskAssessmentConflictError(ValueError):
    pass


def _serialize(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows_to_dicts(
    cursor: pyodbc.Cursor,
    rows,
) -> list[dict[str, Any]]:
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


def _format_risk_level(
    value: Any,
) -> str | None:
    if value is None:
        return None

    risk_level = str(value).strip()

    if not risk_level:
        return None

    return risk_level.title()


def _normalize_status(
    value: Any,
    line_number: int,
    *,
    required: bool,
) -> bool | None:
    if value is None or str(value).strip() == "":
        if required:
            raise RiskAssessmentConflictError(
                f"status is required for line {line_number}"
            )
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False

    normalized = str(value).strip().lower()

    if normalized in {
        "yes",
        "true",
        "1",
        "y",
    }:
        return True

    if normalized in {
        "no",
        "false",
        "0",
        "n",
    }:
        return False

    raise RiskAssessmentConflictError(
        f"status must be Yes or No for line {line_number}"
    )


def _normalize_risk_level(
    applicable: bool | None,
    value: Any,
    line_number: int,
) -> str | None:
    risk_level = str(
        value or ""
    ).strip().upper()

    if applicable is False:
        return None

    if not risk_level:
        return None

    if risk_level not in {
        "LOW",
        "MEDIUM",
        "HIGH",
    }:
        raise RiskAssessmentConflictError(
            "riskLevel must be Low, Medium or High "
            f"for line {line_number}"
        )

    return risk_level


def _get_vendor_email_context(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> tuple[str | None, str | None]:
    cursor.execute(
        f"""
        SELECT TOP 1
            Name,
            Email
        FROM {PROSPECT_TABLE}
        WHERE ProspectId = ?
        """,
        prospect_id,
    )

    row = cursor.fetchone()

    if row is None:
        return None, None

    company_name = (
        str(row[0]).strip()
        if row[0] is not None
        else None
    )

    vendor_email = (
        str(row[1]).strip()
        if row[1] is not None
        else None
    )

    return company_name, vendor_email


def _get_latest_assessment(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> tuple[int, str] | None:
    cursor.execute(
        f"""
        SELECT TOP 1
            RiskAssessmentId,
            AssessmentStatus
        FROM {RISK_HEADER_TABLE}
        WITH
        (
            UPDLOCK,
            HOLDLOCK
        )
        WHERE ProspectId = ?
        ORDER BY RiskAssessmentId DESC
        """,
        prospect_id,
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return (
        int(row[0]),
        str(row[1] or "").strip().upper(),
    )


def _get_existing_approval_batch(
    cursor: pyodbc.Cursor,
    risk_assessment_id: int,
) -> tuple[int, str] | None:
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

    if row is None:
        return None

    return (
        int(row[0]),
        str(row[1] or "").strip().upper(),
    )
def _insert_risk_header(
    cursor: pyodbc.Cursor,
    *,
    prospect_id: str,
    assessed_by_user_id: Any,
    submitted_by_user_id: Any,
    assessment_status: str,
    overall_risk_level: str | None,
    comments: str | None,
    vendor_group: str | None,
) -> int:
    cursor.execute(
        f"""
        INSERT INTO {RISK_HEADER_TABLE}
        (
            ProspectId,
            AssessedByUserId,
            SubmittedByUserId,
            AssessmentStatus,
            OverallRiskLevel,
            Comments,
            VendorGroup,
            CreatedAt,
            ModifiedAt,
            SubmittedAt
        )
        OUTPUT INSERTED.RiskAssessmentId
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            SYSUTCDATETIME(),
            SYSUTCDATETIME(),
            CASE
                WHEN ? = N'DRAFT'
                THEN NULL
                ELSE SYSUTCDATETIME()
            END
        )
        """,
        (
            prospect_id,
            assessed_by_user_id,
            submitted_by_user_id,
            assessment_status,
            overall_risk_level,
            comments,
            vendor_group,
            assessment_status,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        raise RiskAssessmentConflictError(
            "Risk-assessment header could not be created"
        )

    return int(row[0])

def _update_risk_header(
    cursor: pyodbc.Cursor,
    *,
    risk_assessment_id: int,
    prospect_id: str,
    assessed_by_user_id: Any,
    submitted_by_user_id: Any,
    assessment_status: str,
    overall_risk_level: str | None,
    comments: str | None,
    vendor_group: str | None,
) -> None:
    cursor.execute(
        f"""
        UPDATE {RISK_HEADER_TABLE}
        SET
            AssessedByUserId = ?,
            SubmittedByUserId = ?,
            AssessmentStatus = ?,
            OverallRiskLevel = ?,
            Comments = ?,
            VendorGroup = ?,
            ModifiedAt = SYSUTCDATETIME(),
            SubmittedAt =
                CASE
                    WHEN ? = N'DRAFT'
                    THEN NULL
                    ELSE SYSUTCDATETIME()
                END
        WHERE RiskAssessmentId = ?
          AND ProspectId = ?
        """,
        (
            assessed_by_user_id,
            submitted_by_user_id,
            assessment_status,
            overall_risk_level,
            comments,
            vendor_group,
            assessment_status,
            risk_assessment_id,
            prospect_id,
        ),
    )

    if cursor.rowcount != 1:
        raise RiskAssessmentConflictError(
            "Risk-assessment header could not be updated"
        )
# def _update_risk_header(
#     cursor: pyodbc.Cursor,
#     *,
#     risk_assessment_id: int,
#     prospect_id: str,
#     assessed_by_user_id: Any,
#     submitted_by_user_id: Any,
#     assessment_status: str,
#     overall_risk_level: str | None,
#     comments: str | None,
#     vendor_group: str | None,
# ) -> None:
#     cursor.execute(
#         f"""
#         UPDATE {RISK_HEADER_TABLE}
#         SET
#             AssessedByUserId = ?,
#             SubmittedByUserId = ?,
#             AssessmentStatus = ?,
#             OverallRiskLevel = COALESCE(
#                 ?,
#                 OverallRiskLevel
#             ),
#             Comments = ?,
#             VendorGroup = COALESCE(
#                 ?,
#                 VendorGroup
#             ),
#             ModifiedAt = SYSUTCDATETIME(),
#             SubmittedAt =
#                 CASE
#                     WHEN ? = N'DRAFT'
#                     THEN NULL
#                     ELSE SYSUTCDATETIME()
#                 END
#         WHERE RiskAssessmentId = ?
#           AND ProspectId = ?
#         """,
#         (
#             assessed_by_user_id,      # 1
#             submitted_by_user_id,     # 2
#             assessment_status,        # 3
#             overall_risk_level,       # 4
#             comments,                 # 5
#             vendor_group,             # 6
#             assessment_status,        # 7
#             risk_assessment_id,       # 8
#             prospect_id,              # 9
#         ),
#     )

#     if cursor.rowcount != 1:
#         raise RiskAssessmentConflictError(
#             "Risk-assessment header could not be updated"
#         )


def _replace_risk_lines(
    cursor: pyodbc.Cursor,
    *,
    risk_assessment_id: int,
    prospect_id: str,
    assessment_status: str,
    lines: list[Any],
) -> None:
    cursor.execute(
        f"""
        DELETE FROM {RISK_DETAIL_TABLE}
        WHERE RiskAssessmentId = ?
          AND ProspectId = ?
        """,
        (
            risk_assessment_id,
            prospect_id,
        ),
    )

    for line_number, line in enumerate(
        lines,
        start=1,
    ):
        process_name = str(
            line.process or ""
        ).strip()

        identified_risk = str(
            line.identifiedRisk or ""
        ).strip()

        if not process_name:
            raise RiskAssessmentConflictError(
                f"process is required for line {line_number}"
            )

        if not identified_risk:
            raise RiskAssessmentConflictError(
                "identifiedRisk is required for "
                f"line {line_number}"
            )

        # applicable = _normalize_status(
        #     line.status,
        #     line_number,
        #     required=assessment_status != "DRAFT",
        # )
        applicable = _normalize_status(
            line.status,
            line_number,
            # Only final submission requires Yes/No.
            required=assessment_status == "SUBMITTED",
        )

        risk_level = _normalize_risk_level(
            applicable,
            line.riskLevel,
            line_number,
        )

        remarks = (
            str(line.remarks).strip()
            if line.remarks is not None
            else None
        )

        db_is_applicable = (
            None
            if applicable is None
            else 1 if applicable else 0
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
                SYSUTCDATETIME(),
                SYSUTCDATETIME()
            )
            """,
            (
                risk_assessment_id,
                prospect_id,
                process_name,
                identified_risk,
                db_is_applicable,
                risk_level,
                remarks,
            ),
        )


def create_risk_assessment_sync(
    payload: RiskAssessmentRequest,
) -> dict[str, Any]:
    if not payload.Header:
        raise RiskAssessmentConflictError(
            "Header is required"
        )

    if len(payload.Header) != 1:
        raise RiskAssessmentConflictError(
            "Header must contain exactly one record"
        )

    if not payload.lines:
        raise RiskAssessmentConflictError(
            "At least one risk-assessment line is required"
        )

    header = payload.Header[0]

    prospect_id = str(
        header.ProspectId or ""
    ).strip().upper()

    if not prospect_id:
        raise RiskAssessmentConflictError(
            "ProspectId is required"
        )

    assessment_status = str(
        header.AssessmentStatus or "DRAFT"
    ).strip().upper()

    allowed_statuses = {
        "DRAFT",
        "SUBMITTED",
        "RETURNED",
        "REJECTED",
        "RESUBMITTED",
    }

    if assessment_status not in allowed_statuses:
        raise RiskAssessmentConflictError(
            "AssessmentStatus must be DRAFT, SUBMITTED, "
            "RETURNED, REJECTED or RESUBMITTED"
        )


    comments = (
    str(header.Comments).strip()
    if header.Comments is not None
    else None
    )
    comments = comments or None


    vendor_group = (
        str(header.VendorGroup).strip()
        if header.VendorGroup is not None
        else None
    )
    vendor_group = vendor_group or None


    overall_risk_level = (
        str(header.OverallRiskLevel).strip()
        if header.OverallRiskLevel is not None
        else None
    )
    overall_risk_level = overall_risk_level or None
        
    if (
    assessment_status == "SUBMITTED"
    and not vendor_group
    ):
        raise RiskAssessmentConflictError(
            "VendorGroup is required when AssessmentStatus "
            "is SUBMITTED"
        )
    # if assessment_status != "DRAFT" and not vendor_group:
    #     raise RiskAssessmentConflictError(
    #         "VendorGroup is required when submitting "
    #         "the risk assessment"
    #     )
    if (
    assessment_status == "RETURNED"
    and not comments
    ):
            raise RiskAssessmentConflictError(
                "Comments are required when AssessmentStatus "
                "is RETURNED"
            )
    # if (
    #     assessment_status in {
    #         "RETURNED",
    #         "REJECTED",
    #     }
    #     and not comments
    # ):
    #     raise RiskAssessmentConflictError(
    #         "Comments are required when AssessmentStatus "
    #         f"is {assessment_status}"
    #     )

    if (
        assessment_status == "SUBMITTED"
        and (
            header.InitiatedByUserId is None
            or header.InitiatedByUserId <= 0
        )
    ):
        raise RiskAssessmentConflictError(
            "InitiatedByUserId is required when "
            "AssessmentStatus is SUBMITTED"
        )

    resubmitted_email_context: dict[str, Any] | None = None

    with get_secondary_connection() as connection:
        cursor = connection.cursor()

        with get_connection() as cloud_connection:
            cloud_cursor = cloud_connection.cursor()

            try:
                cursor.execute(
                    "SET XACT_ABORT ON;"
                )

                cloud_cursor.execute(
                    f"""
                    SELECT
                        Status,
                        IsDraft
                    FROM {REGISTRATION_TABLE}
                    WITH
                    (
                        UPDLOCK,
                        HOLDLOCK
                    )
                    WHERE ProspectId = ?
                    """,
                    prospect_id,
                )

                registration_row = cloud_cursor.fetchone()

                if registration_row is None:
                    raise RiskAssessmentNotFoundError(
                        "Vendor registration was not found for "
                        f"ProspectId={prospect_id}"
                    )

                current_vendor_status = str(
                    registration_row[0] or ""
                ).strip().upper()

                current_is_draft = int(
                    registration_row[1] or 0
                )

                if assessment_status in {
                    "DRAFT",
                    "SUBMITTED",
                    "RETURNED",
                    "REJECTED",
                }:
                    if current_vendor_status not in {
                        "TO_EVALUATE",
                        "RESUBMITTED",
                    }:
                        raise RiskAssessmentConflictError(
                            "Risk action requires "
                            "VendorRegistration.Status "
                            "TO_EVALUATE or RESUBMITTED. "
                            f"Current status={current_vendor_status}"
                        )

                if assessment_status == "RESUBMITTED":
                    if current_vendor_status not in {
                        "TO_EVALUATE",
                        "RETURNED",
                        "REJECTED",
                    }:
                        raise RiskAssessmentConflictError(
                            "RESUBMITTED requires "
                            "VendorRegistration.Status "
                            "TO_EVALUATE, RETURNED or REJECTED. "
                            f"Current status={current_vendor_status}"
                        )

                latest_assessment = _get_latest_assessment(
                    cursor,
                    prospect_id,
                )

                previous_assessment_status = None
                previous_risk_assessment_id = None
                existing_approval_batch_id = None
                existing_approval_batch_status = None
                assessment_record_action = "CREATED"
                new_approval_cycle = False

                if latest_assessment is not None:
                    (
                        previous_risk_assessment_id,
                        previous_assessment_status,
                    ) = latest_assessment

                    existing_batch = _get_existing_approval_batch(
                        cursor,
                        previous_risk_assessment_id,
                    )

                    if existing_batch is not None:
                        (
                            existing_approval_batch_id,
                            existing_approval_batch_status,
                        ) = existing_batch

                # A RiskAssessmentId can have only one approval batch.
                # When a returned/rejected assessment is being resubmitted,
                # create a new assessment row for the next approval cycle.
                new_approval_cycle = bool(
                    latest_assessment is not None
                    and existing_approval_batch_id is not None
                    and assessment_status in {
                        "RESUBMITTED",
                        "SUBMITTED",
                    }
                )

                editable_statuses = {
                    "DRAFT",
                    "RETURNED",
                    "REJECTED",
                    "RESUBMITTED",
                }

                can_update_existing = bool(
                    latest_assessment is not None
                    and previous_assessment_status
                    in editable_statuses
                    and not new_approval_cycle
                )

                if can_update_existing:
                    risk_assessment_id = int(
                        previous_risk_assessment_id
                    )

                    _update_risk_header(
                        cursor,
                        risk_assessment_id=(
                            risk_assessment_id
                        ),
                        prospect_id=prospect_id,
                        assessed_by_user_id=(
                            header.AssessedByUserId
                        ),
                        submitted_by_user_id=(
                            header.SubmittedByUserId
                        ),
                        assessment_status=(
                            assessment_status
                        ),
                        overall_risk_level=overall_risk_level,
                        comments=comments,
                        vendor_group=vendor_group,
                    )

                    assessment_record_action = "UPDATED"

                else:
                    risk_assessment_id = _insert_risk_header(
                        cursor,
                        prospect_id=prospect_id,
                        assessed_by_user_id=(
                            header.AssessedByUserId
                        ),
                        submitted_by_user_id=(
                            header.SubmittedByUserId
                        ),
                        assessment_status=(
                            assessment_status
                        ),
                        comments=comments,
                        overall_risk_level=overall_risk_level,
                        vendor_group=vendor_group,
                    )

                    assessment_record_action = (
                        "NEW_APPROVAL_CYCLE_CREATED"
                        if new_approval_cycle
                        else "CREATED"
                    )

                _replace_risk_lines(
                    cursor,
                    risk_assessment_id=(
                        risk_assessment_id
                    ),
                    prospect_id=prospect_id,
                    assessment_status=(
                        assessment_status
                    ),
                    lines=payload.lines,
                )

                approval_result = {
                    "APPROVAL_BATCH_ID": None,
                    "BATCH_STATUS": None,
                    "REQUIRED_APPROVER_COUNT": 0,
                    "ASSIGNMENTS": [],
                }

                vendor_status_updated = False
                next_vendor_status = current_vendor_status
                next_is_draft = current_is_draft

                if assessment_status == "SUBMITTED":
                    try:
                        approval_result = (
                            create_approval_workflow_in_transaction(
                                cursor=cursor,
                                risk_assessment_id=(
                                    risk_assessment_id
                                ),
                                prospect_id=prospect_id,
                                initiated_by_user_id=(
                                    header.InitiatedByUserId
                                ),
                            )
                        )
                    except ApprovalConflictError as exc:
                        raise RiskAssessmentConflictError(
                            str(exc)
                        ) from exc

                    next_vendor_status = "IN_APPROVAL"
                    next_is_draft = 1
                    vendor_status_updated = True

                elif assessment_status == "RETURNED":
                    next_vendor_status = "RETURNED"
                    next_is_draft = 0
                    vendor_status_updated = True

                elif assessment_status == "REJECTED":
                    next_vendor_status = "REJECTED"
                    next_is_draft = 0
                    vendor_status_updated = True

                elif assessment_status == "RESUBMITTED":
                    next_vendor_status = "RESUBMITTED"
                    next_is_draft = 0
                    vendor_status_updated = True

                    company_name, vendor_email = (
                        _get_vendor_email_context(
                            cloud_cursor,
                            prospect_id,
                        )
                    )

                    resubmitted_email_context = {
                        "TO_EMAIL": vendor_email,
                        "COMPANY_NAME": company_name,
                        "COMMENTS": comments,
                        "PROSPECT_ID": prospect_id,
                    }

                if vendor_status_updated:
                    cloud_cursor.execute(
                        f"""
                        UPDATE {REGISTRATION_TABLE}
                        SET
                            Status = ?,
                            IsDraft = ?,
                            ModifiedOn = SYSUTCDATETIME()
                        WHERE ProspectId = ?
                        """,
                        (
                            next_vendor_status,
                            next_is_draft,
                            prospect_id,
                        ),
                    )

                    if cloud_cursor.rowcount != 1:
                        raise RiskAssessmentConflictError(
                            "Vendor registration status "
                            "could not be updated"
                        )

                approval_batch_id_value = approval_result.get(
                    "APPROVAL_BATCH_ID"
                )

                approval_batch_status_value = approval_result.get(
                    "BATCH_STATUS"
                )

                approval_assignments_value = (
                    approval_result.get("ASSIGNMENTS")
                    or []
                )

                required_approver_count_value = approval_result.get(
                    "REQUIRED_APPROVER_COUNT"
                )

                # Backward compatibility for old vendorapproval_service.py
                # where the key was accidentally named "get_secondary_connection".
                if required_approver_count_value is None:
                    required_approver_count_value = approval_result.get(
                        "get_secondary_connection"
                    )

                if required_approver_count_value is None:
                    required_approver_count_value = len(
                        approval_assignments_value
                    )

                required_approver_count_value = int(
                    required_approver_count_value or 0
                )

                connection.commit()
                cloud_connection.commit()

                response_payload = {
                    "SUCCESS": True,
                    "MESSAGE": (
                        "Risk assessment processed successfully"
                    ),
                    "RISK_ASSESSMENT_ID": risk_assessment_id,
                    "PREVIOUS_RISK_ASSESSMENT_ID": (
                        previous_risk_assessment_id
                    ),
                    "ASSESSMENT_RECORD_ACTION": (
                        assessment_record_action
                    ),
                    "NEW_APPROVAL_CYCLE": (
                        new_approval_cycle
                    ),
                    "PREVIOUS_ASSESSMENT_STATUS": (
                        previous_assessment_status
                    ),
                    "PREVIOUS_APPROVAL_BATCH_ID": (
                        existing_approval_batch_id
                    ),
                    "PREVIOUS_APPROVAL_BATCH_STATUS": (
                        existing_approval_batch_status
                    ),
                    "PROSPECT_ID": prospect_id,
                    "ASSESSMENT_STATUS": assessment_status,
                    "VENDOR_GROUP": vendor_group,
                    "OVERALL_RISK_LEVEL": overall_risk_level,
                    "PREVIOUS_VENDOR_STATUS": (
                        current_vendor_status
                    ),
                    "VENDOR_REGISTRATION_STATUS": (
                        next_vendor_status
                    ),
                    "VENDOR_STATUS_UPDATED": (
                        vendor_status_updated
                    ),
                    "ISDRAFT": next_is_draft,
                    "TOTAL_RISK_LINES": len(
                        payload.lines
                    ),
                    "APPROVAL_CREATED": (
                        assessment_status == "SUBMITTED"
                    ),
                    "APPROVAL_BATCH_ID": approval_batch_id_value,
                    "BATCH_STATUS": approval_batch_status_value,
                    "REQUIRED_APPROVER_COUNT": (
                        required_approver_count_value
                    ),
                    "ASSIGNMENTS": approval_assignments_value,
                }

            except Exception:
                connection.rollback()
                cloud_connection.rollback()
                raise

            finally:
                cursor.close()
                cloud_cursor.close()

    email_result = {
        "TRIGGERED": False,
        "SENT": False,
        "RECIPIENT": None,
        "ERROR": None,
    }

    if resubmitted_email_context is not None:
        email_result["TRIGGERED"] = True
        email_result["RECIPIENT"] = (
            resubmitted_email_context[
                "TO_EMAIL"
            ]
        )

        if not resubmitted_email_context[
            "TO_EMAIL"
        ]:
            email_result["ERROR"] = (
                "Vendor email was not found "
                "in d365_VendorProspect"
            )
        else:
            try:
                email_result["SENT"] = (
                    send_returned_risk_assessment_email(
                        to_email=(
                            resubmitted_email_context[
                                "TO_EMAIL"
                            ]
                        ),
                        prospect_id=(
                            resubmitted_email_context[
                                "PROSPECT_ID"
                            ]
                        ),
                        company_name=(
                            resubmitted_email_context[
                                "COMPANY_NAME"
                            ]
                        ),
                        comments=(
                            resubmitted_email_context[
                                "COMMENTS"
                            ]
                        ),
                    )
                )

                if not email_result["SENT"]:
                    email_result["ERROR"] = (
                        "Email service returned False"
                    )

            except Exception as exc:
                email_result["ERROR"] = str(exc)

                logger.exception(
                    "[RISK RESUBMITTED EMAIL] Failed for "
                    f"ProspectId={prospect_id}"
                )

    response_payload[
        "EMAIL_NOTIFICATION"
    ] = email_result

    return response_payload


def risk_assessment_listpage_sync(
    payload: RiskAssessmentListPageRequest,
) -> dict[str, Any]:
    prospect_id = str(
        payload.ProspectId or ""
    ).strip().upper()

    if not prospect_id:
        raise RiskAssessmentConflictError(
            "ProspectId is required"
        )

    with get_secondary_connection() as connection:
        cursor = connection.cursor()

        try:
            cursor.execute(
                f"""
                SELECT TOP 1
                    RiskAssessmentId,
                    ProspectId,
                    AssessedByUserId,
                    SubmittedByUserId,
                    AssessmentStatus,
                    Comments,
                    VendorGroup,
                    OverallRiskLevel,
                    CreatedAt,
                    ModifiedAt,
                    SubmittedAt
                FROM {RISK_HEADER_TABLE}
                WHERE ProspectId = ?
                ORDER BY RiskAssessmentId DESC
                """,
                prospect_id,
            )

            header_row = cursor.fetchone()

            if header_row is None:
                return {
                    "success": True,
                    "prospectId": prospect_id,
                    "headers": [],
                    "lines": [],
                }

            header_columns = [
                column[0]
                for column in cursor.description
            ]

            latest_header = dict(
                zip(
                    header_columns,
                    header_row,
                )
            )

            risk_assessment_id = int(
                latest_header[
                    "RiskAssessmentId"
                ]
            )

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
                WHERE ProspectId = ?
                  AND RiskAssessmentId = ?
                ORDER BY RiskAssessmentDetailId
                """,
                (
                    prospect_id,
                    risk_assessment_id,
                ),
            )

            lines = _rows_to_dicts(
                cursor,
                cursor.fetchall(),
            )

            return {
                "success": True,
                "prospectId": prospect_id,
                "headers": [
                    {
                        "riskAssessmentId": (
                            latest_header.get(
                                "RiskAssessmentId"
                            )
                        ),
                        "prospectId": (
                            latest_header.get(
                                "ProspectId"
                            )
                        ),
                        "assessedByUserId": (
                            latest_header.get(
                                "AssessedByUserId"
                            )
                        ),
                        "submittedByUserId": (
                            latest_header.get(
                                "SubmittedByUserId"
                            )
                        ),
                        "assessmentStatus": (
                            latest_header.get(
                                "AssessmentStatus"
                            )
                        ),
                        "comments": (
                            latest_header.get(
                                "Comments"
                            )
                        ),
                        "vendorGroup": (
                            latest_header.get(
                                "VendorGroup"
                            )
                        ),
                        "overallRiskLevel": (
                            latest_header.get(
                                "OverallRiskLevel"
                            )
                        ),
                        "createdAt": _serialize(
                            latest_header.get(
                                "CreatedAt"
                            )
                        ),
                        "modifiedAt": _serialize(
                            latest_header.get(
                                "ModifiedAt"
                            )
                        ),
                        "submittedAt": _serialize(
                            latest_header.get(
                                "SubmittedAt"
                            )
                        ),
                    }
                ],
                "lines": [
                    {
                        "riskAssessmentDetailId": (
                            row.get(
                                "RiskAssessmentDetailId"
                            )
                        ),
                        "riskAssessmentId": (
                            row.get(
                                "RiskAssessmentId"
                            )
                        ),
                        "prospectId": (
                            row.get(
                                "ProspectId"
                            )
                        ),
                        "process": (
                            row.get(
                                "ProcessName"
                            )
                        ),
                        "identifiedRisk": (
                            row.get(
                                "IdentifiedRisk"
                            )
                        ),
                        "status": (
                            ""
                            if row.get(
                                "IsApplicable"
                            ) is None
                            else (
                                "Yes"
                                if bool(
                                    row.get(
                                        "IsApplicable"
                                    )
                                )
                                else "No"
                            )
                        ),
                        "riskLevel": (
                            _format_risk_level(
                                row.get(
                                    "RiskLevel"
                                )
                            )
                        ),
                        "remarks": (
                            row.get("Remarks")
                        ),
                        "createdAt": _serialize(
                            row.get("CreatedAt")
                        ),
                        "modifiedAt": _serialize(
                            row.get("ModifiedAt")
                        ),
                    }
                    for row in lines
                ],
            }

        finally:
            cursor.close()

# from __future__ import annotations

# from datetime import date, datetime
# from typing import Any

# import pyodbc
# from loguru import logger

# from app.core.config import settings
# from app.db.base import get_secondary_connection
# from app.schemas.riskassessment_schema import (
#     RiskAssessmentListPageRequest,
#     RiskAssessmentRequest,
# )
# from app.services.riskassessment_email_service import (
#     send_returned_risk_assessment_email,
# )
# from app.services.vendorapproval_service import (
#     ApprovalConflictError,
#     create_approval_workflow_in_transaction,
# )


# DB_SCHEMA = getattr(
#     settings,
#     "SECONDARY_DB_SCHEMA",
#     getattr(settings, "DB_SCHEMA", "dev"),
# )

# RISK_HEADER_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
# )
# RISK_DETAIL_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRiskAssessmentDetail]"
# )
# REGISTRATION_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
# )
# PROSPECT_TABLE = (
#     f"[{DB_SCHEMA}].[d365_VendorProspect]"
# )
# APPROVAL_BATCH_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
# )


# class RiskAssessmentNotFoundError(LookupError):
#     pass


# class RiskAssessmentConflictError(ValueError):
#     pass


# def _serialize(value: Any) -> Any:
#     if isinstance(value, (date, datetime)):
#         return value.isoformat()
#     return value


# def _rows_to_dicts(
#     cursor: pyodbc.Cursor,
#     rows,
# ) -> list[dict[str, Any]]:
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


# def _format_risk_level(
#     value: Any,
# ) -> str | None:
#     if value is None:
#         return None

#     risk_level = str(value).strip()

#     if not risk_level:
#         return None

#     return risk_level.title()


# def _normalize_status(
#     value: Any,
#     line_number: int,
#     *,
#     required: bool,
# ) -> bool | None:
#     if value is None or str(value).strip() == "":
#         if required:
#             raise RiskAssessmentConflictError(
#                 f"status is required for line {line_number}"
#             )
#         return None

#     if isinstance(value, bool):
#         return value

#     if isinstance(value, int):
#         if value == 1:
#             return True
#         if value == 0:
#             return False

#     normalized = str(value).strip().lower()

#     if normalized in {
#         "yes",
#         "true",
#         "1",
#         "y",
#     }:
#         return True

#     if normalized in {
#         "no",
#         "false",
#         "0",
#         "n",
#     }:
#         return False

#     raise RiskAssessmentConflictError(
#         f"status must be Yes or No for line {line_number}"
#     )


# def _normalize_risk_level(
#     applicable: bool | None,
#     value: Any,
#     line_number: int,
# ) -> str | None:
#     risk_level = str(
#         value or ""
#     ).strip().upper()

#     if applicable is False:
#         return None

#     if not risk_level:
#         return None

#     if risk_level not in {
#         "LOW",
#         "MEDIUM",
#         "HIGH",
#     }:
#         raise RiskAssessmentConflictError(
#             "riskLevel must be Low, Medium or High "
#             f"for line {line_number}"
#         )

#     return risk_level


# def _get_vendor_email_context(
#     cursor: pyodbc.Cursor,
#     prospect_id: str,
# ) -> tuple[str | None, str | None]:
#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             Name,
#             Email
#         FROM {PROSPECT_TABLE}
#         WHERE ProspectId = ?
#         """,
#         prospect_id,
#     )

#     row = cursor.fetchone()

#     if row is None:
#         return None, None

#     company_name = (
#         str(row[0]).strip()
#         if row[0] is not None
#         else None
#     )

#     vendor_email = (
#         str(row[1]).strip()
#         if row[1] is not None
#         else None
#     )

#     return company_name, vendor_email


# def _get_latest_assessment(
#     cursor: pyodbc.Cursor,
#     prospect_id: str,
# ) -> tuple[int, str] | None:
#     cursor.execute(
#         f"""
#         SELECT TOP 1
#             RiskAssessmentId,
#             AssessmentStatus
#         FROM {RISK_HEADER_TABLE}
#         WITH
#         (
#             UPDLOCK,
#             HOLDLOCK
#         )
#         WHERE ProspectId = ?
#         ORDER BY RiskAssessmentId DESC
#         """,
#         prospect_id,
#     )

#     row = cursor.fetchone()

#     if row is None:
#         return None

#     return (
#         int(row[0]),
#         str(row[1] or "").strip().upper(),
#     )


# def _get_existing_approval_batch(
#     cursor: pyodbc.Cursor,
#     risk_assessment_id: int,
# ) -> tuple[int, str] | None:
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

#     if row is None:
#         return None

#     return (
#         int(row[0]),
#         str(row[1] or "").strip().upper(),
#     )


# def _insert_risk_header(
#     cursor: pyodbc.Cursor,
#     *,
#     prospect_id: str,
#     assessed_by_user_id: Any,
#     submitted_by_user_id: Any,
#     assessment_status: str,
#     comments: str | None,
#     vendor_group: str | None,
# ) -> int:
#     cursor.execute(
#         f"""
#         INSERT INTO {RISK_HEADER_TABLE}
#         (
#             ProspectId,
#             AssessedByUserId,
#             SubmittedByUserId,
#             AssessmentStatus,
#             Comments,
#             VendorGroup,
#             CreatedAt,
#             ModifiedAt,
#             SubmittedAt
#         )
#         OUTPUT INSERTED.RiskAssessmentId
#         VALUES
#         (
#             ?,
#             ?,
#             ?,
#             ?,
#             ?,
#             ?,
#             SYSUTCDATETIME(),
#             SYSUTCDATETIME(),
#             CASE
#                 WHEN ? = N'DRAFT'
#                 THEN NULL
#                 ELSE SYSUTCDATETIME()
#             END
#         )
#         """,
#         (
#             prospect_id,
#             assessed_by_user_id,
#             submitted_by_user_id,
#             assessment_status,
#             comments,
#             vendor_group,
#             assessment_status,
#         ),
#     )

#     row = cursor.fetchone()

#     if row is None:
#         raise RiskAssessmentConflictError(
#             "Risk-assessment header could not be created"
#         )

#     return int(row[0])


# def _update_risk_header(
#     cursor: pyodbc.Cursor,
#     *,
#     risk_assessment_id: int,
#     prospect_id: str,
#     assessed_by_user_id: Any,
#     submitted_by_user_id: Any,
#     assessment_status: str,
#     comments: str | None,
#     vendor_group: str | None,
# ) -> None:
#     cursor.execute(
#         f"""
#         UPDATE {RISK_HEADER_TABLE}
#         SET
#             AssessedByUserId = ?,
#             SubmittedByUserId = ?,
#             AssessmentStatus = ?,
#             Comments = ?,
#             VendorGroup = COALESCE(?, VendorGroup),
#             ModifiedAt = SYSUTCDATETIME(),
#             SubmittedAt =
#                 CASE
#                     WHEN ? = N'DRAFT'
#                     THEN NULL
#                     ELSE SYSUTCDATETIME()
#                 END
#         WHERE RiskAssessmentId = ?
#           AND ProspectId = ?
#         """,
#         (
#             assessed_by_user_id,
#             submitted_by_user_id,
#             assessment_status,
#             comments,
#             vendor_group,
#             assessment_status,
#             risk_assessment_id,
#             prospect_id,
#         ),
#     )

#     if cursor.rowcount != 1:
#         raise RiskAssessmentConflictError(
#             "Risk-assessment header could not be updated"
#         )


# def _replace_risk_lines(
#     cursor: pyodbc.Cursor,
#     *,
#     risk_assessment_id: int,
#     prospect_id: str,
#     assessment_status: str,
#     lines: list[Any],
# ) -> None:
#     cursor.execute(
#         f"""
#         DELETE FROM {RISK_DETAIL_TABLE}
#         WHERE RiskAssessmentId = ?
#           AND ProspectId = ?
#         """,
#         (
#             risk_assessment_id,
#             prospect_id,
#         ),
#     )

#     for line_number, line in enumerate(
#         lines,
#         start=1,
#     ):
#         process_name = str(
#             line.process or ""
#         ).strip()

#         identified_risk = str(
#             line.identifiedRisk or ""
#         ).strip()

#         if not process_name:
#             raise RiskAssessmentConflictError(
#                 f"process is required for line {line_number}"
#             )

#         if not identified_risk:
#             raise RiskAssessmentConflictError(
#                 "identifiedRisk is required for "
#                 f"line {line_number}"
#             )

#         applicable = _normalize_status(
#             line.status,
#             line_number,
#             required=assessment_status != "DRAFT",
#         )

#         risk_level = _normalize_risk_level(
#             applicable,
#             line.riskLevel,
#             line_number,
#         )

#         remarks = (
#             str(line.remarks).strip()
#             if line.remarks is not None
#             else None
#         )

#         db_is_applicable = (
#             None
#             if applicable is None
#             else 1 if applicable else 0
#         )

#         cursor.execute(
#             f"""
#             INSERT INTO {RISK_DETAIL_TABLE}
#             (
#                 RiskAssessmentId,
#                 ProspectId,
#                 ProcessName,
#                 IdentifiedRisk,
#                 IsApplicable,
#                 RiskLevel,
#                 Remarks,
#                 CreatedAt,
#                 ModifiedAt
#             )
#             VALUES
#             (
#                 ?,
#                 ?,
#                 ?,
#                 ?,
#                 ?,
#                 ?,
#                 ?,
#                 SYSUTCDATETIME(),
#                 SYSUTCDATETIME()
#             )
#             """,
#             (
#                 risk_assessment_id,
#                 prospect_id,
#                 process_name,
#                 identified_risk,
#                 db_is_applicable,
#                 risk_level,
#                 remarks,
#             ),
#         )


# def create_risk_assessment_sync(
#     payload: RiskAssessmentRequest,
# ) -> dict[str, Any]:
#     if not payload.Header:
#         raise RiskAssessmentConflictError(
#             "Header is required"
#         )

#     if len(payload.Header) != 1:
#         raise RiskAssessmentConflictError(
#             "Header must contain exactly one record"
#         )

#     if not payload.lines:
#         raise RiskAssessmentConflictError(
#             "At least one risk-assessment line is required"
#         )

#     header = payload.Header[0]

#     prospect_id = str(
#         header.ProspectId or ""
#     ).strip().upper()

#     if not prospect_id:
#         raise RiskAssessmentConflictError(
#             "ProspectId is required"
#         )

#     assessment_status = str(
#         header.AssessmentStatus or "DRAFT"
#     ).strip().upper()

#     allowed_statuses = {
#         "DRAFT",
#         "SUBMITTED",
#         "RETURNED",
#         "REJECTED",
#         "RESUBMITTED",
#     }

#     if assessment_status not in allowed_statuses:
#         raise RiskAssessmentConflictError(
#             "AssessmentStatus must be DRAFT, SUBMITTED, "
#             "RETURNED, REJECTED or RESUBMITTED"
#         )

#     comments = (
#         str(header.Comments).strip()
#         if header.Comments is not None
#         else None
#     )
#     vendor_group = (
#         str(header.VendorGroup).strip()
#         if header.VendorGroup is not None
#         else None
#     )

#     vendor_group = vendor_group or None

#     if assessment_status != "DRAFT" and not vendor_group:
#         raise RiskAssessmentConflictError(
#             "VendorGroup is required when submitting "
#             "the risk assessment"
#         )

#     if (
#         assessment_status in {
#             "RETURNED",
#             "REJECTED",
#         }
#         and not comments
#     ):
#         raise RiskAssessmentConflictError(
#             "Comments are required when AssessmentStatus "
#             f"is {assessment_status}"
#         )

#     if (
#         assessment_status == "SUBMITTED"
#         and (
#             header.InitiatedByUserId is None
#             or header.InitiatedByUserId <= 0
#         )
#     ):
#         raise RiskAssessmentConflictError(
#             "InitiatedByUserId is required when "
#             "AssessmentStatus is SUBMITTED"
#         )

#     resubmitted_email_context: dict[str, Any] | None = None

#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 "SET XACT_ABORT ON;"
#             )

#             cursor.execute(
#                 f"""
#                 SELECT
#                     Status,
#                     IsDraft
#                 FROM {REGISTRATION_TABLE}
#                 WITH
#                 (
#                     UPDLOCK,
#                     HOLDLOCK
#                 )
#                 WHERE ProspectId = ?
#                 """,
#                 prospect_id,
#             )

#             registration_row = cursor.fetchone()

#             if registration_row is None:
#                 raise RiskAssessmentNotFoundError(
#                     "Vendor registration was not found for "
#                     f"ProspectId={prospect_id}"
#                 )

#             current_vendor_status = str(
#                 registration_row[0] or ""
#             ).strip().upper()

#             current_is_draft = int(
#                 registration_row[1] or 0
#             )

#             if assessment_status in {
#                 "DRAFT",
#                 "SUBMITTED",
#                 "RETURNED",
#                 "REJECTED",
#             }:
#                 if current_vendor_status not in {
#                     "TO_EVALUATE",
#                     "RESUBMITTED",
#                 }:
#                     raise RiskAssessmentConflictError(
#                         "Risk action requires "
#                         "VendorRegistration.Status "
#                         "TO_EVALUATE or RESUBMITTED. "
#                         f"Current status={current_vendor_status}"
#                     )

#             if assessment_status == "RESUBMITTED":
#                 if current_vendor_status not in {
#                     "TO_EVALUATE",
#                     "RETURNED",
#                     "REJECTED",
#                 }:
#                     raise RiskAssessmentConflictError(
#                         "RESUBMITTED requires "
#                         "VendorRegistration.Status "
#                         "TO_EVALUATE, RETURNED or REJECTED. "
#                         f"Current status={current_vendor_status}"
#                     )

#             latest_assessment = _get_latest_assessment(
#                 cursor,
#                 prospect_id,
#             )

#             previous_assessment_status = None
#             previous_risk_assessment_id = None
#             existing_approval_batch_id = None
#             existing_approval_batch_status = None
#             assessment_record_action = "CREATED"
#             new_approval_cycle = False

#             if latest_assessment is not None:
#                 (
#                     previous_risk_assessment_id,
#                     previous_assessment_status,
#                 ) = latest_assessment

#                 existing_batch = _get_existing_approval_batch(
#                     cursor,
#                     previous_risk_assessment_id,
#                 )

#                 if existing_batch is not None:
#                     (
#                         existing_approval_batch_id,
#                         existing_approval_batch_status,
#                     ) = existing_batch

#             # A RiskAssessmentId can have only one approval batch.
#             # When a returned/rejected assessment is being resubmitted,
#             # create a new assessment row for the next approval cycle.
#             new_approval_cycle = bool(
#                 latest_assessment is not None
#                 and existing_approval_batch_id is not None
#                 and assessment_status in {
#                     "RESUBMITTED",
#                     "SUBMITTED",
#                 }
#             )

#             editable_statuses = {
#                 "DRAFT",
#                 "RETURNED",
#                 "REJECTED",
#                 "RESUBMITTED",
#             }

#             can_update_existing = bool(
#                 latest_assessment is not None
#                 and previous_assessment_status
#                 in editable_statuses
#                 and not new_approval_cycle
#             )

#             if can_update_existing:
#                 risk_assessment_id = int(
#                     previous_risk_assessment_id
#                 )

#                 _update_risk_header(
#                     cursor,
#                     risk_assessment_id=(
#                         risk_assessment_id
#                     ),
#                     prospect_id=prospect_id,
#                     assessed_by_user_id=(
#                         header.AssessedByUserId
#                     ),
#                     submitted_by_user_id=(
#                         header.SubmittedByUserId
#                     ),
#                     assessment_status=(
#                         assessment_status
#                     ),
#                     comments=comments,
#                     vendor_group=vendor_group,
#                 )

#                 assessment_record_action = "UPDATED"

#             else:
#                 risk_assessment_id = _insert_risk_header(
#                     cursor,
#                     prospect_id=prospect_id,
#                     assessed_by_user_id=(
#                         header.AssessedByUserId
#                     ),
#                     submitted_by_user_id=(
#                         header.SubmittedByUserId
#                     ),
#                     assessment_status=(
#                         assessment_status
#                     ),
#                     comments=comments,
#                     vendor_group=vendor_group,
#                 )

#                 assessment_record_action = (
#                     "NEW_APPROVAL_CYCLE_CREATED"
#                     if new_approval_cycle
#                     else "CREATED"
#                 )

#             _replace_risk_lines(
#                 cursor,
#                 risk_assessment_id=(
#                     risk_assessment_id
#                 ),
#                 prospect_id=prospect_id,
#                 assessment_status=(
#                     assessment_status
#                 ),
#                 lines=payload.lines,
#             )

#             approval_result = {
#                 "APPROVAL_BATCH_ID": None,
#                 "BATCH_STATUS": None,
#                 "REQUIRED_APPROVER_COUNT": 0,
#                 "ASSIGNMENTS": [],
#             }

#             vendor_status_updated = False
#             next_vendor_status = current_vendor_status
#             next_is_draft = current_is_draft

#             if assessment_status == "SUBMITTED":
#                 try:
#                     approval_result = (
#                         create_approval_workflow_in_transaction(
#                             cursor=cursor,
#                             risk_assessment_id=(
#                                 risk_assessment_id
#                             ),
#                             prospect_id=prospect_id,
#                             initiated_by_user_id=(
#                                 header.InitiatedByUserId
#                             ),
#                         )
#                     )
#                 except ApprovalConflictError as exc:
#                     raise RiskAssessmentConflictError(
#                         str(exc)
#                     ) from exc

#                 next_vendor_status = "IN_APPROVAL"
#                 next_is_draft = 1
#                 vendor_status_updated = True

#             elif assessment_status == "RETURNED":
#                 next_vendor_status = "RETURNED"
#                 next_is_draft = 0
#                 vendor_status_updated = True

#             elif assessment_status == "REJECTED":
#                 next_vendor_status = "REJECTED"
#                 next_is_draft = 0
#                 vendor_status_updated = True

#             elif assessment_status == "RESUBMITTED":
#                 next_vendor_status = "RESUBMITTED"
#                 next_is_draft = 0
#                 vendor_status_updated = True

#                 company_name, vendor_email = (
#                     _get_vendor_email_context(
#                         cursor,
#                         prospect_id,
#                     )
#                 )

#                 resubmitted_email_context = {
#                     "TO_EMAIL": vendor_email,
#                     "COMPANY_NAME": company_name,
#                     "COMMENTS": comments,
#                     "PROSPECT_ID": prospect_id,
#                 }

#             if vendor_status_updated:
#                 cursor.execute(
#                     f"""
#                     UPDATE {REGISTRATION_TABLE}
#                     SET
#                         Status = ?,
#                         IsDraft = ?,
#                         ModifiedOn = SYSUTCDATETIME()
#                     WHERE ProspectId = ?
#                     """,
#                     (
#                         next_vendor_status,
#                         next_is_draft,
#                         prospect_id,
#                     ),
#                 )

#                 if cursor.rowcount != 1:
#                     raise RiskAssessmentConflictError(
#                         "Vendor registration status "
#                         "could not be updated"
#                     )

#             connection.commit()

#             response_payload = {
#                 "SUCCESS": True,
#                 "MESSAGE": (
#                     "Risk assessment processed successfully"
#                 ),
#                 "RISK_ASSESSMENT_ID": risk_assessment_id,
#                 "PREVIOUS_RISK_ASSESSMENT_ID": (
#                     previous_risk_assessment_id
#                 ),
#                 "ASSESSMENT_RECORD_ACTION": (
#                     assessment_record_action
#                 ),
#                 "NEW_APPROVAL_CYCLE": (
#                     new_approval_cycle
#                 ),
#                 "PREVIOUS_ASSESSMENT_STATUS": (
#                     previous_assessment_status
#                 ),
#                 "PREVIOUS_APPROVAL_BATCH_ID": (
#                     existing_approval_batch_id
#                 ),
#                 "PREVIOUS_APPROVAL_BATCH_STATUS": (
#                     existing_approval_batch_status
#                 ),
#                 "PROSPECT_ID": prospect_id,
#                 "ASSESSMENT_STATUS": assessment_status,
#                 "VENDOR_GROUP": vendor_group,
#                 "PREVIOUS_VENDOR_STATUS": (
#                     current_vendor_status
#                 ),
#                 "VENDOR_REGISTRATION_STATUS": (
#                     next_vendor_status
#                 ),
#                 "VENDOR_STATUS_UPDATED": (
#                     vendor_status_updated
#                 ),
#                 "ISDRAFT": next_is_draft,
#                 "TOTAL_RISK_LINES": len(
#                     payload.lines
#                 ),
#                 "APPROVAL_CREATED": (
#                     assessment_status == "SUBMITTED"
#                 ),
#                 "APPROVAL_BATCH_ID": (
#                     approval_result[
#                         "APPROVAL_BATCH_ID"
#                     ]
#                 ),
#                 "BATCH_STATUS": (
#                     approval_result[
#                         "BATCH_STATUS"
#                     ]
#                 ),
                
#                 "REQUIRED_APPROVER_COUNT": (
#                     approval_result[
#                         "REQUIRED_APPROVER_COUNT"
#                     ]
#                 ),
#                 "ASSIGNMENTS": (
#                     approval_result[
#                         "ASSIGNMENTS"
#                     ]
#                 ),
#             }

#         except Exception:
#             connection.rollback()
#             raise

#         finally:
#             cursor.close()

#     email_result = {
#         "TRIGGERED": False,
#         "SENT": False,
#         "RECIPIENT": None,
#         "ERROR": None,
#     }

#     if resubmitted_email_context is not None:
#         email_result["TRIGGERED"] = True
#         email_result["RECIPIENT"] = (
#             resubmitted_email_context[
#                 "TO_EMAIL"
#             ]
#         )

#         if not resubmitted_email_context[
#             "TO_EMAIL"
#         ]:
#             email_result["ERROR"] = (
#                 "Vendor email was not found "
#                 "in d365_VendorProspect"
#             )
#         else:
#             try:
#                 email_result["SENT"] = (
#                     send_returned_risk_assessment_email(
#                         to_email=(
#                             resubmitted_email_context[
#                                 "TO_EMAIL"
#                             ]
#                         ),
#                         prospect_id=(
#                             resubmitted_email_context[
#                                 "PROSPECT_ID"
#                             ]
#                         ),
#                         company_name=(
#                             resubmitted_email_context[
#                                 "COMPANY_NAME"
#                             ]
#                         ),
#                         comments=(
#                             resubmitted_email_context[
#                                 "COMMENTS"
#                             ]
#                         ),
#                     )
#                 )

#                 if not email_result["SENT"]:
#                     email_result["ERROR"] = (
#                         "Email service returned False"
#                     )

#             except Exception as exc:
#                 email_result["ERROR"] = str(exc)

#                 logger.exception(
#                     "[RISK RESUBMITTED EMAIL] Failed for "
#                     f"ProspectId={prospect_id}"
#                 )

#     response_payload[
#         "EMAIL_NOTIFICATION"
#     ] = email_result

#     return response_payload


# def risk_assessment_listpage_sync(
#     payload: RiskAssessmentListPageRequest,
# ) -> dict[str, Any]:
#     prospect_id = str(
#         payload.ProspectId or ""
#     ).strip().upper()

#     if not prospect_id:
#         raise RiskAssessmentConflictError(
#             "ProspectId is required"
#         )

#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 f"""
#                 SELECT TOP 1
#                     RiskAssessmentId,
#                     ProspectId,
#                     AssessedByUserId,
#                     SubmittedByUserId,
#                     AssessmentStatus,
#                     Comments,
#                     VendorGroup,
#                     CreatedAt,
#                     ModifiedAt,
#                     SubmittedAt
#                 FROM {RISK_HEADER_TABLE}
#                 WHERE ProspectId = ?
#                 ORDER BY RiskAssessmentId DESC
#                 """,
#                 prospect_id,
#             )

#             header_row = cursor.fetchone()

#             if header_row is None:
#                 return {
#                     "success": True,
#                     "prospectId": prospect_id,
#                     "headers": [],
#                     "lines": [],
#                 }

#             header_columns = [
#                 column[0]
#                 for column in cursor.description
#             ]

#             latest_header = dict(
#                 zip(
#                     header_columns,
#                     header_row,
#                 )
#             )

#             risk_assessment_id = int(
#                 latest_header[
#                     "RiskAssessmentId"
#                 ]
#             )

#             cursor.execute(
#                 f"""
#                 SELECT
#                     RiskAssessmentDetailId,
#                     RiskAssessmentId,
#                     ProspectId,
#                     ProcessName,
#                     IdentifiedRisk,
#                     IsApplicable,
#                     RiskLevel,
#                     Remarks,
#                     CreatedAt,
#                     ModifiedAt
#                 FROM {RISK_DETAIL_TABLE}
#                 WHERE ProspectId = ?
#                   AND RiskAssessmentId = ?
#                 ORDER BY RiskAssessmentDetailId
#                 """,
#                 (
#                     prospect_id,
#                     risk_assessment_id,
#                 ),
#             )

#             lines = _rows_to_dicts(
#                 cursor,
#                 cursor.fetchall(),
#             )

#             return {
#                 "success": True,
#                 "prospectId": prospect_id,
#                 "headers": [
#                     {
#                         "riskAssessmentId": (
#                             latest_header.get(
#                                 "RiskAssessmentId"
#                             )
#                         ),
#                         "prospectId": (
#                             latest_header.get(
#                                 "ProspectId"
#                             )
#                         ),
#                         "assessedByUserId": (
#                             latest_header.get(
#                                 "AssessedByUserId"
#                             )
#                         ),
#                         "submittedByUserId": (
#                             latest_header.get(
#                                 "SubmittedByUserId"
#                             )
#                         ),
#                         "assessmentStatus": (
#                             latest_header.get(
#                                 "AssessmentStatus"
#                             )
#                         ),
#                         "comments": (
#                             latest_header.get(
#                                 "Comments"
#                             )
#                         ),
#                         "vendorGroup": (
#                             latest_header.get(
#                                 "VendorGroup"
#                             )
#                         ),
#                         "createdAt": _serialize(
#                             latest_header.get(
#                                 "CreatedAt"
#                             )
#                         ),
#                         "modifiedAt": _serialize(
#                             latest_header.get(
#                                 "ModifiedAt"
#                             )
#                         ),
#                         "submittedAt": _serialize(
#                             latest_header.get(
#                                 "SubmittedAt"
#                             )
#                         ),
#                     }
#                 ],
#                 "lines": [
#                     {
#                         "riskAssessmentDetailId": (
#                             row.get(
#                                 "RiskAssessmentDetailId"
#                             )
#                         ),
#                         "riskAssessmentId": (
#                             row.get(
#                                 "RiskAssessmentId"
#                             )
#                         ),
#                         "prospectId": (
#                             row.get(
#                                 "ProspectId"
#                             )
#                         ),
#                         "process": (
#                             row.get(
#                                 "ProcessName"
#                             )
#                         ),
#                         "identifiedRisk": (
#                             row.get(
#                                 "IdentifiedRisk"
#                             )
#                         ),
#                         "status": (
#                             ""
#                             if row.get(
#                                 "IsApplicable"
#                             ) is None
#                             else (
#                                 "Yes"
#                                 if bool(
#                                     row.get(
#                                         "IsApplicable"
#                                     )
#                                 )
#                                 else "No"
#                             )
#                         ),
#                         "riskLevel": (
#                             _format_risk_level(
#                                 row.get(
#                                     "RiskLevel"
#                                 )
#                             )
#                         ),
#                         "remarks": (
#                             row.get("Remarks")
#                         ),
#                         "createdAt": _serialize(
#                             row.get("CreatedAt")
#                         ),
#                         "modifiedAt": _serialize(
#                             row.get("ModifiedAt")
#                         ),
#                     }
#                     for row in lines
#                 ],
#             }

#         finally:
#             cursor.close()