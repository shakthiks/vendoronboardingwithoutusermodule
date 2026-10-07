# app/services/reevaluation_service.py

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import get_connection
from app.services.email_service import send_email

# ============================================================
# SCHEMA
# ============================================================

SCHEMA = settings.DB_SCHEMA


# ============================================================
# TABLES
# ============================================================

VENDOR_PROSPECT_TABLE = (
    f"{SCHEMA}.d365_VendorProspect"
)

VENDOR_REGISTRATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorRegistration"
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

VENDOR_REEVALUATION_TEMPLATE_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluationEmailTemplate"
)


# ============================================================
# CURSOR ROW → DICTIONARY
# ============================================================

def _row_to_dict(
    cursor,
    row,
) -> dict[str, Any]:

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return dict(
        zip(columns, row)
    )


# ============================================================
# EMAIL PLACEHOLDER REPLACEMENT
# ============================================================
def build_vendor_email(
    subject_template: str,
    body_template: str,
    vendor_name: str,
    reevaluation_id: int,
    reevaluation_no: str,
    vendor_account: str,
) -> tuple[str, str]:

    subject = subject_template or ""
    body = body_template or ""

    # ========================================================
    # FRONTEND URL FROM ENV
    # ========================================================

    frontend_url = (
        settings.ONBOARDING_FRONTEND_URL
        .strip()
        .rstrip("/")
    )

    # Adjust this frontend route if your React route differs
    reevaluation_link = (
        f"{frontend_url}"
        f"/vendor-reevaluation"
        f"?reevaluationId={reevaluation_id}"
    )

    # ========================================================
    # TEMPLATE PLACEHOLDERS
    # ========================================================

    replacements = {
        "[Vendor Name]":
            vendor_name or "",

        "[Reevaluation No]":
            reevaluation_no or "",

        "[Vendor Account]":
            vendor_account or "",

        "[Document Upload Link]":
            reevaluation_link,

        "[Company Name]":
            "Hi-Q Electronics",
    }

    for placeholder, value in replacements.items():

        subject = subject.replace(
            placeholder,
            str(value),
        )

        body = body.replace(
            placeholder,
            str(value),
        )

    return subject, body


# ============================================================
# EMAIL SENDER
# ============================================================
#
# IMPORTANT:
# Replace only this function with your current email service.
#
# Example:
#
# from app.services.mail_service import send_email
#
# def send_reevaluation_email(...):
#     send_email(
#         to_email=to_email,
#         cc_emails=cc_emails,
#         subject=subject,
#         body=body
#     )
#
# ============================================================

def send_reevaluation_email(
    to_email: str,
    cc_emails: list[str],
    subject: str,
    body: str,
) -> None:

    result = send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        cc_emails=cc_emails,
    )

    if result is not True:
        raise Exception(
            "Email service failed to send reevaluation email."
        )
# ============================================================
# GET ACTIVE COMMON CC RECIPIENTS
# ============================================================

def _get_active_cc(
    cursor,
) -> list[dict[str, Any]]:

    cursor.execute(
        f"""
        SELECT
            CCMasterId,
            DisplayName,
            EmailAddress
        FROM {VENDOR_REEVALUATION_CC_MASTER_TABLE}
        WHERE IsActive = 1
        ORDER BY CCMasterId;
        """
    )

    rows = cursor.fetchall()

    if not rows:
        return []

    columns = [
        column[0].lower()
        for column in cursor.description
    ]

    return [
        dict(
            zip(columns, row)
        )
        for row in rows
    ]


# ============================================================
# GET ACTIVE DEFAULT EMAIL TEMPLATE
# ============================================================

