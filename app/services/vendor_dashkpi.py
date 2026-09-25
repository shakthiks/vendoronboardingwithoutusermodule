import json

from fastapi.concurrency import run_in_threadpool

from app.db.base import (
    get_connection,
    get_d365_connection
)

from app.core.config import settings

from app.services.closedrfq import get_closed_cases_sync
SCHEMA = settings.DB_SCHEMA

RFQ_REPLIES_TABLE = f"{SCHEMA}.HIQ_VENDORRFQREPLIES"


# ============================================================
# HELPERS
# ============================================================
def normalize(val):
    return str(val or "").replace(" ", "").strip().upper()


# ============================================================
# RFQ KPI
# ============================================================
def get_rfq_kpi_sync():

    # ========================================================
    # D365 KPIs
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        # ====================================================
        # TOTAL RFQ CASES
        # ====================================================
        cur.execute("""
            SELECT COUNT(DISTINCT C.RFQCASEID)

            FROM PurchRFQCaseTable C WITH (NOLOCK)

            INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
                ON T.RFQCASEID = C.RFQCASEID
               AND T.DATAAREAID = 'hi-q'

            WHERE T.RFQID IS NOT NULL
              AND LTRIM(RTRIM(T.RFQID)) <> ''
        """)

        total_rfq_cases = cur.fetchone()[0] or 0

        # ====================================================
        # TOTAL ACTIVE CASES
        # IST offset applied
        # ====================================================
        cur.execute("""
            SELECT COUNT(*)

            FROM PurchRFQCaseTable WITH (NOLOCK)

            WHERE CAST(DATEADD(MINUTE,330,EXPIRYDATETIME) AS DATE)
                  >= CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)
        """)

        total_active_cases = cur.fetchone()[0] or 0

        # ====================================================
        # ON BIDDING
        # IST offset applied on both WHERE and HAVING clauses
        # ====================================================
        cur.execute("""
            SELECT COUNT(*)

            FROM (
                SELECT T.RFQCASEID

                FROM PURCHRFQTABLE T WITH (NOLOCK)

                WHERE T.DATAAREAID = 'hi-q'
                  AND CAST(
                        DATEADD(MINUTE,330,T.EXPIRYDATETIME)
                      AS DATE)
                      >=
                      CAST(
                        DATEADD(MINUTE,330,GETUTCDATE())
                      AS DATE)

                GROUP BY T.RFQCASEID

                HAVING
                    CAST(
                        DATEADD(MINUTE,330,MAX(T.EXPIRYDATETIME))
                    AS DATE)
                    >=
                    CAST(
                        DATEADD(MINUTE,330,GETUTCDATE())
                    AS DATE)

                    AND NOT EXISTS (
                        SELECT 1

                        FROM PURCHRFQLINE PL WITH (NOLOCK)

                        JOIN PURCHRFQTABLE T2 WITH (NOLOCK)
                            ON T2.RFQID = PL.RFQID

                        WHERE T2.RFQCASEID = T.RFQCASEID
                          AND PL.STATUS = 4
                          AND PL.DATAAREAID = 'hi-q'
                    )
            ) X
        """)

        on_bidding = cur.fetchone()[0] or 0

        # ====================================================
        # EXPIRING SOON
        # IST offset applied on date range comparison
        # ====================================================
        cur.execute("""
            SELECT COUNT(DISTINCT C.RFQCASEID)

            FROM PurchRFQCaseTable C WITH (NOLOCK)

            INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
                ON T.RFQCASEID = C.RFQCASEID
               AND T.DATAAREAID = 'hi-q'

            WHERE
                CAST(DATEADD(MINUTE,330,C.EXPIRYDATETIME) AS DATE)
                BETWEEN
                    CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)
                    AND CAST(DATEADD(DAY,3,DATEADD(MINUTE,330,GETUTCDATE())) AS DATE)

                AND T.RFQID IS NOT NULL
                AND LTRIM(RTRIM(T.RFQID)) <> ''

                AND NOT EXISTS (
                    SELECT 1

                    FROM PURCHRFQLINE PL WITH (NOLOCK)

                    WHERE PL.RFQID = T.RFQID
                      AND PL.STATUS = 4
                      AND PL.DATAAREAID = 'hi-q'
                )
        """)

        expiring_soon = cur.fetchone()[0] or 0

    # ========================================================
    # PORTAL DB — fetch submitted rows for under_review check
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                RFQCASEID,
                RFQID,
                VENDORACCOUNT,
                PAYLOADJSON,
                SUBMISSIONSTATUS

            FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)

            WHERE SUBMISSIONSTATUS = 1
        """)

        portal_rows = cur.fetchall()

    # ========================================================
    # UNDER REVIEW
    # ========================================================
    under_review = 0
    checked_cases = set()

    with get_d365_connection() as conn:

        cur = conn.cursor()

        for r in portal_rows:

            rfq_case_id = normalize(r[0])
            rfq_id = normalize(r[1])
            vendor = normalize(r[2])

            payload_json = r[3]

            if rfq_case_id in checked_cases:
                continue

            if not payload_json:
                continue

            try:
                payload = json.loads(payload_json)
            except Exception:
                continue

            valid_items = set()

            for item in payload.get("Item", []):

                status = str(
                    item.get("lineStatus")
                ).lower()

                if status in ["true", "1"]:

                    valid_items.add(
                        normalize(
                            item.get("itemNumber")
                        )
                    )

            if not valid_items:
                continue

            cur.execute("""
                SELECT ITEMID, STATUS

                FROM PURCHRFQLINE WITH (NOLOCK)

                WHERE RFQID = ?
                  AND DATAAREAID = 'hi-q'
            """, rfq_id)

            lines = cur.fetchall()

            for line in lines:

                item = normalize(line[0])
                status = line[1]

                if item in valid_items and status < 3:

                    under_review += 1
                    checked_cases.add(rfq_case_id)

                    break

    # ========================================================
    # CLOSED CASES — use same source as the Closed tab
    # Called outside any connection block so it can open
    # its own connection without conflict.
    # ========================================================
    closed_result = get_closed_cases_sync()

    if isinstance(closed_result, dict):
        closed_cases = closed_result.get("count", 0)
    elif isinstance(closed_result, list):
        closed_cases = len(closed_result)
    else:
        closed_cases = 0

    # ========================================================
    # FINAL
    # ========================================================
    return {
        "total_active_cases": on_bidding + under_review,

        "on_bidding": on_bidding,

        "expiring_soon": expiring_soon,

        "under_review": under_review,

        "total_rfq_cases": total_rfq_cases,

        "closed_cases": closed_cases
    }


async def get_rfq_kpi():
    return await run_in_threadpool(get_rfq_kpi_sync)


# ============================================================
# VENDOR DASHBOARD KPI
# ============================================================
def get_vendordashboard_kpi_sync():

    with get_d365_connection() as conn:

        cur = conn.cursor()

        # ====================================================
        # TOTAL VENDORS
        # ====================================================
        cur.execute("""
            SELECT COUNT(DISTINCT ACCOUNTNUM)

            FROM VENDTABLE WITH (NOLOCK)
        """)

        total_vendors = cur.fetchone()[0] or 0

        # ====================================================
        # TOTAL MATERIALS
        # ====================================================
        cur.execute("""
            SELECT COUNT(DISTINCT ITEMID)

            FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)
        """)

        total_materials = cur.fetchone()[0] or 0

        # ====================================================
        # UPCOMING EXPIRY
        # ====================================================
        cur.execute("""
            SELECT COUNT(*)

            FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)

            WHERE VALIDTO BETWEEN
                GETUTCDATE()
                AND DATEADD(DAY, 30, GETUTCDATE())
        """)

        upcoming_expiry = cur.fetchone()[0] or 0

        # ====================================================
        # EXPIRY DETAILS
        # ====================================================
        cur.execute("""
            SELECT
                ITEMID,
                PDSAPPROVEDVENDOR,
                VALIDFROM,
                VALIDTO

            FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)

            WHERE VALIDTO BETWEEN
                GETUTCDATE()
                AND DATEADD(DAY, 30, GETUTCDATE())

            ORDER BY VALIDTO
        """)

        expiry_data = [
            {
                "item": r[0],
                "vendor": r[1],
                "valid_from": r[2],
                "valid_to": r[3]
            }
            for r in cur.fetchall()
        ]

    return {
        "total_vendors": total_vendors,

        "total_materials_mapped": total_materials,

        "upcoming_expiry": upcoming_expiry,

        "expiry_details": expiry_data
    }


async def get_vendordashboard_kpi():
    return await run_in_threadpool(
        get_vendordashboard_kpi_sync
    )


# import json

# from fastapi.concurrency import run_in_threadpool

# from app.db.base import (
#     get_connection,
#     get_d365_connection
# )

# from app.core.config import settings

# from app.services.closedrfq import get_closed_cases_sync
# SCHEMA = settings.DB_SCHEMA

# RFQ_REPLIES_TABLE = f"{SCHEMA}.HIQ_VENDORRFQREPLIES"


# # ============================================================
# # HELPERS
# # ============================================================
# def normalize(val):
#     return str(val or "").replace(" ", "").strip().upper()


# # ============================================================
# # RFQ KPI
# # ============================================================
# def get_rfq_kpi_sync():

#     # ========================================================
#     # D365 KPIs
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         # ====================================================
#         # TOTAL RFQ CASES
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(DISTINCT C.RFQCASEID)

#             FROM PurchRFQCaseTable C WITH (NOLOCK)

#             INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
#                 ON T.RFQCASEID = C.RFQCASEID
#                AND T.DATAAREAID = 'hi-q'

#             WHERE T.RFQID IS NOT NULL
#               AND LTRIM(RTRIM(T.RFQID)) <> ''
#         """)

