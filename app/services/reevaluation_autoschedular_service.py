# app/services/reevaluation_auto_scheduler_service.py

from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from typing import Any

from app.core.config import settings
from app.db.base import get_connection
from app.services.email_service import send_email
from app.utils.reevalution_email_template import (
    build_reevaluation_email_html,
)


# ============================================================
# SCHEMA / TABLES
# ============================================================

SCHEMA = settings.DB_SCHEMA

VENDOR_PROSPECT_TABLE = (
    f"{SCHEMA}.d365_VendorProspect"
)

VENDOR_REGISTRATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorRegistration"
)

VENDOR_CERTIFICATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorCertification"
)

VENDOR_REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

VENDOR_REEVALUATION_CC_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationCC"
)

VENDOR_REEVALUATION_CC_MASTER_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationCCMaster"
)

VENDOR_REEVALUATION_ACTION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationAction"
)

SCHEDULER_LOG_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationSchedulerLog"
)


# ============================================================
# BUSINESS RULES
# ============================================================

SYSTEM_USER = "SYSTEM"

# Requirement:
# Send one document-expiry reminder when a certificate enters
# the 90-day expiry window.
CERTIFICATE_REMINDER_DAYS = 90

ACTIVE_REEVALUATION_STATUSES = {
    "PENDING",
    "MAIL_SENT",
    "IN_PROGRESS",
    "SUBMITTED",
    "UNDER_REVIEW",
    "RESUBMISSION_REQUESTED",
    "RESUBMITTED",
}

EVENT_CERTIFICATE_REMINDER = (
    "CERTIFICATE_EXPIRY_REMINDER"
)

EVENT_CERTIFICATE_EXPIRED = (
    "CERTIFICATE_EXPIRED_REEVALUATION"
)

