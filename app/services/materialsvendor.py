from fastapi.concurrency import run_in_threadpool

from app.db.base import (
    get_connection,
    get_d365_connection
)

from app.core.config import settings

from app.utils.date_utils import (
    format_ist_date_only
)

from app.utils.remainingdate import (
    calculate_days_left
)


SCHEMA = settings.DB_SCHEMA

VENDOR_USER_TABLE = (
    f"{SCHEMA}.HIQ_VENDORPORTALUSER"
)


# ============================================================
# HELPERS
# ============================================================
def normalize(val):
    return str(val or "").strip().upper()


# ============================================================
# MATERIAL LIST
# ============================================================
def fetch_materials_with_vendor_count_sync():

    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT

                LTRIM(RTRIM(IT.ITEMID))
                    AS MATERIAL_ID,

                ECORESPRODUCTTRANSLATION.NAME
                    AS MATERIAL_NAME,

                COUNT(
                    DISTINCT
                    LTRIM(
                        RTRIM(
                            AVL.PDSAPPROVEDVENDOR
                        )
                    )
                ) AS VENDORS_MAPPED,

                CASE
                    WHEN EXISTS (

                        SELECT 1

                        FROM PDSAPPROVEDVENDORLIST AVL2
                        WITH (NOLOCK)

                        WHERE
                            LTRIM(RTRIM(AVL2.ITEMID))
                            =
                            LTRIM(RTRIM(IT.ITEMID))

                          AND AVL2.DATAAREAID = 'hi-q'

                          AND AVL2.VALIDFROM
                                <= GETUTCDATE()

                          AND AVL2.VALIDTO
                                >= GETUTCDATE()

                          AND DATEDIFF(
                                DAY,
                                GETUTCDATE(),
                                AVL2.VALIDTO
                              ) < 30
                    )

                    THEN 1
                    ELSE 0
                END AS EXPIRY_STATUS

            FROM INVENTTABLE IT
            WITH (NOLOCK)

            JOIN HIQ_INVENTITEMGROUPITEMVIEW
                ON HIQ_INVENTITEMGROUPITEMVIEW.ITEMID
                   = IT.ITEMID

            LEFT JOIN ECORESPRODUCTTRANSLATION
                ON ECORESPRODUCTTRANSLATION.PRODUCT
                   = IT.PRODUCT

            LEFT JOIN PDSAPPROVEDVENDORLIST AVL
            WITH (NOLOCK)

                ON LTRIM(RTRIM(AVL.ITEMID))
                    =
                   LTRIM(RTRIM(IT.ITEMID))

               AND AVL.DATAAREAID = 'hi-q'

               AND AVL.VALIDFROM
                    <= GETUTCDATE()

               AND AVL.VALIDTO
                    >= GETUTCDATE()

            WHERE IT.DATAAREAID = 'hi-q'

              AND ITEMGROUPID NOT IN (
                    'FG-BLD/BRI',
                    'FG-FLEX',
                    'FG-HDI',
                    'FG-STD ML'
              )

            GROUP BY
                LTRIM(RTRIM(IT.ITEMID)),
                ECORESPRODUCTTRANSLATION.NAME

            ORDER BY
                LTRIM(RTRIM(IT.ITEMID))
        """)

        rows = cur.fetchall()

        cols = [
            c[0].lower()
            for c in cur.description
        ]

    result = []

    for r in rows:

        data = dict(zip(cols, r))

        result.append({

            "material_id":
                data["material_id"],

            "material_code":
                data["material_id"],

            "material_name":
                data["material_name"] or "-",

            "vendors_mapped":
                int(data["vendors_mapped"] or 0),

            "expiry_status":
                True
                if data["expiry_status"] == 1
                else False
        })

    return result


async def fetch_materials_with_vendor_count():
    return await run_in_threadpool(
        fetch_materials_with_vendor_count_sync
    )


# ============================================================
# MATERIAL DETAIL
# ============================================================
def fetch_material_detail_sync(
    item_id: str
):

    item_id = item_id.strip()

    # ========================================================
    # STEP 1 - PORTAL USERS
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                VENDORACCOUNT,
                STATUS,
                EMAILADDRESS

            FROM {VENDOR_USER_TABLE}
            WITH (NOLOCK)
        """)

        portal_rows = cur.fetchall()

    portal_map = {

        normalize(r[0]): {

            "status":
                r[1],

            "email":
                r[2]
        }

        for r in portal_rows
    }

    # ========================================================
    # STEP 2 - D365
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        # ====================================================
        # HEADER
        # ====================================================
        cur.execute("""
            SELECT
                LTRIM(RTRIM(IT.ITEMID))
                    AS ITEMID,

                IT.NAMEALIAS
                    AS MATERIAL_NAME,

                COUNT(
                    DISTINCT AVL.PDSAPPROVEDVENDOR
                ) AS TOTAL_VENDORS

            FROM INVENTTABLE IT
            WITH (NOLOCK)

            LEFT JOIN PDSAPPROVEDVENDORLIST AVL
            WITH (NOLOCK)

                ON LTRIM(RTRIM(AVL.ITEMID))
                   =
                   LTRIM(RTRIM(IT.ITEMID))

               AND AVL.DATAAREAID = 'hi-q'

               AND AVL.VALIDFROM
                    <= GETUTCDATE()

               AND AVL.VALIDTO
                    >= GETUTCDATE()

            WHERE
                LTRIM(RTRIM(IT.ITEMID)) = ?

              AND IT.DATAAREAID = 'hi-q'

            GROUP BY
                LTRIM(RTRIM(IT.ITEMID)),
                IT.NAMEALIAS
        """, (item_id,))

        header_row = cur.fetchone()

        # ====================================================
        # ACTIVE VENDORS
        # ====================================================
        cur.execute("""
            SELECT DISTINCT
                AVL.PDSAPPROVEDVENDOR

            FROM PDSAPPROVEDVENDORLIST AVL
            WITH (NOLOCK)

            WHERE
                LTRIM(RTRIM(AVL.ITEMID)) = ?

              AND AVL.DATAAREAID = 'hi-q'

              AND AVL.VALIDFROM
                    <= GETUTCDATE()

              AND AVL.VALIDTO
                    >= GETUTCDATE()
        """, (item_id,))

        approved_vendors = [
            normalize(r[0])
            for r in cur.fetchall()
        ]

        active_vendors = 0

        for vendor in approved_vendors:

            info = portal_map.get(vendor)

            if info and info["status"] == 1:
                active_vendors += 1

        # ====================================================
        # LATEST PRICE
        # ====================================================
        cur.execute("""
            SELECT TOP 1

                LQ.PURCHPRICE,

                LQ.PURCHUNIT,

                LQ.RFQID,

                LQ.RFQCASEID

            FROM (

                SELECT

                    RL.PURCHPRICE,

                    RL.PURCHUNIT,

                    RL.RFQID,

                    T.RFQCASEID,

                    T.VENDACCOUNT,

                    RL.CREATEDDATETIME,

                    ROW_NUMBER() OVER (
                        PARTITION BY T.VENDACCOUNT
                        ORDER BY RL.CREATEDDATETIME DESC
                    ) AS rn

                FROM PURCHRFQREPLYLINE RL
                WITH (NOLOCK)

                INNER JOIN PURCHRFQLINE PL
                WITH (NOLOCK)

                    ON PL.RECID = RL.RFQLINERECID

                   AND PL.DATAAREAID = 'hi-q'

                INNER JOIN PURCHRFQTABLE T
                WITH (NOLOCK)

                    ON T.RFQID = RL.RFQID

                   AND T.DATAAREAID = 'hi-q'

                INNER JOIN PDSAPPROVEDVENDORLIST AVL
                WITH (NOLOCK)

                    ON LTRIM(RTRIM(AVL.ITEMID))
                       =
                       LTRIM(RTRIM(PL.ITEMID))

                   AND AVL.PDSAPPROVEDVENDOR
                       = T.VENDACCOUNT

                   AND AVL.DATAAREAID = 'hi-q'

                   AND AVL.VALIDFROM
                        <= GETUTCDATE()

                   AND AVL.VALIDTO
                        >= GETUTCDATE()

                WHERE
                    LTRIM(RTRIM(PL.ITEMID)) = ?

                  AND RL.DATAAREAID = 'hi-q'

                  AND RL.PURCHPRICE > 0

            ) LQ

            WHERE LQ.rn = 1

            ORDER BY LQ.PURCHPRICE ASC
        """, (item_id,))

        price_row = cur.fetchone()

        # ====================================================
        # VENDOR DETAILS
        # ====================================================
        cur.execute("""
            SELECT

                DP.NAME
                    AS VENDOR_NAME,

                AVL.PDSAPPROVEDVENDOR
                    AS VENDOR_ACCOUNT,

                PA.CITY
                    AS LOCATION,

                AVL.VALIDTO
                    AS EXPIRY,

                LATEST.PURCHPRICE
                    AS QUOTE_PRICE,

                LATEST.PURCHUNIT
                    AS QUOTE_UNIT,

                LATEST.CREATEDDATETIME
                    AS LAST_QUOTE_DATE,

                LATEST.RFQID
                    AS RFQ_ID,

                LATEST.RFQCASEID
                    AS RFQ_CASE_ID

            FROM PDSAPPROVEDVENDORLIST AVL
            WITH (NOLOCK)

            INNER JOIN VENDTABLE V
            WITH (NOLOCK)

                ON V.ACCOUNTNUM
                   = AVL.PDSAPPROVEDVENDOR

               AND V.DATAAREAID = 'hi-q'

            INNER JOIN DIRPARTYTABLE DP
            WITH (NOLOCK)

                ON DP.RECID = V.PARTY

            LEFT JOIN LOGISTICSPOSTALADDRESS PA
            WITH (NOLOCK)

                ON PA.LOCATION
                   = DP.PRIMARYADDRESSLOCATION

               AND PA.VALIDTO >= GETUTCDATE()

            LEFT JOIN (

                SELECT

                    T.VENDACCOUNT,

                    LTRIM(RTRIM(PL.ITEMID))
                        AS ITEMID,

                    RL.PURCHPRICE,

                    RL.PURCHUNIT,

                    RL.CREATEDDATETIME,

                    RL.RFQID,

                    T.RFQCASEID,

                    ROW_NUMBER() OVER (

                        PARTITION BY
                            T.VENDACCOUNT,
                            LTRIM(RTRIM(PL.ITEMID))

                        ORDER BY
                            RL.CREATEDDATETIME DESC

                    ) AS RN

                FROM PURCHRFQREPLYLINE RL
                WITH (NOLOCK)

                INNER JOIN PURCHRFQREPLYTABLE RT
                WITH (NOLOCK)

                    ON RT.RFQID = RL.RFQID

                   AND RT.DATAAREAID = 'hi-q'

                INNER JOIN PURCHRFQTABLE T
                WITH (NOLOCK)

                    ON T.RFQID = RL.RFQID

                   AND T.DATAAREAID = 'hi-q'

                INNER JOIN PURCHRFQLINE PL
                WITH (NOLOCK)

                    ON PL.RECID = RL.RFQLINERECID

                   AND PL.DATAAREAID = 'hi-q'

                WHERE
                    RL.DATAAREAID = 'hi-q'

                  AND RL.PURCHPRICE > 0

            ) LATEST

                ON LATEST.VENDACCOUNT
                   = AVL.PDSAPPROVEDVENDOR

               AND LATEST.ITEMID
                   = LTRIM(RTRIM(AVL.ITEMID))

               AND LATEST.RN = 1

            WHERE
                LTRIM(RTRIM(AVL.ITEMID)) = ?

              AND AVL.DATAAREAID = 'hi-q'

              AND AVL.VALIDFROM
                    <= GETUTCDATE()

              AND AVL.VALIDTO
                    >= GETUTCDATE()

            ORDER BY
                LATEST.PURCHPRICE ASC
        """, (item_id,))

        vendor_rows = cur.fetchall()

        vendor_cols = [
            c[0].lower()
            for c in cur.description
        ]

        vendors = [
            dict(zip(vendor_cols, r))
            for r in vendor_rows
        ]

    # ========================================================
    # FINAL VENDOR ENRICHMENT
    # ========================================================
    final_vendors = []

    for v in vendors:

        vendor_account = normalize(
            v["vendor_account"]
        )

        portal_info = portal_map.get(
            vendor_account,
            {}
        )

        status = (
            "Active"
            if portal_info.get("status") == 1
            else "Inactive"
        )

        final_vendors.append({

            "vendor_name":
                v["vendor_name"] or "-",

            "vendor_account":
                v["vendor_account"] or "-",

            "location":
                v["location"] or "-",

            "quote_price":
                float(v["quote_price"] or 0),

            "quote_unit":
                v["quote_unit"] or "-",

            "last_quote_date":
                format_ist_date_only(
                    v["last_quote_date"]
                ),

            "rfq_id":
                v["rfq_id"],

            "rfq_case_id":
                v["rfq_case_id"],

            "expiry":
                format_ist_date_only(
                    v["expiry"]
                ),

            "days_left":
                calculate_days_left(
                    v["expiry"]
                ),

            "expiry_status":
                True
                if calculate_days_left(
                    v["expiry"]
                ) < 30
                else False,

            "status":
                status,

            "contact":
                portal_info.get("email") or "-"
        })

    return {

        "material_id":
            item_id,

        "material_code":
            item_id,

        "material_name":
            header_row[1]
            if header_row
            else "-",

        "total_vendors":
            int(header_row[2] or 0)
            if header_row
            else 0,

        "active_vendors":
            active_vendors,

        "latest_quoted_price":
            float(price_row[0] or 0)
            if price_row
            else 0,

        "price_unit":
            price_row[1]
            if price_row
            else "-",

        "rfq_id":
            price_row[2]
            if price_row
            else None,

        "rfq_case_id":
            price_row[3]
            if price_row
            else None,

        "vendors":
            final_vendors
    }


