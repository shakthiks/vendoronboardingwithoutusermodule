
from app.db.base import (
    get_connection,
    get_d365_connection
)

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

SCHEMA = settings.DB_SCHEMA

VENDOR_USER_TABLE = (
    f"{SCHEMA}.HIQ_VENDORPORTALUSER"
)


def vendor_list_sync():

    # ==========================================
    # STEP 1 — READ D365 VENDORS
    # ==========================================

    d365_query = """
    SELECT
        V.ACCOUNTNUM,

        P.CITY,

        P.NAME AS VENDOR_NAME,

        CP.CONTACT_PERSON,

        CASE
            WHEN EXISTS (
                SELECT 1
                FROM PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
                WHERE AVL.PDSAPPROVEDVENDOR = V.ACCOUNTNUM
                  AND AVL.DATAAREAID = 'hi-q'
                  AND AVL.VALIDFROM <= GETUTCDATE()
                  AND AVL.VALIDTO >= GETUTCDATE()
                  AND DATEDIFF(
                        DAY,
                        GETUTCDATE(),
                        AVL.VALIDTO
                      ) < 30
            )
            THEN 1
            ELSE 0
        END AS EXPIRY_STATUS

    FROM VENDTABLE V

    OUTER APPLY (
        SELECT TOP 1
            CITY,
            NAME
        FROM HIQ_VENDORPOSTALADDRESSVIEW P
        WHERE P.ACCOUNTNUM = V.ACCOUNTNUM
          AND P.ISPRIMARY = 1
        ORDER BY P.RECID DESC
    ) P

    OUTER APPLY (
        SELECT TOP 1
            DP.NAME AS CONTACT_PERSON
        FROM CONTACTPERSON C
        JOIN DIRPARTYTABLE DP
            ON DP.RECID = C.PARTY
        WHERE C.CONTACTFORPARTY = V.PARTY
          AND C.CONTACTPERSONID = V.CONTACTPERSONID
    ) CP
    """

    with get_d365_connection() as conn:

        cursor = conn.cursor()

        cursor.execute(d365_query)

        rows = cursor.fetchall()

        cols = [
            c[0].lower()
            for c in cursor.description
        ]

        d365_vendors = [
            dict(zip(cols, r))
            for r in rows
        ]

    # ==========================================
    # STEP 2 — READ PORTAL USER STATUS
    # ==========================================

    with get_connection() as conn:

        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT
                VENDORACCOUNT,
                STATUS
            FROM {VENDOR_USER_TABLE}
            """
        )

        user_rows = cursor.fetchall()

        portal_status_map = {
            str(r[0]).strip(): int(r[1] or 0)
            for r in user_rows
        }

    # ==========================================
    # STEP 3 — MERGE BOTH
    # ==========================================

    vendors = []

    for data in d365_vendors:

        vendor_account = (
            str(data.get("accountnum") or "")
            .strip()
        )

        status_value = (
            portal_status_map.get(
                vendor_account,
                0
            )
        )

        vendors.append({

            "vendor_account":
                vendor_account,

            "vendor_name":
                data.get("vendor_name") or "-",

            "location":
                data.get("city") or "-",

            "contact_person":
                data.get("contact_person") or "-",

            "status":
                "Active"
                if status_value == 1
                else "Inactive",

            "expiry_status":
                True
                if data.get("expiry_status") == 1
                else False
        })

    return vendors


async def vendor_list():

    try:

        return await run_in_threadpool(
            vendor_list_sync
        )

    except Exception as e:

        raise Exception(
            f"[VENDORPORTAL] Connection failed: {str(e)}"
        )

# from app.db.base import get_connection,get_d365_connection
# from fastapi.concurrency import run_in_threadpool
# def vendor_list_sync():
#     query = """
#     SELECT 
#         V.ACCOUNTNUM,

#         P.CITY,
#         P.NAME AS VENDOR_NAME,

#         -- CONTACT PERSON NAME
#         CP.CONTACT_PERSON,

#         -- STATUS
#         CASE 
#             WHEN U.STATUS IN (1) THEN 'Active'
#             ELSE 'Inactive'
#         END AS STATUS,
#         CASE 
#         WHEN EXISTS (
#             SELECT 1
#             FROM PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#             WHERE AVL.PDSAPPROVEDVENDOR = V.ACCOUNTNUM
#               AND AVL.DATAAREAID = 'hi-q'
#               AND AVL.VALIDFROM <= GETUTCDATE()
#               AND AVL.VALIDTO >= GETUTCDATE()
#               AND DATEDIFF(DAY, GETUTCDATE(), AVL.VALIDTO) < 30
#         )
#         THEN 1 ELSE 0
#     END AS EXPIRY_STATUS

#     FROM VENDTABLE V

#     -- PRIMARY ADDRESS (ONE ROW ONLY)
#     OUTER APPLY (
#         SELECT TOP 1 CITY, NAME
#         FROM HIQ_VENDORPOSTALADDRESSVIEW P
#         WHERE P.ACCOUNTNUM = V.ACCOUNTNUM
#           AND P.ISPRIMARY = 1
#         ORDER BY P.RECID DESC
#     ) P

#     -- CONTACT PERSON (ONE ROW ONLY)
#     OUTER APPLY (
#         SELECT TOP 1 DP.NAME AS CONTACT_PERSON
#         FROM CONTACTPERSON C
#         JOIN DIRPARTYTABLE DP 
#             ON DP.RECID = C.PARTY
#         WHERE C.CONTACTFORPARTY = V.PARTY
#           AND C.CONTACTPERSONID = V.CONTACTPERSONID
#     ) CP

#     -- USER STATUS
#     LEFT JOIN HIQ_VendorPortalUser U
#         ON V.ACCOUNTNUM = U.VENDOR_ACCOUNT
#     """

#     # with get_connection() as conn:
#     with get_d365_connection() as conn:
#         cursor = conn.cursor()
#         cursor.execute(query)
#         rows = cursor.fetchall()

#         cols = [c[0].lower() for c in cursor.description]

#         vendors = []
#         for r in rows:
#             data = dict(zip(cols, r))

#             vendors.append({
#                 "vendor_account": data.get("accountnum"),
#                 "vendor_name": data.get("vendor_name") or "-",
#                 "location": data.get("city") or "-",
#                 "contact_person": data.get("contact_person") or "-",  
#                 "status": data.get("status"),
#                 "expiry_status": True if data.get("expiry_status") == 1 else False
#             })

#         return vendors

# # async def vendor_list():
# #     return await run_in_threadpool(vendor_list_sync)

# async def vendor_list():
#     try:
#         return await run_in_threadpool(vendor_list_sync)
#     except Exception as e:
#         raise Exception(f"[VENDORPORTAL] Connection failed: {str(e)}")