#         total_rfq_cases = cur.fetchone()[0] or 0

#         # ====================================================
#         # TOTAL ACTIVE CASES
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(*)

#             FROM PurchRFQCaseTable WITH (NOLOCK)

#             WHERE EXPIRYDATETIME >= GETUTCDATE()
#         """)

#         total_active_cases = cur.fetchone()[0] or 0

#         # ====================================================
#         # ON BIDDING
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(*)

#             FROM (
#                 SELECT T.RFQCASEID

#                 FROM PURCHRFQTABLE T WITH (NOLOCK)

#                 WHERE T.DATAAREAID = 'hi-q'
#                   AND T.EXPIRYDATETIME >= GETUTCDATE()

#                 GROUP BY T.RFQCASEID

#                 HAVING
#                     MAX(T.EXPIRYDATETIME) >= GETUTCDATE()

#                     AND NOT EXISTS (
#                         SELECT 1

#                         FROM PURCHRFQLINE PL WITH (NOLOCK)

#                         JOIN PURCHRFQTABLE T2 WITH (NOLOCK)
#                             ON T2.RFQID = PL.RFQID

#                         WHERE T2.RFQCASEID = T.RFQCASEID
#                           AND PL.STATUS = 4
#                           AND PL.DATAAREAID = 'hi-q'
#                     )
#             ) X
#         """)