EVENT_RISK_DUE = (
    "RISK_DUE_REEVALUATION"
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


def _send_html_email(
    *,
    to_email: str,
    cc_emails: list[str],
    subject: str,
    html_body: str,
) -> None:

    result = send_email(
        to_email=to_email,
        subject=subject,
        body=html_body,
        cc_emails=cc_emails,
    )

    if result is not True:
        raise RuntimeError(
            "Email service failed."
        )


def _get_default_cc(
    cursor,
) -> list[dict[str, Any]]:

    cursor.execute(
        f"""
        SELECT
            CCMasterId,
            DisplayName,
            EmailAddress
        FROM {VENDOR_REEVALUATION_CC_MASTER_TABLE}
        WHERE
            IsActive = 1
        ORDER BY
            CCMasterId;
        """
    )

    return _rows_to_dict(
        cursor
    )


def _get_default_cc_emails(
    cc_rows: list[dict[str, Any]],
) -> list[str]:

    values = []

    for row in cc_rows:

        email = str(
            row.get(
                "emailaddress"
            )
            or ""
        ).strip()

        if email:
            values.append(
                email
            )

    # remove duplicates preserving order
    return list(
        dict.fromkeys(
            values
        )
    )


def _get_vendor_by_prospect(
    cursor,
    prospect_id: str,
) -> dict[str, Any] | None:

    cursor.execute(
        f"""
        SELECT TOP 1
            VP.ProspectSeq,
            VP.ProspectId,
            VP.VendorAccount,
            VP.Name AS VendorName,

            COALESCE(
                NULLIF(
                    LTRIM(
                        RTRIM(
                            VR.RepEmail
                        )
                    ),
                    ''
                ),
                NULLIF(
                    LTRIM(
                        RTRIM(
                            VP.Email
                        )
                    ),
                    ''
                )
            ) AS ToEmail

        FROM {VENDOR_PROSPECT_TABLE} VP

        LEFT JOIN {VENDOR_REGISTRATION_TABLE} VR
            ON VR.ProspectId =
               VP.ProspectId

        WHERE
            VP.ProspectId = ?
            AND VP.VendorAccount IS NOT NULL;
        """,
        prospect_id,
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return _row_to_dict(
        cursor,
        row,
    )


def _has_active_reevaluation(
    cursor,
    prospect_id: str,
) -> bool:

    placeholders = ",".join(
        "?"
        for _ in ACTIVE_REEVALUATION_STATUSES
    )

    values = sorted(
        ACTIVE_REEVALUATION_STATUSES
    )

    cursor.execute(
        f"""
        SELECT TOP 1
            ReevaluationId
        FROM {VENDOR_REEVALUATION_TABLE}
        WHERE
            ProspectId = ?
            AND IsActive = 1
            AND UPPER(Status) IN (
                {placeholders}
            )
        ORDER BY
            ReevaluationId DESC;
        """,
        prospect_id,
        *values,
    )

    return (
        cursor.fetchone()
        is not None
    )


def _get_previous_reevaluation(
    cursor,
    vendor_account: str,
) -> dict[str, Any] | None:

    cursor.execute(
        f"""
        SELECT TOP 1
            ReevaluationId,
            ReevaluationNo,
            ReevaluationCycle,
            Status
        FROM {VENDOR_REEVALUATION_TABLE}
        WHERE
            VendorAccount = ?
            AND IsActive = 1
        ORDER BY
            ReevaluationId DESC;
        """,
        vendor_account,
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return _row_to_dict(
        cursor,
        row,
    )


# ============================================================
# SCHEDULER LOG / IDEMPOTENCY
#
# This table is important because the 90-day reminder happens
# before a reevaluation record exists.
# It also prevents a daily scheduler from sending duplicate
# reminder/trigger emails for the same event.
# ============================================================

def _event_already_processed(
    cursor,
    *,
    prospect_id: str,
    event_type: str,
    reference_key: str,
) -> bool:

    cursor.execute(
        f"""
        SELECT TOP 1
            SchedulerLogId
        FROM {SCHEDULER_LOG_TABLE}
        WHERE
            ProspectId = ?
            AND EventType = ?
            AND ReferenceKey = ?
            AND Status = 'SENT';
        """,
        prospect_id,
        event_type,
        reference_key,
    )

    return (
        cursor.fetchone()
        is not None
    )


def _save_scheduler_log(
    cursor,
    *,
    prospect_id: str,
    vendor_account: str | None,
    event_type: str,
    reference_key: str,
    reference_date: date | None,
    reevaluation_id: int | None,
    status: str,
    to_email: str | None,
    error_message: str | None = None,
) -> None:

    cursor.execute(
        f"""
        INSERT INTO {SCHEDULER_LOG_TABLE}
        (
            ProspectId,
            VendorAccount,
            EventType,
            ReferenceKey,
            ReferenceDate,
            ReevaluationId,
            Status,
            ToEmail,
            ErrorMessage,
            CreatedAt
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
            ?,
            ?,
            SYSDATETIME()
        );
        """,
        prospect_id,
        vendor_account,
        event_type,
        reference_key,
        reference_date,
        reevaluation_id,
        status,
        to_email,
        (
            error_message[:2000]
            if error_message
            else None
        ),
    )


# ============================================================
# EMAIL BUILDERS
# ============================================================

def _build_certificate_reminder_email(
    *,
    vendor_name: str,
    expiry_date: date,
) -> tuple[str, str]:

    subject = (
        "Vendor Certificate Expiry Reminder"
    )

    expiry_text = (
        expiry_date.strftime(
            "%d %b %Y"
        )
    )

    content = f"""
    <p style="margin:0 0 16px 0;">
        Our records show that one or more certificates
        associated with your vendor profile are approaching
        their expiry date.
    </p>

    <div style="
        background:#fff7ed;
        border-left:4px solid #f59e0b;
        padding:14px 16px;
        margin:20px 0;
    ">
        <strong>Certificate Expiry Date:</strong><br>
        {escape(expiry_text)}
    </div>

    <p style="margin:0 0 16px 0;">
        This is an advance reminder to help you prepare the
        renewed certificate and any supporting documents.
    </p>

    <p style="margin:0;">
        A vendor reevaluation request will be issued
        automatically if the certificate expires without being
        renewed in our records.
    </p>
    """

    html_body = (
        build_reevaluation_email_html(
            vendor_name=
                vendor_name,

            content=
                content,

            action_url=
                None,

            action_text=
                "Open Vendor Reevaluation Form",
        )
    )

    return (
        subject,
        html_body,
    )


def _build_auto_reevaluation_email(
    *,
    vendor_name: str,
    reevaluation_id: int,
    trigger_reason: str,
    risk_level: str | None = None,
    expired_date: date | None = None,
) -> tuple[str, str]:

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

    if trigger_reason == (
        EVENT_CERTIFICATE_EXPIRED
    ):

        subject = (
            "Action Required - Vendor "
            "Reevaluation Due to Expired Certificate"
        )

        expired_text = (
            expired_date.strftime(
                "%d %b %Y"
            )
            if expired_date
            else ""
        )

        content = f"""
        <p style="margin:0 0 16px 0;">
            Our records indicate that one or more certificates
            associated with your vendor profile have expired.
        </p>

        <div style="
            background:#fef2f2;
            border-left:4px solid #dc2626;
            padding:14px 16px;
            margin:20px 0;
        ">
            <strong>Reason for Reevaluation:</strong><br>
            One or more vendor certificates have expired.
            {
                f"<br><strong>Expired Date:</strong> {escape(expired_text)}"
                if expired_text
                else ""
            }
        </div>

        <p style="margin:0;">
            Please review your vendor information and upload
            the latest valid documents through the Vendor
            Reevaluation Form.
        </p>
        """

    else:

        subject = (
            "Vendor Reevaluation Required"
        )

        content = f"""
        <p style="margin:0 0 16px 0;">
            Your vendor profile is now due for periodic
            reevaluation.
        </p>

        <div style="
            background:#eff6ff;
            border-left:4px solid #1F3864;
            padding:14px 16px;
            margin:20px 0;
        ">
            <strong>Reason for Reevaluation:</strong><br>
            Periodic vendor reevaluation is due based on the
            configured reevaluation schedule.
        </div>

        <p style="margin:0;">
            Please review your company information,
            certifications and supporting documents and submit
            the updated details through the Vendor
            Reevaluation Form.
        </p>
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
                "Open Vendor Reevaluation Form",
        )
    )

    return (
        subject,
        html_body,
    )


# ============================================================
# REEVALUATION CREATE / AUDIT
# ============================================================

def _create_auto_reevaluation(
    cursor,
    *,
    vendor: dict[str, Any],
    cycle: int,
    previous_reevaluation_id: int | None,
    trigger_reason: str,
) -> tuple[int, str]:

    cursor.execute(
        f"""
        INSERT INTO {VENDOR_REEVALUATION_TABLE}
        (
            ProspectSeq,
            ProspectId,
            VendorAccount,

            ReevaluationCycle,
            PreviousReevaluationId,

            TriggerType,
            TriggerReason,

            Status,

            ToEmail,

            TriggeredBy,
            TriggeredAt,

            MailStatus,
            MailAttemptCount,

            IsActive,

            CreatedAt,
            CreatedBy
        )

        OUTPUT
            INSERTED.ReevaluationId,
            INSERTED.ReevaluationNo

        VALUES
        (
            ?,
            ?,
            ?,

            ?,
            ?,

            'AUTO',
            ?,

            'PENDING',

            ?,

            ?,
            SYSDATETIME(),

            'PENDING',
            0,

            1,

            SYSDATETIME(),
            ?
        );
        """,
        vendor[
            "prospectseq"
        ],
        vendor[
            "prospectid"
        ],
        vendor[
            "vendoraccount"
        ],
        cycle,
        previous_reevaluation_id,
        trigger_reason,
        vendor[
            "toemail"
        ],
        SYSTEM_USER,
        SYSTEM_USER,
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "Failed to create automatic reevaluation."
        )

    return (
        int(
            row[0]
        ),
        str(
            row[1]
        ),
    )


def _save_actual_cc(
    cursor,
    *,
    reevaluation_id: int,
    cc_rows: list[dict[str, Any]],
) -> None:

    for cc in cc_rows:

        email_address = str(
            cc.get(
                "emailaddress"
            )
            or ""
        ).strip()

        if not email_address:
            continue

        cursor.execute(
            f"""
            INSERT INTO {VENDOR_REEVALUATION_CC_TABLE}
            (
                ReevaluationId,
                EmailAddress,
                DisplayName,
                IsActive,
                CreatedAt,
                CreatedBy
            )
            VALUES
            (
                ?,
                ?,
                ?,
                1,
                SYSDATETIME(),
                ?
            );
            """,
            reevaluation_id,
            email_address,
            cc.get(
                "displayname"
            ),
            SYSTEM_USER,
        )


def _add_action(
    cursor,
    *,
    reevaluation_id: int,
    action_type: str,
    from_status: str | None,
    to_status: str | None,
    remarks: str | None,
    email_triggered: int = 0,
    email_status: str | None = None,
    email_error: str | None = None,
) -> None:

    cursor.execute(
        f"""
        INSERT INTO {VENDOR_REEVALUATION_ACTION_TABLE}
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
            ?,
            ?,
            ?,
            ?,
            SYSDATETIME(),
            ?,
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
        from_status,
        to_status,
        remarks,
        SYSTEM_USER,
        email_triggered,
        email_status,
        email_status,
        email_error,
    )


def _update_mail_success(
    cursor,
    *,
    reevaluation_id: int,
) -> None:

    cursor.execute(
        f"""
        UPDATE {VENDOR_REEVALUATION_TABLE}
        SET
            Status = 'MAIL_SENT',
            MailStatus = 'SENT',
            MailSentAt = SYSDATETIME(),
            MailAttemptCount =
                ISNULL(
                    MailAttemptCount,
                    0
                ) + 1,
            MailLastError = NULL,
            ModifiedAt = SYSDATETIME(),
            ModifiedBy = ?
        WHERE
            ReevaluationId = ?;
        """,
        SYSTEM_USER,
        reevaluation_id,
    )


def _update_mail_failure(
    cursor,
    *,
    reevaluation_id: int,
    error_message: str,
) -> None:

    cursor.execute(
        f"""
        UPDATE {VENDOR_REEVALUATION_TABLE}
        SET
            MailStatus = 'FAILED',
            MailAttemptCount =
                ISNULL(
                    MailAttemptCount,
                    0
                ) + 1,
            MailLastError = ?,
            ModifiedAt = SYSDATETIME(),
            ModifiedBy = ?
        WHERE
            ReevaluationId = ?;
        """,
        error_message[:2000],
        SYSTEM_USER,
        reevaluation_id,
    )


def _create_and_send_auto_reevaluation(
    cursor,
    *,
    vendor: dict[str, Any],
    trigger_reason: str,
    cc_rows: list[dict[str, Any]],
    cc_emails: list[str],
    risk_level: str | None = None,
    expired_date: date | None = None,
) -> tuple[int, str]:

    vendor_account = (
        vendor.get(
            "vendoraccount"
        )
        or ""
    )

    previous = (
        _get_previous_reevaluation(
            cursor,
            vendor_account,
        )
    )

    if previous:

        cycle = (
            int(
                previous.get(
                    "reevaluationcycle"
                )
                or 0
            )
            + 1
        )

        previous_id = int(
            previous[
                "reevaluationid"
            ]
        )

    else:

        cycle = 1
        previous_id = None

    (
        reevaluation_id,
        reevaluation_no,
    ) = _create_auto_reevaluation(
        cursor,
        vendor=vendor,
        cycle=cycle,
        previous_reevaluation_id=
            previous_id,
        trigger_reason=
            trigger_reason,
    )

    _save_actual_cc(
        cursor,
        reevaluation_id=
            reevaluation_id,
        cc_rows=
            cc_rows,
    )

    _add_action(
        cursor,
        reevaluation_id=
            reevaluation_id,
        action_type=
            "AUTO_REEVALUATION_TRIGGERED",
        from_status=
            None,
        to_status=
            "PENDING",
        remarks=
            (
                "Automatic reevaluation triggered: "
                f"{trigger_reason}."
            ),
    )

    # Save reevaluation before attempting email.
    cursor.connection.commit()

    (
        subject,
        html_body,
    ) = _build_auto_reevaluation_email(
        vendor_name=
            vendor.get(
                "vendorname"
            )
            or "",
        reevaluation_id=
            reevaluation_id,
        trigger_reason=
            trigger_reason,
        risk_level=
            risk_level,
        expired_date=
            expired_date,
    )

    to_email = str(
        vendor.get(
            "toemail"
        )
        or ""
    ).strip()

    if not to_email:
        raise ValueError(
            "Vendor email not found."
        )

    try:

        _send_html_email(
            to_email=
                to_email,
            cc_emails=
                cc_emails,
            subject=
                subject,
            html_body=
                html_body,
        )

        _update_mail_success(
            cursor,
            reevaluation_id=
                reevaluation_id,
        )

        _add_action(
            cursor,
            reevaluation_id=
                reevaluation_id,
            action_type=
                "MAIL_SENT",
            from_status=
                "PENDING",
            to_status=
                "MAIL_SENT",
            remarks=
                (
                    "Automatic reevaluation email "
                    "sent successfully."
                ),
            email_triggered=
                1,
            email_status=
                "SENT",
        )

        cursor.connection.commit()

    except Exception as exc:

        error_message = str(
            exc
        )

        _update_mail_failure(
            cursor,
            reevaluation_id=
                reevaluation_id,
            error_message=
                error_message,
        )

        _add_action(
            cursor,
            reevaluation_id=
                reevaluation_id,
            action_type=
                "MAIL_FAILED",
            from_status=
                "PENDING",
            to_status=
                "PENDING",
            remarks=
                (
                    "Automatic reevaluation email failed."
                ),
            email_triggered=
                1,
            email_status=
                "FAILED",
            email_error=
                error_message[:2000],
        )

        cursor.connection.commit()
        raise

    return (
        reevaluation_id,
        reevaluation_no,
    )


# ============================================================
# 1) CERTIFICATE 90-DAY ONE-TIME REMINDER
#
# No reevaluation is created here.
# No form link is sent here.
# ============================================================

def _process_certificate_reminders(
    cursor,
    *,
    cc_emails: list[str],
) -> dict[str, int]:

    today = date.today()

    reminder_limit = (
        today
        + timedelta(
            days=
                CERTIFICATE_REMINDER_DAYS
        )
    )

    cursor.execute(
        f"""
        SELECT
            C.ProspectId,
            MIN(
                CAST(
                    C.ValidUntil
                    AS DATE
                )
            ) AS NextCertificateExpiry
        FROM {VENDOR_CERTIFICATION_TABLE} C
        WHERE
            C.ValidUntil IS NOT NULL
            AND CAST(
                C.ValidUntil
                AS DATE
            ) > ?
            AND CAST(
                C.ValidUntil
                AS DATE
            ) <= ?
        GROUP BY
            C.ProspectId;
        """,
        today,
        reminder_limit,
    )

    rows = _rows_to_dict(
        cursor
    )

    sent = 0
    skipped = 0
    failed = 0

    for row in rows:

        prospect_id = str(
            row.get(
                "prospectid"
            )
            or ""
        ).strip()

        expiry_date = row.get(
            "nextcertificateexpiry"
        )

        if not prospect_id or not expiry_date:
            continue

        if isinstance(
            expiry_date,
            datetime,
        ):
            expiry_date = (
                expiry_date.date()
            )

        reference_key = (
            f"{prospect_id}:"
            f"{expiry_date.isoformat()}"
        )

        if _event_already_processed(
            cursor,
            prospect_id=
                prospect_id,
            event_type=
                EVENT_CERTIFICATE_REMINDER,
            reference_key=
                reference_key,
        ):

            skipped += 1
            continue

        vendor = (
            _get_vendor_by_prospect(
                cursor,
                prospect_id,
            )
        )

        if not vendor:
            skipped += 1
            continue

        to_email = str(
            vendor.get(
                "toemail"
            )
            or ""
        ).strip()

        if not to_email:

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    vendor.get(
                        "vendoraccount"
                    ),
                event_type=
                    EVENT_CERTIFICATE_REMINDER,
                reference_key=
                    reference_key,
                reference_date=
                    expiry_date,
                reevaluation_id=
                    None,
                status=
                    "FAILED",
                to_email=
                    None,
                error_message=
                    "Vendor email not found.",
            )

            cursor.connection.commit()

            failed += 1
            continue

        (
            subject,
            html_body,
        ) = _build_certificate_reminder_email(
            vendor_name=
                vendor.get(
                    "vendorname"
                )
                or "",
            expiry_date=
                expiry_date,
        )

        try:

            _send_html_email(
                to_email=
                    to_email,
                cc_emails=
                    cc_emails,
                subject=
                    subject,
                html_body=
                    html_body,
            )

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    vendor.get(
                        "vendoraccount"
                    ),
                event_type=
                    EVENT_CERTIFICATE_REMINDER,
                reference_key=
                    reference_key,
                reference_date=
                    expiry_date,
                reevaluation_id=
                    None,
                status=
                    "SENT",
                to_email=
                    to_email,
            )

            cursor.connection.commit()

            sent += 1

        except Exception as exc:

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    vendor.get(
                        "vendoraccount"
                    ),
                event_type=
                    EVENT_CERTIFICATE_REMINDER,
                reference_key=
                    reference_key,
                reference_date=
                    expiry_date,
                reevaluation_id=
                    None,
                status=
                    "FAILED",
                to_email=
                    to_email,
                error_message=
                    str(exc),
            )

            cursor.connection.commit()

            failed += 1

    return {
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }


