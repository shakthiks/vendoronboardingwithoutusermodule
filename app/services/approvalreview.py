from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_secondary_connection,
)
from app.schemas.approvalreview_schemas import (
    ApprovalReviewUpdateRequest,
)
from app.services.riskassessment_email_service import (
    send_returned_risk_assessment_email,
)


# Cloud database schema:
# - HIQ_VendorRegistration
CLOUD_DB_SCHEMA = settings.DB_SCHEMA

# Local / secondary database schema:
# - HIQ_VendorRiskAssessment
# - HIQ_VendorApprovalBatch
# - HIQ_VendorApprovalAssignment
# - HIQ_Users
LOCAL_DB_SCHEMA = getattr(
    settings,
    "SECONDARY_DB_SCHEMA",
    getattr(settings, "DB_SCHEMA", "dev"),
)

REGISTRATION_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[HIQ_VendorRegistration]"
)

RISK_ASSESSMENT_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
)

APPROVAL_BATCH_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
)

APPROVAL_ASSIGNMENT_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalAssignment]"
)

USERS_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_Users]"
)

# Accepts short/alternate forms from clients and normalizes them
# to the canonical status values used throughout this service.
STATUS_ALIASES = {
    "reject": "rejected",
    "rejected": "rejected",
    "return": "returned",
    "returned": "returned",
    "resubmit": "resubmitted",
    "resubmitted": "resubmitted",
    "approve": "approved",
    "approved": "approved",
}


class ApprovalReviewNotFoundError(LookupError):
    pass


class ApprovalReviewConflictError(ValueError):
    pass


def _normalize_prospect_id(
    value: Any,
) -> str:
    prospect_id = str(
        value or ""
    ).strip().upper()

    if not prospect_id:
        raise ApprovalReviewConflictError(
            "ProspectId is required"
        )

    return prospect_id


def _normalize_text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _normalize_review_status(
    value: Any,
) -> str:
    raw_status = str(value or "").strip().lower()

    if not raw_status:
        raise ApprovalReviewConflictError(
            "status is required"
        )

    normalized_status = STATUS_ALIASES.get(raw_status)

    if normalized_status is None:
        raise ApprovalReviewConflictError(
            "status must be approved, returned, "
            "resubmitted or rejected"
        )

    return normalized_status


# ============================================================
# APPROVAL LIST PAGE
# ============================================================
def approval_listpage(prospect_id):
    prospect_id = str(prospect_id or "").strip().upper()

    with get_secondary_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(
                f"""
                SELECT TOP 1
                    ApprovalBatchId,
                    RiskAssessmentId,
                    BatchStatus
                FROM {APPROVAL_BATCH_TABLE}
                WHERE ProspectId = ?
                ORDER BY ApprovalBatchId DESC
                """,
                prospect_id,
            )

            batch_row = cursor.fetchone()

            if not batch_row:
                return {
                    "approvalBatchId": None,
                    "riskAssessmentId": None,
                    "batchStatus": None,
                    "approval_listpage": [],
                }

            latest_approval_batch_id = batch_row[0]
            latest_risk_assessment_id = batch_row[1]
            latest_batch_status = batch_row[2]

            cursor.execute(
                f"""
                SELECT
                    u.FullName,
                    a.DecisionStatus,
                    a.DecisionRemarks,
                    a.DecisionAt
                FROM {APPROVAL_ASSIGNMENT_TABLE} AS a
                JOIN {USERS_TABLE} AS u
                    ON a.ApproverUserId = u.UserId
                WHERE a.ProspectId = ?
                  AND a.ApprovalBatchId = ?
                ORDER BY a.ApprovalAssignmentId
                """,
                (
                    prospect_id,
                    latest_approval_batch_id,
                ),
            )

            rows = cursor.fetchall()

            cols = [c[0].lower() for c in cursor.description]

            result = [dict(zip(cols, row)) for row in rows]

            return {
                "approvalBatchId": latest_approval_batch_id,
                "riskAssessmentId": latest_risk_assessment_id,
                "batchStatus": latest_batch_status,
                "approval_listpage": result,
            }

        finally:
            cursor.close()