#         on_bidding = cur.fetchone()[0] or 0

#         # ====================================================
#         # EXPIRING SOON
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(DISTINCT C.RFQCASEID)

#             FROM PurchRFQCaseTable C WITH (NOLOCK)

#             INNER JOIN PURCHRFQTABLE T WITH (NOLOCK)
#                 ON T.RFQCASEID = C.RFQCASEID
#                AND T.DATAAREAID = 'hi-q'

#             WHERE
#                 C.EXPIRYDATETIME BETWEEN
#                     GETUTCDATE()
#                     AND DATEADD(DAY, 3, GETUTCDATE())

#                 AND T.RFQID IS NOT NULL
#                 AND LTRIM(RTRIM(T.RFQID)) <> ''

#                 AND NOT EXISTS (
#                     SELECT 1

#                     FROM PURCHRFQLINE PL WITH (NOLOCK)

#                     WHERE PL.RFQID = T.RFQID
#                       AND PL.STATUS = 4
#                       AND PL.DATAAREAID = 'hi-q'
#                 )
#         """)

#         expiring_soon = cur.fetchone()[0] or 0

#         # ====================================================
#         # CLOSED CASES
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(DISTINCT T.RFQCASEID)

#             FROM PURCHRFQTABLE T WITH (NOLOCK)