async def fetch_material_detail(
    item_id: str
):
    return await run_in_threadpool(
        fetch_material_detail_sync,
        item_id
    )


# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.lastused_time import format_last_edited
# from app.utils.remainingdate import calculate_days_left,format_expiry_label
# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# from fastapi.concurrency import run_in_threadpool
# def fetch_materials_with_vendor_count_sync():
#     with get_connection() as conn:
#         cur = conn.cursor()

#         cur.execute("""
#             SELECT

#                 LTRIM(RTRIM(IT.ITEMID)) AS MATERIAL_ID,

#                 --IT.NAMEALIAS AS MATERIAL_NAME,

# 				ECORESPRODUCTTRANSLATION.NAME AS MATERIAL_NAME,

#                 COUNT(DISTINCT LTRIM(RTRIM(AVL.PDSAPPROVEDVENDOR))) AS VENDORS_MAPPED,

#                 -- EXPIRY STATUS (vendor-level check)

#                 CASE

#                     WHEN EXISTS (

#                         SELECT 1

#                         FROM PDSAPPROVEDVENDORLIST AVL2 WITH (NOLOCK)

#                         WHERE LTRIM(RTRIM(AVL2.ITEMID)) = LTRIM(RTRIM(IT.ITEMID))

#                           AND AVL2.DATAAREAID = 'hi-q'