# ============================================================
# 2) CERTIFICATE EXPIRED -> CREATE REEVALUATION + FORM LINK
# ============================================================

def _process_expired_certificates(
    cursor,
    *,
    cc_rows: list[dict[str, Any]],
    cc_emails: list[str],
) -> dict[str, int]:

    today = date.today()

    cursor.execute(
        f"""
        SELECT
            C.ProspectId,
            MAX(
                CAST(
                    C.ValidUntil
                    AS DATE
                )
            ) AS LatestExpiredDate
        FROM {VENDOR_CERTIFICATION_TABLE} C
        WHERE
            C.ValidUntil IS NOT NULL
            AND CAST(
                C.ValidUntil
                AS DATE
            ) < ?
        GROUP BY
            C.ProspectId;
        """,
        today,
    )

    rows = _rows_to_dict(
        cursor
    )

    triggered = 0
    skipped = 0
    failed = 0

    for row in rows:

        prospect_id = str(
            row.get(
                "prospectid"
            )
            or ""
        ).strip()

        expired_date = row.get(
            "latestexpireddate"
        )

        if not prospect_id or not expired_date:
            continue

        if isinstance(
            expired_date,
            datetime,
        ):
            expired_date = (
                expired_date.date()
            )

        reference_key = (
            f"{prospect_id}:"
            f"{expired_date.isoformat()}"
        )

        if _event_already_processed(
            cursor,
            prospect_id=
                prospect_id,
            event_type=
                EVENT_CERTIFICATE_EXPIRED,
            reference_key=
                reference_key,
        ):

            skipped += 1
            continue

        # Do not create a duplicate active reevaluation.
        if _has_active_reevaluation(
            cursor,
            prospect_id,
        ):

            skipped += 1
            continue

        vendor = (
            _get_vendor_by_prospect(
                cursor,
                prospect_id,
            )
        )

        if not vendor:
            skipped += 1
            continue

        try:

            (
                reevaluation_id,
                _,
            ) = _create_and_send_auto_reevaluation(
                cursor,
                vendor=
                    vendor,
                trigger_reason=
                    EVENT_CERTIFICATE_EXPIRED,
                cc_rows=
                    cc_rows,
                cc_emails=
                    cc_emails,
                expired_date=
                    expired_date,
            )

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    vendor.get(
                        "vendoraccount"
                    ),
                event_type=
                    EVENT_CERTIFICATE_EXPIRED,
                reference_key=
                    reference_key,
                reference_date=
                    expired_date,
                reevaluation_id=
                    reevaluation_id,
                status=
                    "SENT",
                to_email=
                    vendor.get(
                        "toemail"
                    ),
            )

            cursor.connection.commit()

            triggered += 1

        except Exception as exc:

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    (
                        vendor.get(
                            "vendoraccount"
                        )
                        if vendor
                        else None
                    ),
                event_type=
                    EVENT_CERTIFICATE_EXPIRED,
                reference_key=
                    reference_key,
                reference_date=
                    expired_date,
                reevaluation_id=
                    None,
                status=
                    "FAILED",
                to_email=
                    (
                        vendor.get(
                            "toemail"
                        )
                        if vendor
                        else None
                    ),
                error_message=
                    str(exc),
            )

            cursor.connection.commit()

            failed += 1

    return {
        "triggered": triggered,
        "skipped": skipped,
        "failed": failed,
    }