#             WHERE T.DATAAREAID = 'hi-q'

#               AND EXISTS (
#                     SELECT 1

#                     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)

#                     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#                         ON PL.RECID = RL.RFQLINERECID

#                     INNER JOIN PURCHRFQTABLE T2 WITH (NOLOCK)
#                         ON T2.RFQID = RL.RFQID

#                     WHERE T2.RFQCASEID = T.RFQCASEID
#                       AND PL.STATUS >= 3
#                       AND PL.DATAAREAID = 'hi-q'
#               )
#         """)

#         d365_closed_cases = {
#             normalize(r[0])
#             for r in cur.fetchall()
#         }
        

#     # ========================================================
#     # PORTAL DB
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT
#                 RFQCASEID,
#                 RFQID,
#                 VENDORACCOUNT,
#                 PAYLOADJSON,
#                 SUBMISSIONSTATUS

#             FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)

#             WHERE SUBMISSIONSTATUS = 1
#         """)

#         portal_rows = cur.fetchall()

#     # ========================================================
#     # UNDER REVIEW
#     # ========================================================
#     under_review = 0
#     checked_cases = set()

#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         for r in portal_rows:

#             rfq_case_id = normalize(r[0])
#             rfq_id = normalize(r[1])
#             vendor = normalize(r[2])

#             payload_json = r[3]

#             if rfq_case_id in checked_cases:
#                 continue

#             if not payload_json:
#                 continue

#             try:
#                 payload = json.loads(payload_json)
#             except Exception:
#                 continue

#             valid_items = set()

#             for item in payload.get("Item", []):

#                 status = str(
#                     item.get("lineStatus")
#                 ).lower()

#                 if status in ["true", "1"]:

#                     valid_items.add(
#                         normalize(
#                             item.get("itemNumber")
#                         )
#                     )

#             if not valid_items:
#                 continue

#             cur.execute("""
#                 SELECT ITEMID, STATUS

#                 FROM PURCHRFQLINE WITH (NOLOCK)

#                 WHERE RFQID = ?
#                   AND DATAAREAID = 'hi-q'
#             """, rfq_id)

#             lines = cur.fetchall()

#             for line in lines:

#                 item = normalize(line[0])
#                 status = line[1]

#                 if item in valid_items and status < 3:

#                     under_review += 1
#                     checked_cases.add(rfq_case_id)

#                     break

#     # ========================================================
#     # CLOSED CASES
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT DISTINCT RFQCASEID

#             FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)

#             WHERE SUBMISSIONSTATUS = 1
#         """)

#         submitted_cases = {
#             normalize(r[0])
#             for r in cur.fetchall()
#         }

#     closed_cases = len(
#         d365_closed_cases.intersection(submitted_cases)
#     )

#     # ========================================================
#     # FINAL
#     # ========================================================
#     return {
#         "total_active_cases": on_bidding + under_review,

#         "on_bidding": on_bidding,

#         "expiring_soon": expiring_soon,

#         "under_review": under_review,

#         "total_rfq_cases": total_rfq_cases,

#         "closed_cases": closed_cases
#     }


# async def get_rfq_kpi():
#     return await run_in_threadpool(get_rfq_kpi_sync)


# # ============================================================
# # VENDOR DASHBOARD KPI
# # ============================================================
# def get_vendordashboard_kpi_sync():