#                           AND AVL2.VALIDFROM <= GETUTCDATE()

#                           AND AVL2.VALIDTO   >= GETUTCDATE()

#                           AND DATEDIFF(DAY, GETUTCDATE(), AVL2.VALIDTO) < 30

#                     )

#                     THEN 1 ELSE 0

#                 END AS EXPIRY_STATUS

#             FROM INVENTTABLE IT WITH (NOLOCK)

# 			JOIN HIQ_INVENTITEMGROUPITEMVIEW ON HIQ_INVENTITEMGROUPITEMVIEW.ITEMID = IT.ITEMID

# 			LEFT JOIN ECORESPRODUCTTRANSLATION  ON ECORESPRODUCTTRANSLATION.PRODUCT = IT.PRODUCT

#             LEFT JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)

#                 ON LTRIM(RTRIM(AVL.ITEMID)) = LTRIM(RTRIM(IT.ITEMID))

#                 AND AVL.DATAAREAID = 'hi-q'

#                 AND AVL.VALIDFROM <= GETUTCDATE()

#                 AND AVL.VALIDTO   >= GETUTCDATE()

 


#             WHERE IT.DATAAREAID = 'hi-q'

# 			AND ITEMGROUPID NOT IN('FG-BLD/BRI','FG-FLEX','FG-HDI','FG-STD ML')