# ============================================================
# 3) RISK-BASED NEXT REEVALUATION DATE DUE
#
# The risk-level service is responsible for calculating:
#   RiskLevel
#   EvaluationPeriodMonths
#   NextReevaluationDate
#
# Example:
# HIGH -> 6 months
#
# Scheduler only checks:
#   NextReevaluationDate <= today
# ============================================================

def _process_risk_due_reevaluations(
    cursor,
    *,
    cc_rows: list[dict[str, Any]],
    cc_emails: list[str],
) -> dict[str, int]:

    today = date.today()

    cursor.execute(
        f"""
        ;WITH LatestCompleted AS
        (
            SELECT
                R.ReevaluationId,
                R.ProspectId,
                R.VendorAccount,
                R.RiskLevel,
                R.NextReevaluationDate,

                ROW_NUMBER() OVER
                (
                    PARTITION BY R.ProspectId
                    ORDER BY
                        R.ReevaluationId DESC
                ) AS rn

            FROM {VENDOR_REEVALUATION_TABLE} R

            WHERE
                R.IsActive = 1
                AND UPPER(R.Status) =
                    'COMPLETED'
                AND R.NextReevaluationDate
                    IS NOT NULL
        )

        SELECT
            ReevaluationId,
            ProspectId,
            VendorAccount,
            RiskLevel,
            CAST(
                NextReevaluationDate
                AS DATE
            ) AS NextReevaluationDate

        FROM LatestCompleted

        WHERE
            rn = 1

            AND CAST(
                NextReevaluationDate
                AS DATE
            ) <= ?;
        """,
        today,
    )

    rows = _rows_to_dict(
        cursor
    )

    triggered = 0
    skipped = 0
    failed = 0

    for row in rows:

        prospect_id = str(
            row.get(
                "prospectid"
            )
            or ""
        ).strip()

        due_date = row.get(
            "nextreevaluationdate"
        )

        risk_level = (
            row.get(
                "risklevel"
            )
        )

        if not prospect_id or not due_date:
            continue

        if isinstance(
            due_date,
            datetime,
        ):
            due_date = (
                due_date.date()
            )

        reference_key = (
            f"{prospect_id}:"
            f"{due_date.isoformat()}"
        )

        if _event_already_processed(
            cursor,
            prospect_id=
                prospect_id,
            event_type=
                EVENT_RISK_DUE,
            reference_key=
                reference_key,
        ):

            skipped += 1
            continue

        if _has_active_reevaluation(
            cursor,
            prospect_id,
        ):

            skipped += 1
            continue

        vendor = (
            _get_vendor_by_prospect(
                cursor,
                prospect_id,
            )
        )

        if not vendor:
            skipped += 1
            continue

        try:

            (
                reevaluation_id,
                _,
            ) = _create_and_send_auto_reevaluation(
                cursor,
                vendor=
                    vendor,
                trigger_reason=
                    EVENT_RISK_DUE,
                cc_rows=
                    cc_rows,
                cc_emails=
                    cc_emails,
                risk_level=
                    risk_level,
            )

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    vendor.get(
                        "vendoraccount"
                    ),
                event_type=
                    EVENT_RISK_DUE,
                reference_key=
                    reference_key,
                reference_date=
                    due_date,
                reevaluation_id=
                    reevaluation_id,
                status=
                    "SENT",
                to_email=
                    vendor.get(
                        "toemail"
                    ),
            )

            cursor.connection.commit()

            triggered += 1

        except Exception as exc:

            _save_scheduler_log(
                cursor,
                prospect_id=
                    prospect_id,
                vendor_account=
                    (
                        vendor.get(
                            "vendoraccount"
                        )
                        if vendor
                        else None
                    ),
                event_type=
                    EVENT_RISK_DUE,
                reference_key=
                    reference_key,
                reference_date=
                    due_date,
                reevaluation_id=
                    None,
                status=
                    "FAILED",
                to_email=
                    (
                        vendor.get(
                            "toemail"
                        )
                        if vendor
                        else None
                    ),
                error_message=
                    str(exc),
            )

            cursor.connection.commit()

            failed += 1

    return {
        "triggered": triggered,
        "skipped": skipped,
        "failed": failed,
    }


# ============================================================
# MAIN DAILY SCHEDULER WORK
# ============================================================

def run_reevaluation_scheduler_once() -> dict[str, Any]:

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            cc_rows = (
                _get_default_cc(
                    cursor
                )
            )

            cc_emails = (
                _get_default_cc_emails(
                    cc_rows
                )
            )

            certificate_reminders = (
                _process_certificate_reminders(
                    cursor,
                    cc_emails=
                        cc_emails,
                )
            )

            expired_certificates = (
                _process_expired_certificates(
                    cursor,
                    cc_rows=
                        cc_rows,
                    cc_emails=
                        cc_emails,
                )
            )

            risk_due = (
                _process_risk_due_reevaluations(
                    cursor,
                    cc_rows=
                        cc_rows,
                    cc_emails=
                        cc_emails,
                )
            )

            return {
                "status": True,
                "message":
                    (
                        "Automatic reevaluation "
                        "scheduler completed."
                    ),
                "data": {
                    "certificate_90_day_reminder":
                        certificate_reminders,

                    "certificate_expired":
                        expired_certificates,

                    "risk_due":
                        risk_due,
                },
            }

        finally:

            cursor.close()