#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         # ====================================================
#         # TOTAL VENDORS
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(DISTINCT ACCOUNTNUM)

#             FROM VENDTABLE WITH (NOLOCK)
#         """)

#         total_vendors = cur.fetchone()[0] or 0

#         # ====================================================
#         # TOTAL MATERIALS
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(DISTINCT ITEMID)

#             FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)
#         """)

#         total_materials = cur.fetchone()[0] or 0

#         # ====================================================
#         # UPCOMING EXPIRY
#         # ====================================================
#         cur.execute("""
#             SELECT COUNT(*)

#             FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)

#             WHERE VALIDTO BETWEEN
#                 GETUTCDATE()
#                 AND DATEADD(DAY, 30, GETUTCDATE())
#         """)

#         upcoming_expiry = cur.fetchone()[0] or 0

#         # ====================================================
#         # EXPIRY DETAILS
#         # ====================================================
#         cur.execute("""
#             SELECT
#                 ITEMID,
#                 PDSAPPROVEDVENDOR,
#                 VALIDFROM,
#                 VALIDTO

#             FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)

#             WHERE VALIDTO BETWEEN
#                 GETUTCDATE()
#                 AND DATEADD(DAY, 30, GETUTCDATE())

#             ORDER BY VALIDTO
#         """)

#         expiry_data = [
#             {
#                 "item": r[0],
#                 "vendor": r[1],
#                 "valid_from": r[2],
#                 "valid_to": r[3]
#             }
#             for r in cur.fetchall()
#         ]

#     return {
#         "total_vendors": total_vendors,

#         "total_materials_mapped": total_materials,

#         "upcoming_expiry": upcoming_expiry,

#         "expiry_details": expiry_data
#     }


# async def get_vendordashboard_kpi():
#     return await run_in_threadpool(
#         get_vendordashboard_kpi_sync
#     )

# # from app.db.base import get_connection
# # import json
# # from fastapi.concurrency import run_in_threadpool
# # def get_rfq_kpi_sync():
# #     def normalize(val):
# #         return str(val or "").replace(" ", "").strip().upper()
# #     with get_connection() as conn:
# #         cur = conn.cursor()
# #         cur.execute("""
# #             SELECT COUNT(DISTINCT C.RFQCASEID)
# #             FROM PurchRFQCaseTable C
# #             INNER JOIN PURCHRFQTABLE T
# #                 ON T.RFQCASEID = C.RFQCASEID
# #                 AND T.DATAAREAID = 'hi-q'
# #             WHERE 
# #                 T.RFQID IS NOT NULL
# #                 AND LTRIM(RTRIM(T.RFQID)) <> ''
# #         """)
# #         total_rfq_cases = cur.fetchone()[0]
# #         # =========================
# #         # TOTAL ACTIVE CASES
# #         # =========================
# #         cur.execute("""
# #             SELECT COUNT(*)
# #             FROM PurchRFQCaseTable
# #             WHERE EXPIRYDATETIME >= GETUTCDATE()
# #         """)
# #         total_active_cases = cur.fetchone()[0]
 
# #         # =========================
# #         # ON BIDDING
# #         # =========================
# #         cur.execute("""
# #     SELECT COUNT(*)
# #     FROM (
# #         SELECT T.RFQCASEID
# #         FROM PURCHRFQTABLE T WITH (NOLOCK)
# #         WHERE T.DATAAREAID = 'hi-q'
# #           AND T.EXPIRYDATETIME >= GETUTCDATE()
# #         GROUP BY T.RFQCASEID
# #         HAVING
# #             MAX(T.EXPIRYDATETIME) >= GETUTCDATE()
# #             AND NOT EXISTS (
# #                 SELECT 1
# #                 FROM PURCHRFQLINE PL
# #                 JOIN PURCHRFQTABLE T2
# #                     ON T2.RFQID = PL.RFQID
# #                 WHERE T2.RFQCASEID = T.RFQCASEID
# #                   AND PL.STATUS = 4
# #                   AND PL.DATAAREAID = 'hi-q'
# #             )
# #     ) X
# # """)
# #         on_bidding = cur.fetchone()[0]
# #         # cur.execute("""
# #         #     SELECT COUNT(*)
# #         #     FROM PurchRFQCaseTable C
# #         #     WHERE C.EXPIRYDATETIME >= GETUTCDATE()
# #         #     AND  EXISTS (
# #         #         SELECT 1
# #         #         FROM HIQ_VENDORRFQREPLIES V
# #         #         WHERE V.RFQ_CASE_ID = C.RFQCASEID
# #         #           AND V.SUBMISSION_STATUS = 0
# #         #     )
# #         # """)
# #         # on_bidding = cur.fetchone()[0]
 
