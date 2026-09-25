import logging

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_d365_connection,
)


logger = logging.getLogger(__name__)

SCHEMA = settings.DB_SCHEMA

VENDOR_USER_TABLE = (
    f"{SCHEMA}.HIQ_VENDORPORTALUSER"
)


# ============================================================
# HELPERS
# ============================================================

def normalize(value) -> str:
    """
    Normalize values only for comparison.

    Example:
        " Test@Email.com " -> "TEST@EMAIL.COM"
        None -> ""
    """
    if value is None:
        return ""

    return str(value).strip().upper()


# ============================================================
# VENDOR DETAILS
# ============================================================

def get_vendor_details_sync() -> list[dict]:

    # ========================================================
    # STEP 1 - FETCH D365 VENDORS
    # ========================================================
    with get_d365_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                V.ACCOUNTNUM,

                P.NAME,

                V.ACCOUNTNUM + ' - ' + P.NAME
                    AS DISPLAY_NAME,

                MAX(
                    CASE
                        WHEN E.TYPE = 2
                         AND E.ISPRIMARY1 = 1
                        THEN E.LOCATOR
                    END
                ) AS EMAIL,

                MAX(
                    CASE
                        WHEN E.TYPE = 1
                         AND E.ISPRIMARY1 = 1
                        THEN E.LOCATOR
                    END
                ) AS PHONE

            FROM VENDTABLE V WITH (NOLOCK)

            INNER JOIN DIRPARTYTABLE P WITH (NOLOCK)
                ON P.RECID = V.PARTY

            LEFT JOIN HIQ_vendorELECTRONICADDRESSVIEW E
                WITH (NOLOCK)
                ON E.ACCOUNTNUM = V.ACCOUNTNUM
               AND E.DATAAREAID = 'hi-q'

            WHERE V.HIQ_VENDORCOLLABORATION = 1

            GROUP BY
                V.ACCOUNTNUM,
                P.NAME

            ORDER BY
                P.NAME
            """
        )

        columns = [
            column[0].lower()
            for column in cursor.description
        ]

        d365_vendors = [
            dict(zip(columns, row))
            for row in cursor.fetchall()
        ]

    logger.info(
        "[STEP 1] D365 vendors fetched: %s",
        len(d365_vendors),
    )

    # ========================================================
    # STEP 2 - FETCH EXISTING PORTAL USERS
    # ========================================================
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT
                VENDORACCOUNT,
                EMAILADDRESS
            FROM {VENDOR_USER_TABLE}
            WITH (NOLOCK)
            """
        )

        portal_rows = cursor.fetchall()

    logger.info(
        "[STEP 2] Portal users fetched: %s",
        len(portal_rows),
    )

    # Keep both normalized and original email values.
    portal_map = {
        normalize(row[0]): {
            "vendor_account": row[0],
            "email": row[1],
            "normalized_email": normalize(row[1]),
        }
        for row in portal_rows
        if normalize(row[0])
    }

    if not portal_map:
        logger.warning(
            "[STEP 2] Portal user table returned no records. "
            "All D365 vendors will be considered New."
        )

    # ========================================================
    # STEP 3 - BUILD RESPONSE WITH STATUS
    # ========================================================
    result = []

    status_counts = {
        "New": 0,
        "Email Changed": 0,
        "Already Invited": 0,
        "Email Missing": 0,
    }

    for vendor in d365_vendors:

        account = normalize(
            vendor.get("accountnum")
        )

        original_d365_email = (
            str(vendor.get("email")).strip()
            if vendor.get("email") is not None
            else None
        )

        d365_email = normalize(
            original_d365_email
        )

        portal_user = portal_map.get(account)

        portal_email = (
            portal_user["email"]
            if portal_user
            else None
        )

        normalized_portal_email = (
            portal_user["normalized_email"]
            if portal_user
            else ""
        )

        # ----------------------------------------------------
        # EMAIL MISSING IN D365
        # ----------------------------------------------------
        if not d365_email:
            status = "Email Missing"

            # Vendor may still have been invited previously.
            is_invited = portal_user is not None
            can_invite = False

        # ----------------------------------------------------
        # NEW VENDOR
        # ----------------------------------------------------
        elif portal_user is None:
            status = "New"
            is_invited = False
            can_invite = True

        # ----------------------------------------------------
        # EMAIL CHANGED
        # ----------------------------------------------------
        elif normalized_portal_email != d365_email:
            status = "Email Changed"
            is_invited = True
            can_invite = True

        # ----------------------------------------------------
        # ALREADY INVITED
        # ----------------------------------------------------
        else:
            status = "Already Invited"
            is_invited = True
            can_invite = False

        status_counts[status] += 1

        result.append(
            {
                "accountnum": vendor.get("accountnum"),
                "name": vendor.get("name"),
                "display_name": vendor.get("display_name"),
                "email": original_d365_email,
                "phone": vendor.get("phone"),

                # Existing email saved in portal table
                "portal_email": portal_email,

                # Frontend display/status fields
                "status": status,
                "is_invited": is_invited,
                "can_invite": can_invite,
            }
        )

    logger.info(
        "[STEP 3] Total vendors returned=%s | "
        "New=%s | Email Changed=%s | "
        "Already Invited=%s | Email Missing=%s",
        len(result),
        status_counts["New"],
        status_counts["Email Changed"],
        status_counts["Already Invited"],
        status_counts["Email Missing"],
    )

    return result