#             GROUP BY

#                 LTRIM(RTRIM(IT.ITEMID)),

#                 --IT.NAMEALIAS

# 				ECORESPRODUCTTRANSLATION.NAME

#             ORDER BY LTRIM(RTRIM(IT.ITEMID))
#         """)

#         rows = cur.fetchall()
#         cols = [c[0].lower() for c in cur.description]

#         result = []
#         for r in rows:
#             data = dict(zip(cols, r))

#             result.append({
#                 "material_id":    data["material_id"],
#                 "material_code":  data["material_id"],
#                 "material_name":  data["material_name"] or "-",
#                 "vendors_mapped": int(data["vendors_mapped"] or 0),

#                 # ✅ FINAL FLAG
#                 "expiry_status":  True if data["expiry_status"] == 1 else False
#             })

#         return result
    

# async def fetch_materials_with_vendor_count():
#     return await run_in_threadpool(fetch_materials_with_vendor_count_sync)

# def fetch_material_detail_sync(item_id: str):
#     item_id = item_id.strip()  # ✅ clean incoming param

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # ── 1. Header ─────────────────────────────────────────
#         cur.execute("""
#             SELECT
#                 LTRIM(RTRIM(IT.ITEMID))                       AS ITEMID,
#                 IT.NAMEALIAS                                  AS MATERIAL_NAME,
#                 COUNT(DISTINCT AVL.PDSAPPROVEDVENDOR)         AS TOTAL_VENDORS
#             FROM INVENTTABLE IT WITH (NOLOCK)
#             LEFT JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#                 ON  LTRIM(RTRIM(AVL.ITEMID)) = LTRIM(RTRIM(IT.ITEMID))
#                 AND AVL.DATAAREAID = 'hi-q'
#                 AND AVL.VALIDFROM <= GETUTCDATE()
#                 AND AVL.VALIDTO   >= GETUTCDATE()
#             WHERE LTRIM(RTRIM(IT.ITEMID)) = ?
#               AND IT.DATAAREAID = 'hi-q'
#             GROUP BY LTRIM(RTRIM(IT.ITEMID)), IT.NAMEALIAS
#         """, (item_id,))
#         header_row = cur.fetchone()