# #         # =========================
# #         # EXPIRING SOON (NEXT 3 DAYS)
# #         # =========================
# #         # cur.execute("""
# #         #     SELECT COUNT(*)
# #         #     FROM PurchRFQCaseTable
# #         #     WHERE EXPIRYDATETIME BETWEEN GETUTCDATE() AND DATEADD(DAY, 3, GETUTCDATE())
# #         # """)

# #         cur.execute("""
# #   SELECT COUNT(DISTINCT C.RFQCASEID)
# # FROM PurchRFQCaseTable C

# # INNER JOIN PURCHRFQTABLE T
# #     ON T.RFQCASEID = C.RFQCASEID
# #     AND T.DATAAREAID = 'hi-q'

# # WHERE 
# #     C.EXPIRYDATETIME BETWEEN GETUTCDATE() AND DATEADD(DAY, 3, GETUTCDATE())

# #     -- IMPORTANT FIX
# #     AND T.RFQID IS NOT NULL
# #     AND LTRIM(RTRIM(T.RFQID)) <> ''

# #     -- EXCLUDE ACCEPTED
# #     AND NOT EXISTS (
# #         SELECT 1
# #         FROM PURCHRFQLINE PL
# #         WHERE PL.RFQID = T.RFQID
# #           AND PL.STATUS = 4
# #           AND PL.DATAAREAID = 'hi-q')
# # """)
# #         expiring_soon = cur.fetchone()[0]
        
# #         # =========================
# #         # UNDER REVIEW
# #         # =========================
# #          # =========================
# #         # UNDER REVIEW (FIXED)
# #         # =========================
# #         under_review = 0
# #         checked_cases = set()

# #         cur.execute("""
# #             SELECT DISTINCT RFQ_CASE_ID, RFQ_ID, VENDOR_ACCOUNT
# #             FROM HIQ_VENDORRFQREPLIES
# #             WHERE SUBMISSION_STATUS = 1
# #         """)

# #         rows = cur.fetchall()

# #         for r in rows:
# #             rfq_case_id = r[0]
# #             rfq_id = r[1]
# #             vendor = r[2]

# #             if rfq_case_id in checked_cases:
# #                 continue

# #             # GET PAYLOAD
# #             cur.execute("""
# #                 SELECT TOP 1 PAYLOAD_JSON
# #                 FROM HIQ_VENDORRFQREPLIES
# #                 WHERE RFQ_ID = ?
# #                   AND VENDOR_ACCOUNT = ?
# #                   AND SUBMISSION_STATUS = 1
# #                 ORDER BY CREATED_AT DESC
# #             """, (rfq_id, vendor))

# #             row = cur.fetchone()
# #             if not row or not row[0]:
# #                 continue

# #             try:
# #                 payload = json.loads(row[0])
# #             except:
# #                 continue

# #             valid_items = set()

# #             for item in payload.get("Item", []):
# #                 status = str(item.get("lineStatus")).lower()

# #                 if status in ["true", "1"]:
# #                     valid_items.add(normalize(item.get("itemNumber")))

# #             if not valid_items:
# #                 continue

# #             # CHECK D365
# #             cur.execute("""
# #                 SELECT ITEMID, STATUS
# #                 FROM PURCHRFQLINE
# #                 WHERE RFQID = ?
# #                   AND DATAAREAID = 'hi-q'
# #             """, (rfq_id,))