# ============================================================
# ASYNC WRAPPER
# ============================================================

async def get_vendor_details() -> list[dict]:
    return await run_in_threadpool(
        get_vendor_details_sync
    )


# from fastapi.concurrency import run_in_threadpool

# from app.db.base import (
#     get_connection,
#     get_d365_connection
# )

# from app.core.config import settings
# import logging

# logger = logging.getLogger(__name__)

# SCHEMA = settings.DB_SCHEMA

# VENDOR_USER_TABLE = (
#     f"{SCHEMA}.HIQ_VENDORPORTALUSER"
# )


# # ============================================================
# # HELPERS
# # ============================================================
# def normalize(val):
#     """Normalize to uppercase stripped string; None/empty → empty string."""
#     if val is None:
#         return ""
#     return str(val).strip().upper()


# # ============================================================
# # VENDOR DETAILS
# # ============================================================
# def get_vendor_details_sync():

#     # ========================================================
#     # STEP 1 - D365 VENDORS
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         cur.execute("""
#             SELECT
#                 V.ACCOUNTNUM,

#                 P.NAME,

#                 V.ACCOUNTNUM + ' - ' + P.NAME
#                     AS DISPLAY_NAME,

#                 MAX(
#                     CASE
#                         WHEN E.TYPE = 2
#                          AND E.ISPRIMARY1 = 1
#                         THEN E.LOCATOR
#                     END
#                 ) AS EMAIL,

#                 MAX(
#                     CASE
#                         WHEN E.TYPE = 1
#                          AND E.ISPRIMARY1 = 1
#                         THEN E.LOCATOR
#                     END
#                 ) AS PHONE

#             FROM VENDTABLE V

#             JOIN DIRPARTYTABLE P
#                 ON P.RECID = V.PARTY

#             LEFT JOIN HIQ_vendorELECTRONICADDRESSVIEW E
#                 ON E.ACCOUNTNUM = V.ACCOUNTNUM
#                AND E.DATAAREAID = 'hi-q'

#             WHERE V.HIQ_VENDORCOLLABORATION = 1

#             GROUP BY
#                 V.ACCOUNTNUM,
#                 P.NAME

#             ORDER BY P.NAME
#         """)

#         rows = cur.fetchall()

#         cols = [
#             c[0].lower()
#             for c in cur.description
#         ]

#         d365_vendors = [
#             dict(zip(cols, r))
#             for r in rows
#         ]

#     logger.info(f"[STEP 1] D365 vendors fetched: {len(d365_vendors)}")

#     # ========================================================
#     # STEP 2 - PORTAL USERS
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT
#                 VENDORACCOUNT,
#                 EMAILADDRESS

#             FROM {VENDOR_USER_TABLE}
#             WITH (NOLOCK)
#         """)

#         portal_rows = cur.fetchall()