#         # ── 2. Active vendors ─────────────────────────────────
#         cur.execute("""
#             SELECT COUNT(DISTINCT AVL.PDSAPPROVEDVENDOR)
#             FROM PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#             INNER JOIN HIQ_VendorPortalUser U WITH (NOLOCK)
#                 ON  U.VENDOR_ACCOUNT = AVL.PDSAPPROVEDVENDOR
#             WHERE LTRIM(RTRIM(AVL.ITEMID)) = ?
#               AND AVL.DATAAREAID = 'hi-q'
#               AND AVL.VALIDFROM <= GETUTCDATE()
#               AND AVL.VALIDTO   >= GETUTCDATE()
#               AND U.STATUS IN (1)
#         """, (item_id,))
#         active_vendors = int(cur.fetchone()[0] or 0)

#         # ── 3. Latest quoted price ────────────────────────────
#         cur.execute("""SELECT TOP 1
#     LQ.PURCHPRICE,
#     LQ.PURCHUNIT,
#     LQ.RFQID,
#     LQ.RFQCASEID
# FROM (
#     SELECT
#         RL.PURCHPRICE,
#         RL.PURCHUNIT,
#         RL.RFQID,
#         T.RFQCASEID,
#         T.VENDACCOUNT,
#         RL.CREATEDDATETIME,
        