# #             for l in cur.fetchall():
# #                 item = normalize(l[0])
# #                 status = l[1]

# #                 if item in valid_items and status < 3:
# #                     under_review += 1
# #                     checked_cases.add(rfq_case_id)
# #                     break

        

# #         # CLOSED RFQ CASES
# #         # =========================
# #         cur.execute("""
# #            SELECT COUNT(DISTINCT T.RFQCASEID)
# # FROM PURCHRFQTABLE T
# # WHERE T.DATAAREAID = 'hi-q'

# # -- must have submission
# # AND EXISTS (
# #     SELECT 1
# #     FROM HIQ_VENDORRFQREPLIES R
# #     WHERE R.RFQ_CASE_ID = T.RFQCASEID
# #       AND R.SUBMISSION_STATUS = 1
# # )

# # -- must have final decision
# # AND EXISTS (
# #     SELECT 1
# #     FROM PURCHRFQREPLYLINE RL
# #     INNER JOIN PURCHRFQLINE PL
# #         ON PL.RECID = RL.RFQLINERECID
# #     INNER JOIN PURCHRFQTABLE T2
# #         ON T2.RFQID = RL.RFQID
# #     WHERE T2.RFQCASEID = T.RFQCASEID
# #       AND PL.STATUS >= 3
# #       AND PL.DATAAREAID = 'hi-q'
# # )
# #         """)
# #         closed_cases = cur.fetchone()[0]

# #     return {
# #         "total_active_cases": on_bidding+under_review,
# #         "on_bidding": on_bidding,
# #         "expiring_soon": expiring_soon,
# #         "under_review": under_review,
# #         "total_rfq_cases":total_rfq_cases,
# #         "closed_cases":closed_cases
# #     }
# # async def get_rfq_kpi():
# #     return await run_in_threadpool(get_rfq_kpi_sync)

# # def get_vendordashboard_kpi_sync():
# #     with get_connection() as conn:
# #         cur = conn.cursor()

# #         # 1. Total Vendors
# #         cur.execute("""
# #             SELECT COUNT(DISTINCT ACCOUNTNUM) FROM VENDTABLE
# #         """)
# #         total_vendors = cur.fetchone()[0]

# #         # 2. Total Materials Mapped
# #         cur.execute("""
# #              select count(distinct ITEMID) from PDSAPPROVEDVENDORLIST
# #         """)
# #         total_materials = cur.fetchone()[0]

# #         # 3. Upcoming Expiry
# #         cur.execute("""
# #             SELECT COUNT(*) 
# #             FROM PDSAPPROVEDVENDORLIST
# #             WHERE VALIDTO BETWEEN GETUTCDATE() AND DATEADD(DAY, 30, GETUTCDATE())
# #         """)
# #         upcoming_expiry = cur.fetchone()[0]

# #         # 4. Expiry Details
# #         cur.execute("""
# #             SELECT 
# #                 ITEMID,
# #                 PDSAPPROVEDVENDOR,
# #                 VALIDFROM,
# #                 VALIDTO
# #             FROM PDSAPPROVEDVENDORLIST
# #             WHERE VALIDTO BETWEEN GETUTCDATE() AND DATEADD(DAY, 30, GETUTCDATE())
# #             ORDER BY VALIDTO
# #         """)

# #         expiry_data = [
# #             {
# #                 "item": r[0],
# #                 "vendor": r[1],
# #                 "valid_from": r[2],
# #                 "valid_to": r[3]
# #             }
# #             for r in cur.fetchall()
# #         ]

# #     return {
# #         "total_vendors": total_vendors,
# #         "total_materials_mapped": total_materials,
# #         "upcoming_expiry": upcoming_expiry,
# #         "expiry_details": expiry_data
# #     }

# # async def get_vendordashboard_kpi():
# #     return await run_in_threadpool(get_vendordashboard_kpi_sync)