#     logger.info(f"[STEP 2] Portal users fetched: {len(portal_rows)}")

#     # KEY FIX: normalize both account AND email while building the map
#     portal_map = {
#         normalize(r[0]): normalize(r[1])
#         for r in portal_rows
#     }

#     if not portal_map:
#         logger.warning(
#             "[STEP 2] portal_map is EMPTY — "
#             "check DB connection or table has data. "
#             "All D365 vendors will be treated as NEW."
#         )

#     # ========================================================
#     # STEP 3 - FILTER
#     # ========================================================
#     result = []

#     for v in d365_vendors:

#         account    = normalize(v["accountnum"])
#         d365_email = normalize(v["email"])

#         # Skip vendors with no email in D365 at all
#         # (nothing useful to register/update)
#         if not d365_email:
#             logger.debug(
#                 f"[SKIP] {account} — no D365 email, skipping"
#             )
#             continue

#         portal_email = portal_map.get(account)

#         # ====================================================
#         # NEW VENDOR — not in portal at all
#         # ====================================================
#         if portal_email is None:
#             logger.debug(
#                 f"[NEW] {account} — not in portal"
#             )
#             result.append(v)
#             continue

#         # ====================================================
#         # EMAIL CHANGED — in portal but email differs
#         # ====================================================
#         if portal_email != d365_email:
#             logger.debug(
#                 f"[CHANGED] {account} — "
#                 f"portal='{portal_email}' | d365='{d365_email}'"
#             )
#             result.append(v)
#             continue

#         # ====================================================
#         # UP TO DATE — already registered with same email
#         # ====================================================
#         logger.debug(
#             f"[SKIP] {account} — already up to date"
#         )

#     logger.info(
#         f"[STEP 3] Filter result: "
#         f"{len(result)} vendors need action "
#         f"(new or email changed)"
#     )

#     return result


# async def get_vendor_details():
#     return await run_in_threadpool(
#         get_vendor_details_sync
#     )

# # from fastapi.concurrency import run_in_threadpool

# # from app.db.base import (
# #     get_connection,
# #     get_d365_connection
# # )

# # from app.core.config import settings


# # SCHEMA = settings.DB_SCHEMA

# # VENDOR_USER_TABLE = (
# #     f"{SCHEMA}.HIQ_VENDORPORTALUSER"
# # )


# # # ============================================================
# # # HELPERS
# # # ============================================================
# # def normalize(val):
# #     return str(val or "").strip().upper()


# # # ============================================================
# # # VENDOR DETAILS
# # # ============================================================
# # def get_vendor_details_sync():

# #     # ========================================================
# #     # STEP 1 - D365 VENDORS
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute("""
# #             SELECT
# #                 V.ACCOUNTNUM,

# #                 P.NAME,

# #                 V.ACCOUNTNUM + ' - ' + P.NAME
# #                     AS DISPLAY_NAME,

# #                 MAX(
# #                     CASE
# #                         WHEN E.TYPE = 2
# #                          AND E.ISPRIMARY1 = 1
# #                         THEN E.LOCATOR
# #                     END
# #                 ) AS EMAIL,

# #                 MAX(
# #                     CASE
# #                         WHEN E.TYPE = 1
# #                          AND E.ISPRIMARY1 = 1
# #                         THEN E.LOCATOR
# #                     END
# #                 ) AS PHONE

# #             FROM VENDTABLE V

# #             JOIN DIRPARTYTABLE P
# #                 ON P.RECID = V.PARTY

# #             LEFT JOIN HIQ_vendorELECTRONICADDRESSVIEW E
# #                 ON E.ACCOUNTNUM = V.ACCOUNTNUM
# #                AND E.DATAAREAID = 'hi-q'

# #             WHERE V.HIQ_VENDORCOLLABORATION = 1

# #             GROUP BY
# #                 V.ACCOUNTNUM,
# #                 P.NAME

# #             ORDER BY P.NAME
# #         """)

# #         rows = cur.fetchall()

# #         cols = [
# #             c[0].lower()
# #             for c in cur.description
# #         ]

# #         d365_vendors = [
# #             dict(zip(cols, r))
# #             for r in rows
# #         ]