#         -- Get latest record per vendor
#         ROW_NUMBER() OVER (
#             PARTITION BY T.VENDACCOUNT
#             ORDER BY RL.CREATEDDATETIME DESC
#         ) AS rn

#     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)

#     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#         ON PL.RECID = RL.RFQLINERECID
#         AND PL.DATAAREAID = 'hi-q'

#     INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
#         ON T.RFQID = RL.RFQID
#         AND T.DATAAREAID = 'hi-q'

#     INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#         ON LTRIM(RTRIM(AVL.ITEMID)) = LTRIM(RTRIM(PL.ITEMID))
#         AND AVL.PDSAPPROVEDVENDOR = T.VENDACCOUNT
#         AND AVL.DATAAREAID = 'hi-q'
#         AND AVL.VALIDFROM <= GETUTCDATE()
#         AND AVL.VALIDTO   >= GETUTCDATE()

#     WHERE LTRIM(RTRIM(PL.ITEMID)) = ?
#       AND RL.DATAAREAID = 'hi-q'
#       AND RL.PURCHPRICE > 0

# ) LQ

# -- Only latest quote per vendor
# WHERE LQ.rn = 1

# -- Now pick lowest among latest
# ORDER BY LQ.PURCHPRICE ASC""", (item_id))
#         # cur.execute("""
#         #     SELECT TOP 1
#         #         RL.PURCHPRICE,
#         #         RL.PURCHUNIT,
#         #         RL.RFQID,
#         #         T.RFQCASEID
#         #     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
#         #     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#         #         ON  PL.RECID      = RL.RFQLINERECID
#         #         AND PL.DATAAREAID = 'hi-q'
#         #     INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
#         #         ON  T.RFQID      = RL.RFQID
#         #         AND T.DATAAREAID = 'hi-q'
#         #     INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#         #         ON  LTRIM(RTRIM(AVL.ITEMID)) = LTRIM(RTRIM(PL.ITEMID))
#         #         AND AVL.PDSAPPROVEDVENDOR    = T.VENDACCOUNT
#         #         AND AVL.DATAAREAID           = 'hi-q'
#         #         AND AVL.VALIDFROM           <= GETUTCDATE()
#         #         AND AVL.VALIDTO             >= GETUTCDATE()
#         #     WHERE LTRIM(RTRIM(PL.ITEMID)) = ?
#         #       AND RL.DATAAREAID            = 'hi-q'
#         #       AND RL.PURCHPRICE            > 0
#         #     ORDER BY
#         #         RL.CREATEDDATETIME DESC,
#         #         RL.PURCHPRICE ASC
#         # """, (item_id,))
#         price_row = cur.fetchone()

#         # ── 4. Vendors list ───────────────────────────────────
#         cur.execute("""
#             SELECT
#                 DP.NAME                     AS VENDOR_NAME,
#                 AVL.PDSAPPROVEDVENDOR       AS VENDOR_ACCOUNT,
#                 PA.CITY                     AS LOCATION,
#                 AVL.VALIDTO                 AS EXPIRY,
#                 LATEST.PURCHPRICE           AS QUOTE_PRICE,
#                 LATEST.PURCHUNIT            AS QUOTE_UNIT,
#                 LATEST.CREATEDDATETIME      AS LAST_QUOTE_DATE,
#                 LATEST.RFQID                AS RFQ_ID,
#                 LATEST.RFQCASEID            AS RFQ_CASE_ID,
#                 CASE
#                     WHEN U.STATUS IN (1) THEN 'Active'
#                     ELSE 'Inactive'
#                 END                         AS STATUS,
#                 U.EMAIL_ADDRESS             AS CONTACT

#             FROM PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)

#             INNER JOIN VENDTABLE V WITH (NOLOCK)
#                 ON  V.ACCOUNTNUM  = AVL.PDSAPPROVEDVENDOR
#                 AND V.DATAAREAID  = 'hi-q'

#             INNER JOIN DIRPARTYTABLE DP WITH (NOLOCK)
#                 ON  DP.RECID = V.PARTY