def _get_active_email_template(
    cursor,
) -> dict[str, Any] | None:

    cursor.execute(
        f"""
        SELECT TOP 1
            TemplateId,
            TemplateName,
            Subject,
            EmailBody
        FROM {VENDOR_REEVALUATION_TEMPLATE_TABLE}
        WHERE IsActive = 1
        ORDER BY TemplateId DESC;
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
# GET VENDOR MASTER DATA
# ============================================================

def _get_vendor(
    cursor,
    vendor_account: str,
) -> dict[str, Any] | None:

    cursor.execute(
        f"""
        SELECT TOP 1

            VP.ProspectSeq,

            VP.ProspectId,

            VP.VendorAccount,

            VP.Name AS VendorName,

            COALESCE
            (
                NULLIF(
                    LTRIM(RTRIM(VR.RepEmail)),
                    ''
                ),

                NULLIF(
                    LTRIM(RTRIM(VP.Email)),
                    ''
                )
            ) AS ToEmail

        FROM {VENDOR_PROSPECT_TABLE} VP

        LEFT JOIN {VENDOR_REGISTRATION_TABLE} VR
            ON VR.ProspectId = VP.ProspectId

        WHERE
            LTRIM(RTRIM(VP.VendorAccount))
            =
            LTRIM(RTRIM(?));
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
# GET PREVIOUS / LATEST REEVALUATION
# ============================================================

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
# CREATE MAIN REEVALUATION
# ============================================================

def _create_reevaluation(
    cursor,
    vendor: dict[str, Any],
    cycle: int,
    previous_reevaluation_id: int | None,
    trigger_reason: str | None,
    triggered_by: str,
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

            'MANUAL',
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

        vendor["prospectseq"],
        vendor["prospectid"],
        vendor["vendoraccount"],

        cycle,
        previous_reevaluation_id,

        trigger_reason,

        vendor["toemail"],

        triggered_by,

        triggered_by,
    )

    row = cursor.fetchone()

    if row is None:

        raise Exception(
            "Failed to create vendor reevaluation."
        )

    return (
        int(row[0]),
        str(row[1]),
    )


# ============================================================
# SAVE ACTUAL CC USED
# ============================================================

def _save_actual_cc(
    cursor,
    reevaluation_id: int,
    cc_list: list[dict[str, Any]],
    created_by: str,
) -> None:

    for cc in cc_list:

        email_address = (
            str(
                cc.get(
                    "emailaddress"
                )
                or ""
            )
            .strip()
        )

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

            created_by,
        )


# ============================================================
# ADD ACTION HISTORY
# ============================================================

def _add_action(
    cursor,
    reevaluation_id: int,
    action_type: str,
    from_status: str | None,
    to_status: str | None,
    remarks: str | None,
    action_by: str,
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

        action_by,

        email_triggered,

        email_status,

        email_status,

        email_error,
    )


# ============================================================
# UPDATE MAIL SUCCESS
# ============================================================

def _update_mail_success(
    cursor,
    reevaluation_id: int,
    modified_by: str,
) -> None:

    cursor.execute(
        f"""
        UPDATE {VENDOR_REEVALUATION_TABLE}

        SET
            MailStatus = 'SENT',

            MailSentAt =
                SYSDATETIME(),

            MailAttemptCount =
                MailAttemptCount + 1,

            MailError =
                NULL,

            Status =
                'MAIL_SENT',

            ModifiedAt =
                SYSDATETIME(),

            ModifiedBy =
                ?

        WHERE
            ReevaluationId = ?;
        """,

        modified_by,

        reevaluation_id,
    )


# ============================================================
# UPDATE MAIL FAILURE
# ============================================================

def _update_mail_failure(
    cursor,
    reevaluation_id: int,
    error_message: str,
    modified_by: str,
) -> None:

    cursor.execute(
        f"""
        UPDATE {VENDOR_REEVALUATION_TABLE}

        SET
            MailStatus = 'FAILED',

            MailAttemptCount =
                MailAttemptCount + 1,

            MailError =
                ?,

            ModifiedAt =
                SYSDATETIME(),

            ModifiedBy =
                ?

        WHERE
            ReevaluationId = ?;
        """,

        error_message[:2000],

        modified_by,

        reevaluation_id,
    )


# ============================================================
# MAIN SYNC FUNCTION
# ============================================================