# #     # ========================================================
# #     # STEP 2 - PORTAL USERS
# #     # ========================================================
# #     with get_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute(f"""
# #             SELECT
# #                 VENDORACCOUNT,
# #                 EMAILADDRESS

# #             FROM {VENDOR_USER_TABLE}
# #             WITH (NOLOCK)
# #         """)

# #         portal_rows = cur.fetchall()

# #     portal_map = {
# #         normalize(r[0]): normalize(r[1])
# #         for r in portal_rows
# #     }

# #     # ========================================================
# #     # STEP 3 - FILTER
# #     # ========================================================
# #     result = []

# #     for v in d365_vendors:

# #         account = normalize(v["accountnum"])

# #         d365_email = normalize(v["email"])

# #         portal_email = portal_map.get(account)

# #         # ====================================================
# #         # NEW VENDOR
# #         # ====================================================
# #         if account not in portal_map:

# #             result.append(v)

# #             continue

# #         # ====================================================
# #         # EMAIL CHANGED
# #         # ====================================================
# #         if portal_email != d365_email:

# #             result.append(v)

# #     return result


# # async def get_vendor_details():
# #     return await run_in_threadpool(
# #         get_vendor_details_sync
# #     )


# # # from app.db.base import get_connection
# # # from fastapi.concurrency import run_in_threadpool
# # # def get_vendor_details_sync():
# # #     query = """
# # #     SELECT 
# # #         V.ACCOUNTNUM,
# # #         P.NAME,
# # #         V.ACCOUNTNUM + ' - ' + P.NAME AS DISPLAY_NAME,

# # #         MAX(CASE 
# # #             WHEN E.TYPE = 2 AND E.ISPRIMARY1 = 1 
# # #             THEN E.LOCATOR 
# # #         END) AS EMAIL,

# # #         MAX(CASE 
# # #             WHEN E.TYPE = 1 AND E.ISPRIMARY1 = 1 
# # #             THEN E.LOCATOR 
# # #         END) AS PHONE

# # #     FROM VENDTABLE V

# # #     JOIN DIRPARTYTABLE P 
# # #         ON P.RECID = V.PARTY

# # #     LEFT JOIN HIQ_vendorELECTRONICADDRESSVIEW E
# # #         ON E.ACCOUNTNUM = V.ACCOUNTNUM
# # #        AND E.DATAAREAID = 'hi-q'

# # #     -- ✅ EXCLUDE already registered vendors 
# # # WHERE  
# # #     V.HIQ_VENDORCOLLABORATION = 1

# # #     AND (
# # #         -- ✅ New vendors
# # #         NOT EXISTS (
# # #             SELECT 1 
# # #             FROM HIQ_VendorPortalUser U
# # #             WHERE U.vendor_account = V.ACCOUNTNUM
# # #         )

# # #         OR

# # #         -- 🔥 Email changed
# # #         EXISTS (
# # #             SELECT 1
# # #             FROM HIQ_VendorPortalUser U
# # #             WHERE U.vendor_account = V.ACCOUNTNUM
# # #               AND U.email_address <> (
# # #                     SELECT MAX(CASE 
# # #                         WHEN E2.TYPE = 2 AND E2.ISPRIMARY1 = 1 
# # #                         THEN E2.LOCATOR 
# # #                     END)
# # #                     FROM HIQ_vendorELECTRONICADDRESSVIEW E2
# # #                     WHERE E2.ACCOUNTNUM = V.ACCOUNTNUM
# # #                       AND E2.DATAAREAID = 'hi-q'
# # #               )
# # #         )
# # #     )

# # # GROUP BY 
# # #     V.ACCOUNTNUM,
# # #     P.NAME

# # #     ORDER BY P.NAME
# # #     """

# # #     with get_connection() as conn:
# # #         cur = conn.cursor()
# # #         cur.execute(query)

# # #         rows = cur.fetchall()
# # #         cols = [c[0].lower() for c in cur.description]

# # #         return [dict(zip(cols, r)) for r in rows]

# # # async def get_vendor_details():
# # #     return await run_in_threadpool(get_vendor_details_sync)