#             LEFT JOIN LOGISTICSPOSTALADDRESS PA WITH (NOLOCK)
#                 ON  PA.LOCATION = DP.PRIMARYADDRESSLOCATION
#                 AND PA.VALIDTO >= GETUTCDATE()

#             LEFT JOIN HIQ_VendorPortalUser U WITH (NOLOCK)
#                 ON  U.VENDOR_ACCOUNT = AVL.PDSAPPROVEDVENDOR

#             LEFT JOIN (
#                 SELECT
#                     T.VENDACCOUNT,
#                     LTRIM(RTRIM(PL.ITEMID))  AS ITEMID,
#                     RL.PURCHPRICE,
#                     RL.PURCHUNIT,
#                     RL.CREATEDDATETIME,
#                     RL.RFQID,
#                     T.RFQCASEID,
#                     ROW_NUMBER() OVER (
#                         PARTITION BY T.VENDACCOUNT, LTRIM(RTRIM(PL.ITEMID))
#                         ORDER BY RL.CREATEDDATETIME DESC
#                     ) AS RN
#                 FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
#                 INNER JOIN PURCHRFQREPLYTABLE RT WITH (NOLOCK)
#                     ON  RT.RFQID      = RL.RFQID
#                     AND RT.DATAAREAID = 'hi-q'
#                 INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
#                     ON  T.RFQID      = RL.RFQID
#                     AND T.DATAAREAID = 'hi-q'
#                 INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#                     ON  PL.RECID      = RL.RFQLINERECID
#                     AND PL.DATAAREAID = 'hi-q'
#                 WHERE RL.DATAAREAID = 'hi-q'
#                   AND RL.PURCHPRICE > 0
#             ) LATEST
#                 ON  LATEST.VENDACCOUNT = AVL.PDSAPPROVEDVENDOR
#                 AND LATEST.ITEMID      = LTRIM(RTRIM(AVL.ITEMID))
#                 AND LATEST.RN          = 1

#             WHERE LTRIM(RTRIM(AVL.ITEMID)) = ?
#               AND AVL.DATAAREAID           = 'hi-q'
#               AND AVL.VALIDFROM           <= GETUTCDATE()
#               AND AVL.VALIDTO             >= GETUTCDATE()

#             ORDER BY LATEST.PURCHPRICE ASC
#         """, (item_id,))

#         vendor_rows = cur.fetchall()
#         vendor_cols = [c[0].lower() for c in cur.description]
#         vendors     = [dict(zip(vendor_cols, r)) for r in vendor_rows]

#     return {
#         "material_id":          item_id,
#         "material_code":        item_id,
#         "material_name":        header_row[1] if header_row else "-",
#         "total_vendors":        int(header_row[2] or 0) if header_row else 0,
#         "active_vendors":       active_vendors,
#         "latest_quoted_price":  float(price_row[0] or 0) if price_row else 0,
#         "price_unit":           price_row[1] if price_row else "-",
#         "rfq_id":               price_row[2] if price_row else None,
#         "rfq_case_id":          price_row[3] if price_row else None,
#         "vendors": [
#             {
#                 "vendor_name":      v["vendor_name"]      or "-",
#                 "vendor_account":   v["vendor_account"]   or "-",
#                 "location":         v["location"]         or "-",
#                 "quote_price":      float(v["quote_price"] or 0),
#                 "quote_unit":       v["quote_unit"]        or "-",
#                 "last_quote_date":  format_ist_date_only(v["last_quote_date"]),
#                 "rfq_id":           v["rfq_id"],
#                 "rfq_case_id":      v["rfq_case_id"],
#                 "expiry":           format_ist_date_only(v["expiry"]),
#                 "days_left":        calculate_days_left(v["expiry"]),
#                 "expiry_status":    True if calculate_days_left(v["expiry"]) < 30 else False,
#                 "status":           v["status"]  or "Dormant",
#                 "contact":          v["contact"] or "-",
#             }
#             for v in vendors
#         ]
#     }


# async def fetch_material_detail(item_id: str):
#     return await run_in_threadpool(fetch_material_detail_sync, item_id)