# ============================================================
# APPROVAL REVIEW UPDATE
# ============================================================

def approvalreview_update(
    request: ApprovalReviewUpdateRequest,
) -> dict[str, Any]:
    """
    Updates only the assessment linked to the latest approval batch.

    It does not update all risk assessments belonging to the prospect.
    """

    prospect_id = _normalize_prospect_id(
        request.ProspectId
    )

    normalized_status = _normalize_review_status(
        request.status
    )

    comments = _normalize_text(
        request.Comments
    )

    if (
        normalized_status
        in {
            "resubmitted",
            "returned",
            "rejected",
        }
        and not comments
    ):
        raise ApprovalReviewConflictError(
            "Comments are required when status is "
            f"{normalized_status}"
        )

    email_sent = False
    company_name = None
    risk_assessment_status = None
    approval_batch_id = None
    risk_assessment_id = None
    batch_status = None

    # Cloud connection:
    #   HIQ_VendorRegistration
    #
    # Local / secondary connection:
    #   HIQ_VendorRiskAssessment
    #   HIQ_VendorApprovalBatch
    with get_connection() as cloud_conn:
        cloud_cursor = cloud_conn.cursor()

        with get_secondary_connection() as local_conn:
            local_cursor = local_conn.cursor()

            try:
                cloud_cursor.execute(
                    "SET XACT_ABORT ON;"
                )
                local_cursor.execute(
                    "SET XACT_ABORT ON;"
                )

                # ====================================================
                # CLOUD: LOCK / READ VENDOR REGISTRATION
                # ====================================================
                cloud_cursor.execute(
                    f"""
                    SELECT
                        CompanyName,
                        Status
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

                vendor_row = cloud_cursor.fetchone()

                if vendor_row is None:
                    raise ApprovalReviewNotFoundError(
                        "No vendor registration found for "
                        f"ProspectId={prospect_id}"
                    )

                company_name = vendor_row[0]

                # ====================================================
                # LOCAL: GET LATEST APPROVAL CYCLE
                # ====================================================
                local_cursor.execute(
                    f"""
                    SELECT TOP 1
                        ApprovalBatchId,
                        RiskAssessmentId,
                        BatchStatus
                    FROM {APPROVAL_BATCH_TABLE}
                    WITH
                    (
                        UPDLOCK,
                        HOLDLOCK
                    )
                    WHERE ProspectId = ?
                    ORDER BY ApprovalBatchId DESC
                    """,
                    prospect_id,
                )

                batch_row = local_cursor.fetchone()

                if batch_row is None:
                    raise ApprovalReviewNotFoundError(
                        "No approval batch found for "
                        f"ProspectId={prospect_id}"
                    )

                approval_batch_id = int(
                    batch_row[0]
                )

                risk_assessment_id = int(
                    batch_row[1]
                )

                current_batch_status = str(
                    batch_row[2] or ""
                ).strip().upper()

                # ====================================================
                # DETERMINE FINAL STATUSES
                # ====================================================
                if normalized_status == "returned":
                    registration_status = "RETURNED"
                    risk_assessment_status = "RETURNED"
                    batch_status = "RETURNED"
                    registration_is_draft = 0

                elif normalized_status == "resubmitted":
                    registration_status = "RESUBMITTED"
                    risk_assessment_status = "RETURNED"
                    batch_status = "RETURNED"
                    registration_is_draft = 0

                elif normalized_status == "rejected":
                    registration_status = "REJECTED"
                    risk_assessment_status = "REJECTED"

                    # Current BatchStatus constraint normally contains:
                    # PENDING, APPROVED and RETURNED.
                    batch_status = "RETURNED"
                    registration_is_draft = 0

                else:
                    registration_status = "APPROVED"
                    risk_assessment_status = "APPROVED"
                    batch_status = "APPROVED"
                    registration_is_draft = 1

                # ====================================================
                # LOCAL: UPDATE LATEST RISK ASSESSMENT ONLY
                # ====================================================

                local_cursor.execute(
                    f"""
                    UPDATE {RISK_ASSESSMENT_TABLE}
                    SET
                        AssessmentStatus = ?,
                        Comments = ?,
                        ModifiedAt = SYSUTCDATETIME()
                    WHERE RiskAssessmentId = ?
                      AND ProspectId = ?
                    """,
                    (
                        risk_assessment_status,
                        comments,
                        risk_assessment_id,
                        prospect_id,
                    ),
                )

                if local_cursor.rowcount != 1:
                    raise ApprovalReviewConflictError(
                        "Latest risk assessment could not be updated. "
                        f"RiskAssessmentId={risk_assessment_id}"
                    )

                # ====================================================
                # LOCAL: UPDATE LATEST APPROVAL BATCH ONLY
                # ====================================================

                local_cursor.execute(
                    f"""
                    UPDATE {APPROVAL_BATCH_TABLE}
                    SET
                        BatchStatus = ?,
                        CompletedAt =
                            CASE
                                WHEN ? = N'PENDING'
                                THEN NULL
                                ELSE SYSUTCDATETIME()
                            END
                    WHERE ApprovalBatchId = ?
                      AND ProspectId = ?
                    """,
                    (
                        batch_status,
                        batch_status,
                        approval_batch_id,
                        prospect_id,
                    ),
                )

                if local_cursor.rowcount != 1:
                    raise ApprovalReviewConflictError(
                        "Latest approval batch could not be updated. "
                        f"ApprovalBatchId={approval_batch_id}"
                    )

                # ====================================================
                # CLOUD: UPDATE VENDOR REGISTRATION
                # ====================================================

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
                        registration_status,
                        registration_is_draft,
                        prospect_id,
                    ),
                )

                if cloud_cursor.rowcount != 1:
                    raise ApprovalReviewConflictError(
                        "Vendor registration could not be updated"
                    )

                # Local risk/approval data is the workflow source.
                # Commit it first, then commit the cloud registration.
                local_conn.commit()
                cloud_conn.commit()

            except Exception:
                # Roll back any transaction that has not already committed.
                try:
                    local_conn.rollback()
                except Exception:
                    pass

                try:
                    cloud_conn.rollback()
                except Exception:
                    pass

                raise

            finally:
                local_cursor.close()
                cloud_cursor.close()

    # ============================================================
    # EMAIL AFTER SUCCESSFUL COMMIT
    # ============================================================

    email_error = None

    if normalized_status in {
        "resubmitted",
        "returned",
    }:
        to_email = _normalize_text(
            request.ToEmail
        )

        if not to_email:
            email_error = (
                "ToEmail is required to send "
                "the returned assessment email"
            )

        else:
            try:
                email_sent = (
                    send_returned_risk_assessment_email(
                        to_email=to_email,
                        prospect_id=prospect_id,
                        company_name=company_name,
                        comments=comments,
                    )
                )

                if not email_sent:
                    email_error = (
                        "Email service returned False"
                    )

            except Exception as exc:
                email_error = str(exc)

    return {
        "success": True,
        "approvalreview_update":
            "updated successfully",
        "prospectId":
            prospect_id,
        "approvalBatchId":
            approval_batch_id,
        "riskAssessmentId":
            risk_assessment_id,
        "previousBatchStatus":
            current_batch_status,
        "batchStatus":
            batch_status,
        "vendorRegistrationStatus":
            registration_status,
        "riskAssessmentStatus":
            risk_assessment_status,
        "isDraft":
            registration_is_draft,
        "emailSent":
            email_sent,
        "emailError":
            email_error,
    }