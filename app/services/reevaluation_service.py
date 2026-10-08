from datetime import date, timedelta

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

from app.db.base import (
    get_connection,
    rows_to_dict
)


# ============================================================
# DATABASE SCHEMA
# ============================================================

SCHEMA = settings.DB_SCHEMA


# ============================================================
# TABLE NAMES
# ============================================================

VENDOR_REEVALUATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorReevaluation"
)

VENDOR_CERTIFICATION_TABLE = (
    f"{SCHEMA}.HIQ_VendorCertification"
)

VENDOR_PROSPECT_TABLE = (
    f"{SCHEMA}.d365_VendorProspect"
)


# ============================================================
# SYNC FUNCTION
# Actual DB operation happens here
# ============================================================

def get_vendor_reevaluation_home_sync():

    try:

        query = f"""
        ;WITH LatestReevaluation AS
        (
            SELECT
                R.ReevaluationId,
                R.ReevaluationNo,
                R.ProspectSeq,
                R.ProspectId,
                R.VendorAccount,
                R.ReevaluationCycle,
                R.Status,
                R.RiskLevel,
                R.CompletedAt,
                R.NextReevaluationDate,
                R.DueDate,
                R.ReminderDate,
                R.SubmittedAt,
                R.FormOpenedAt,
                R.IsActive,

                ROW_NUMBER() OVER
                (
                    PARTITION BY R.VendorAccount
                    ORDER BY R.ReevaluationId DESC
                ) AS rn

            FROM {VENDOR_REEVALUATION_TABLE} R

            WHERE R.IsActive = 1
        )

        SELECT

            -- ==========================================
            -- VENDOR BASIC DETAILS
            -- ==========================================

            VP.VendorAccount,

            VP.ProspectId,

            VP.Name AS VendorName,


            -- ==========================================
            -- REEVALUATION DETAILS
            -- ==========================================

            LR.ReevaluationId,

            LR.ReevaluationNo,

            LR.ReevaluationCycle,

            --LR.RiskLevel,
            CASE
                WHEN UPPER(LR.RiskLevel) = 'CRITICAL'
                    THEN 'Critical'

                WHEN UPPER(LR.RiskLevel) = 'HIGH'
                    THEN 'High'

                WHEN UPPER(LR.RiskLevel) = 'ELEVATED'
                    THEN 'Elevated'

                WHEN UPPER(LR.RiskLevel) = 'MEDIUM'
                    THEN 'Medium'

                WHEN UPPER(LR.RiskLevel) = 'LOW'
                    THEN 'Low'

                ELSE LR.RiskLevel
            END AS RiskLevel,

            CAST(
                LR.CompletedAt AS DATE
            ) AS LastReevaluation,

            LR.NextReevaluationDate,

            LR.DueDate,

            LR.ReminderDate,

            LR.SubmittedAt,

            LR.FormOpenedAt,

            LR.Status AS RawStatus,


            -- ==========================================
            -- UI STATUS
            -- ==========================================

            CASE

                -- No reevaluation record
                WHEN LR.ReevaluationId IS NULL
                    THEN 'Not Started'


                -- Mail sent / Vendor filling form
                WHEN UPPER(LR.Status) IN
                (
                    'PENDING',
                    'MAIL_SENT',
                    'IN_PROGRESS'
                )
                    THEN 'Awaiting Response'


                -- Vendor submitted and internal review happening
                WHEN UPPER(LR.Status) IN
                (
                    'SUBMITTED',
                    'UNDER_REVIEW',
                    'RESUBMITTED'
                )
                    THEN 'Validation In Progress'


                -- Returned to vendor
                WHEN UPPER(LR.Status) IN
                (
                    'RETURNED',
                    'RESUBMISSION_REQUESTED'
                )
                    THEN 'Returned'


                -- Finished
                WHEN UPPER(LR.Status) = 'COMPLETED'
                    THEN 'Completed'


                -- Cancelled
                WHEN UPPER(LR.Status) = 'CANCELLED'
                    THEN 'Cancelled'


                ELSE LR.Status

            END AS Status,


            -- ==========================================
            -- DOCUMENT STATUS
            -- ==========================================

            CASE

                -- At least one certification expired
                WHEN EXISTS
                (
                    SELECT 1

                    FROM {VENDOR_CERTIFICATION_TABLE} C

                    WHERE
                        C.ProspectId = VP.ProspectId

                        AND C.ValidUntil <
                            CAST(GETDATE() AS DATE)
                )
                    THEN 'Expired'


                -- At least one certification expires
                -- within next 30 days
                WHEN EXISTS
                (
                    SELECT 1

                    FROM {VENDOR_CERTIFICATION_TABLE} C

                    WHERE
                        C.ProspectId = VP.ProspectId

                        AND C.ValidUntil >=
                            CAST(GETDATE() AS DATE)

                        AND C.ValidUntil <=
                            DATEADD(
                                DAY,
                                30,
                                CAST(GETDATE() AS DATE)
                            )
                )
                    THEN 'Expiring Soon'


                -- Certification exists and is valid
                -- for more than 30 days
                WHEN EXISTS
                (
                    SELECT 1

                    FROM {VENDOR_CERTIFICATION_TABLE} C

                    WHERE
                        C.ProspectId = VP.ProspectId

                        AND C.ValidUntil >
                            DATEADD(
                                DAY,
                                30,
                                CAST(GETDATE() AS DATE)
                            )
                )
                    THEN 'Valid'


                -- No certification found
                ELSE '-'

            END AS DocumentStatus


        -- ==============================================
        -- ALL EXISTING VENDORS
        -- ==============================================

        FROM {VENDOR_PROSPECT_TABLE} VP


        -- ==============================================
        -- ATTACH ONLY LATEST REEVALUATION
        -- ==============================================

        LEFT JOIN LatestReevaluation LR

            ON LR.VendorAccount = VP.VendorAccount

            AND LR.rn = 1


        -- ==============================================
        -- ONLY APPROVED/CREATED D365 VENDORS
        -- ==============================================

        WHERE
            VP.VendorAccount IS NOT NULL


        ORDER BY
            VP.VendorAccount;
        """


        # ========================================================
        # DATABASE CONNECTION
        # ========================================================

        with get_connection() as conn:

            cursor = conn.cursor()

            try:

                cursor.execute(query)

                rows = rows_to_dict(cursor)

            finally:

                cursor.close()


        # ========================================================
        # RESPONSE COLLECTIONS
        # ========================================================

        all_vendors = []

        in_review_vendors = []

        returned_vendors = []

        due_soon_vendors = []

        document_issue_vendors = []


        # ========================================================
        # DATE RANGE FOR DUE SOON
        # ========================================================

        today = date.today()

        due_soon_limit = (
            today + timedelta(days=30)
        )


        # ========================================================
        # CLASSIFY EACH VENDOR
        # ========================================================

        for vendor in rows:

            # ------------------------------------------
            # ALL VENDORS
            # ------------------------------------------

            all_vendors.append(vendor)


            # rows_to_dict() changes all column names
            # into lowercase.
            raw_status = (
                vendor.get("rawstatus")
                or ""
            ).upper()


            document_status = (
                vendor.get("documentstatus")
                or ""
            ).upper()


            next_reevaluation_date = (
                vendor.get(
                    "nextreevaluationdate"
                )
            )


            # ==========================================
            # IN REVIEW
            # ==========================================

            if raw_status in [

                "PENDING",

                "MAIL_SENT",

                "IN_PROGRESS",

                "SUBMITTED",

                "UNDER_REVIEW",

                "RESUBMITTED"

            ]:

                in_review_vendors.append(
                    vendor
                )


            # ==========================================
            # RETURNED
            # ==========================================

            if raw_status in [

                "RETURNED",

                "RESUBMISSION_REQUESTED"

            ]:

                returned_vendors.append(
                    vendor
                )


            # ==========================================
            # DUE SOON
            # ==========================================

            if next_reevaluation_date:

                if (
                    today
                    <= next_reevaluation_date
                    <= due_soon_limit
                ):

                    due_soon_vendors.append(
                        vendor
                    )


            # ==========================================
            # DOCUMENT ISSUES
            # ==========================================

            if document_status in [

                "EXPIRED",

                "EXPIRING SOON"

            ]:

                document_issue_vendors.append(
                    vendor
                )


        # ========================================================
        # FINAL RESPONSE
        # ========================================================

        return {

            "status": True,

            "message":
                "Vendor reevaluation data fetched successfully.",

            "data": {

                # ======================================
                # DASHBOARD CARDS
                # ======================================

                "summary": {

                    "total_vendors":
                        len(all_vendors),

                    "in_review":
                        len(in_review_vendors),

                    "returned":
                        len(returned_vendors),

                    "due_soon":
                        len(due_soon_vendors),

                    "document_issues":
                        len(document_issue_vendors)
                },


                # ======================================
                # TABLE DATA
                # ======================================

                "all_vendors":
                    all_vendors,

                "in_review_vendors":
                    in_review_vendors,

                "returned_vendors":
                    returned_vendors,

                "due_soon_vendors":
                    due_soon_vendors,

                "document_issue_vendors":
                    document_issue_vendors
            }
        }


    except Exception as e:

        raise Exception(
            "[VENDOR REEVALUATION] "
            f"Failed to fetch reevaluation data: {str(e)}"
        )


# ============================================================
# ASYNC FUNCTION
# Router calls this function
# ============================================================

async def get_vendor_reevaluation_home():

    try:

        return await run_in_threadpool(
            get_vendor_reevaluation_home_sync
        )

    except Exception as e:

        return {

            "status": False,

            "message":
                "Failed to fetch vendor reevaluation data.",

            "error": str(e),

            "data": None
        }