def initiate_vendor_reevaluation_sync(
    payload,
):

    # ========================================================
    # CLEAN SELECTED VENDORS
    # ========================================================

    vendor_accounts = list(
        dict.fromkeys(
            account.strip()
            for account
            in payload.vendor_accounts

            if account
            and account.strip()
        )
    )

    if not vendor_accounts:

        return {
            "status": False,

            "message":
                "At least one vendor account is required.",

            "data": None,
        }


    results = []

    successful = 0

    failed = 0


    # ========================================================
    # OPEN DB
    # ========================================================

    with get_connection() as conn:

        cursor = conn.cursor()

        try:

            # =================================================
            # ACTIVE COMMON CC
            # =================================================

            cc_list = _get_active_cc(
                cursor
            )


            cc_emails = [

                str(
                    cc.get(
                        "emailaddress"
                    )
                    or ""
                ).strip()

                for cc in cc_list

                if cc.get(
                    "emailaddress"
                )
            ]


            # =================================================
            # EMAIL MODE
            #
            # DEFAULT_TEMPLATE
            # OR
            # BATCH_OVERRIDE
            # =================================================

            use_batch_override = False

            email_source = (
                "DEFAULT_TEMPLATE"
            )

            subject_template = ""

            body_template = ""


            email_override = getattr(
                payload,
                "email_override",
                None,
            )


            if email_override:

                use_batch_override = bool(
                    email_override.use_override
                )


            # =================================================
            # BATCH OVERRIDE
            # =================================================

            if use_batch_override:

                subject_template = (
                    email_override.subject
                    or ""
                ).strip()


                body_template = (
                    email_override.body
                    or ""
                ).strip()


                if not subject_template:

                    return {
                        "status": False,

                        "message":
                            "Subject is required when "
                            "batch email override is enabled.",

                        "data": None,
                    }


                if not body_template:

                    return {
                        "status": False,

                        "message":
                            "Email body is required when "
                            "batch email override is enabled.",

                        "data": None,
                    }


                email_source = (
                    "BATCH_OVERRIDE"
                )


            # =================================================
            # DEFAULT SETTINGS TEMPLATE
            # =================================================

            else:

                template = (
                    _get_active_email_template(
                        cursor
                    )
                )


                if template is None:

                    return {
                        "status": False,

                        "message":
                            "Active reevaluation "
                            "email template not found.",

                        "data": None,
                    }


                subject_template = (
                    template.get(
                        "subject"
                    )
                    or ""
                )


                body_template = (
                    template.get(
                        "emailbody"
                    )
                    or ""
                )


            # =================================================
            # PROCESS EACH VENDOR
            # =================================================

            for vendor_account in vendor_accounts:

                reevaluation_id = None

                reevaluation_no = None

                cycle = None


                try:

                    # ==========================================
                    # FETCH CURRENT VENDOR DETAILS
                    # ==========================================

                    vendor = _get_vendor(
                        cursor,
                        vendor_account,
                    )


                    if vendor is None:

                        failed += 1


                        results.append(
                            {
                                "vendor_account":
                                    vendor_account,

                                "status":
                                    "FAILED",

                                "message":
                                    "Vendor not found.",
                            }
                        )


                        continue


                    # ==========================================
                    # VALIDATE TO EMAIL
                    # ==========================================

                    to_email = (
                        str(
                            vendor.get(
                                "toemail"
                            )
                            or ""
                        )
                        .strip()
                    )


                    if not to_email:

                        failed += 1


                        results.append(
                            {
                                "vendor_account":
                                    vendor_account,

                                "prospect_id":
                                    vendor.get(
                                        "prospectid"
                                    ),

                                "status":
                                    "FAILED",

                                "message":
                                    "Vendor email address "
                                    "not found.",
                            }
                        )


                        continue


                    # ==========================================
                    # GET PREVIOUS REEVALUATION
                    # ==========================================

                    previous = (
                        _get_previous_reevaluation(
                            cursor,
                            vendor_account,
                        )
                    )


                    # ==========================================
                    # PREVENT SECOND ACTIVE REEVALUATION
                    # ==========================================

                    if previous:

                        previous_status = (
                            str(
                                previous.get(
                                    "status"
                                )
                                or ""
                            )
                            .strip()
                            .upper()
                        )


                        if previous_status not in [
                            "COMPLETED",
                            "CANCELLED",
                        ]:

                            failed += 1


                            results.append(
                                {
                                    "vendor_account":
                                        vendor_account,

                                    "status":
                                        "FAILED",

                                    "message":
                                        (
                                            "Vendor already has "
                                            "an active reevaluation."
                                        ),
                                }
                            )


                            continue


                    # ==========================================
                    # CALCULATE CYCLE
                    # ==========================================

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


                    # ==========================================
                    # CREATE REEVALUATION
                    # ==========================================

                    (
                        reevaluation_id,
                        reevaluation_no,
                    ) = _create_reevaluation(

                        cursor=cursor,

                        vendor=vendor,

                        cycle=cycle,

                        previous_reevaluation_id=
                            previous_id,

                        trigger_reason=
                            payload.trigger_reason,

                        triggered_by=
                            payload.triggered_by,
                    )


                    # ==========================================
                    # SAVE ACTUAL CC USED
                    # ==========================================

                    _save_actual_cc(

                        cursor=cursor,

                        reevaluation_id=
                            reevaluation_id,

                        cc_list=
                            cc_list,

                        created_by=
                            payload.triggered_by,
                    )


                    # ==========================================
                    # ACTION:
                    # REEVALUATION_TRIGGERED
                    # ==========================================

                    _add_action(

                        cursor=cursor,

                        reevaluation_id=
                            reevaluation_id,

                        action_type=
                            "REEVALUATION_TRIGGERED",

                        from_status=None,

                        to_status=
                            "PENDING",

                        remarks=
                            "Vendor reevaluation initiated.",

                        action_by=
                            payload.triggered_by,

                        email_triggered=
                            0,
                    )


                    # =================================================
                    # COMMIT RECORD BEFORE MAIL
                    #
                    # Reevaluation remains available even if email fails.
                    # =================================================

                    conn.commit()


                    # ==========================================
                    # BUILD FINAL EMAIL
                    # ==========================================
                    subject, body = build_vendor_email(
                        subject_template=subject_template,
                        body_template=body_template,
                        vendor_name=vendor.get("vendorname") or "",
                        reevaluation_id=reevaluation_id,
                        reevaluation_no=reevaluation_no,
                        vendor_account=vendor_account,
                    )
            

                    # ==========================================
                    # SEND MAIL
                    # ==========================================

                    try:

                        send_reevaluation_email(

                            to_email=
                                to_email,

                            cc_emails=
                                cc_emails,

                            subject=
                                subject,

                            body=
                                body,
                        )


                        # ======================================
                        # MAIL SUCCESS
                        # ======================================

                        _update_mail_success(

                            cursor=cursor,

                            reevaluation_id=
                                reevaluation_id,

                            modified_by=
                                payload.triggered_by,
                        )


                        _add_action(

                            cursor=cursor,

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
                                    "Vendor reevaluation email "
                                    f"sent using {email_source}."
                                ),

                            action_by=
                                payload.triggered_by,

                            email_triggered=
                                1,

                            email_status=
                                "SENT",
                        )


                        conn.commit()


                        successful += 1


                        results.append(
                            {
                                "vendor_account":
                                    vendor_account,

                                "prospect_id":
                                    vendor.get(
                                        "prospectid"
                                    ),

                                "vendor_name":
                                    vendor.get(
                                        "vendorname"
                                    ),

                                "reevaluation_id":
                                    reevaluation_id,

                                "reevaluation_no":
                                    reevaluation_no,

                                "reevaluation_cycle":
                                    cycle,

                                "mail_status":
                                    "SENT",

                                "email_source":
                                    email_source,

                                "message":
                                    (
                                        "Reevaluation initiated "
                                        "successfully."
                                    ),
                            }
                        )


                    # ==========================================
                    # MAIL FAILED
                    # ==========================================

                    except Exception as mail_error:

                        error_message = str(
                            mail_error
                        )


                        _update_mail_failure(

                            cursor=cursor,

                            reevaluation_id=
                                reevaluation_id,

                            error_message=
                                error_message,

                            modified_by=
                                payload.triggered_by,
                        )


                        _add_action(

                            cursor=cursor,

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
                                    "Vendor reevaluation email "
                                    f"failed using {email_source}."
                                ),

                            action_by=
                                payload.triggered_by,

                            email_triggered=
                                1,

                            email_status=
                                "FAILED",

                            email_error=
                                error_message[:2000],
                        )


                        conn.commit()


                        failed += 1


                        results.append(
                            {
                                "vendor_account":
                                    vendor_account,

                                "prospect_id":
                                    vendor.get(
                                        "prospectid"
                                    ),

                                "vendor_name":
                                    vendor.get(
                                        "vendorname"
                                    ),

                                "reevaluation_id":
                                    reevaluation_id,

                                "reevaluation_no":
                                    reevaluation_no,

                                "reevaluation_cycle":
                                    cycle,

                                "mail_status":
                                    "FAILED",

                                "email_source":
                                    email_source,

                                "message":
                                    (
                                        "Reevaluation created "
                                        "but email sending failed."
                                    ),

                                "error":
                                    error_message,
                            }
                        )


                # ==============================================
                # VENDOR PROCESS FAILED
                # ==============================================

                except Exception as vendor_error:

                    try:

                        conn.rollback()

                    except Exception:

                        pass


                    failed += 1


                    results.append(
                        {
                            "vendor_account":
                                vendor_account,

                            "reevaluation_id":
                                reevaluation_id,

                            "reevaluation_no":
                                reevaluation_no,

                            "reevaluation_cycle":
                                cycle,

                            "status":
                                "FAILED",

                            "message":
                                str(
                                    vendor_error
                                ),
                        }
                    )


        finally:

            cursor.close()


    # ========================================================
    # FINAL RESPONSE MESSAGE
    # ========================================================

    if successful > 0 and failed == 0:

        message = (
            "All vendor reevaluations "
            "initiated successfully."
        )


    elif successful > 0 and failed > 0:

        message = (
            "Vendor reevaluation initiation "
            "completed with partial failures."
        )


    else:

        message = (
            "Vendor reevaluation initiation failed."
        )


    # ========================================================
    # FINAL RESPONSE
    # ========================================================

    return {

        "status":
            successful > 0,

        "message":
            message,

        "data": {

            "total_requested":
                len(vendor_accounts),

            "successful":
                successful,

            "failed":
                failed,

            "email_source":
                email_source,

            "vendors":
                results,
        },
    }


# ============================================================
# ASYNC API WRAPPER
# ============================================================

async def initiate_vendor_reevaluation(
    payload,
):

    return await run_in_threadpool(
        initiate_vendor_reevaluation_sync,
        payload,
    )