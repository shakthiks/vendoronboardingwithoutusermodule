from fastapi.concurrency import run_in_threadpool

from app.db.base import (
    get_d365_connection
)

from app.utils.date_utils import (
    format_ist_date_only
)

from app.utils.remainingdate import (
    calculate_days_left
)


# ============================================================
# ON BIDDING CASES
# ============================================================
def fetch_on_bidding_cases_sync():

    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                T.RFQCASEID,

                MAX(C.NAME) AS CASE_NAME,

                MAX(C.CREATEDDATETIME) AS CREATED_DATE,

                MAX(C.EXPIRYDATETIME) AS EXPIRY_DATE,

                COUNT(DISTINCT T.VENDACCOUNT) AS VENDOR_COUNT,

                CASE
                WHEN CAST(DATEADD(MINUTE,330,MAX(C.EXPIRYDATETIME)) AS DATE)
                    BETWEEN CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)
                    AND CAST(DATEADD(DAY,3,DATEADD(MINUTE,330,GETUTCDATE())) AS DATE)
                    THEN 1
                    ELSE 0
                END AS EXPIRY_STATUS

            FROM PURCHRFQTABLE T

            INNER JOIN PurchRFQCaseTable C
                ON C.RFQCASEID = T.RFQCASEID

            WHERE T.DATAAREAID = 'hi-q'

            GROUP BY T.RFQCASEID

           HAVING
            CAST(DATEADD(MINUTE,330,MAX(C.EXPIRYDATETIME)) AS DATE)
            >= CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)

                AND NOT EXISTS (
                    SELECT 1

                    FROM PURCHRFQLINE PL

                    INNER JOIN PURCHRFQTABLE T2
                        ON T2.RFQID = PL.RFQID

                    WHERE T2.RFQCASEID = T.RFQCASEID
                      AND PL.STATUS = 4
                      AND PL.DATAAREAID = 'hi-q'
                )

            ORDER BY MAX(C.CREATEDDATETIME) DESC
        """)

        rows = cur.fetchall()

    return [
        {
            "rfq_case_id": r[0],

            "case_name": r[1],

            "created_date":
                format_ist_date_only(r[2]),

            "expiry_date":
                format_ist_date_only(r[3]),

            "vendor_count": r[4],

            "expiry_status": bool(r[5]),

            "status": "On Bidding"
        }
        for r in rows
    ]


async def fetch_on_bidding_cases():
    return await run_in_threadpool(
        fetch_on_bidding_cases_sync
    )


# ============================================================
# VENDOR PROFILE
# ============================================================
def fetch_vendor_profile_sync(
    vendor_account: str
):

    profile = {
        "email": None,
        "phone": None,
        "address": None,
        "name": None
    }

    with get_d365_connection() as conn:

        cursor = conn.cursor()

        # ====================================================
        # EMAIL + PHONE
        # ====================================================
        cursor.execute("""
            SELECT TYPE, LOCATOR

            FROM HIQ_vendorELECTRONICADDRESSVIEW
            WITH (NOLOCK)

            WHERE ACCOUNTNUM = ?
              AND ISPRIMARY1 = 1
        """, vendor_account)

        for row in cursor.fetchall():

            if row.TYPE == 2:
                profile["email"] = row.LOCATOR

            elif row.TYPE == 1:
                profile["phone"] = row.LOCATOR

        # ====================================================
        # ADDRESS
        # ====================================================
        cursor.execute("""
            SELECT TOP 1
                ADDRESS,
                NAME

            FROM HIQ_vendorPostalADDRESSVIEW
            WITH (NOLOCK)

            WHERE ACCOUNTNUM = ?
              AND ISPRIMARY = 1
        """, vendor_account)

        row = cursor.fetchone()

        if row:

            profile["address"] = row.ADDRESS

            profile["name"] = row.NAME

    return profile


async def fetch_vendor_profile(
    vendor_account: str
):
    return await run_in_threadpool(
        fetch_vendor_profile_sync,
        vendor_account
    )


# ============================================================
# VENDOR BID RFQS
# ============================================================
def get_vendor_bidrfqs_sync(
    rfq_case_id: str
):

    with get_d365_connection() as conn:

        cur = conn.cursor()

        # ====================================================
        # CASE HEADER
        # ====================================================
        cur.execute("""
            SELECT TOP 1
                L.RFQCASEID,
                L.NAME,
                L.CREATEDDATETIME,
                L.EXPIRYDATETIME,
                L.DELIVERYDATE

            FROM PurchRFQCaseTable L
            WITH (NOLOCK)

            WHERE L.RFQCASEID = ?
        """, (rfq_case_id,))

        case = cur.fetchone()

        if not case:
            return {}

        # ====================================================
        # VENDORS
        # ====================================================
        cur.execute("""
            SELECT
                T.RFQID,
                T.VENDACCOUNT,
                T.DLVMODE,
                T.DLVTERM,
                T.PAYMENT

            FROM PURCHRFQTABLE T
            WITH (NOLOCK)

            WHERE T.RFQCASEID = ?
              AND T.DATAAREAID = 'hi-q'
        """, (rfq_case_id,))

        vendors = cur.fetchall()

        result = []

        for v in vendors:

            rfq_id = v[0]

            vendor_account = v[1]

            profile = fetch_vendor_profile_sync(
                vendor_account
            )

            # =================================================
            # VENDOR REMARKS
            # =================================================
            cur.execute("""
                SELECT
                    PL.ITEMID,
                    RL.HIQ_COMMENTS

                FROM PURCHRFQREPLYLINE RL

                INNER JOIN PURCHRFQLINE PL
                    ON PL.RECID = RL.RFQLINERECID

                WHERE RL.RFQID = ?
                  AND RL.DATAAREAID = 'hi-q'
            """, (rfq_id,))

            remarks_map = {
                r[0]: r[1]
                for r in cur.fetchall()
            }

            # =================================================
            # REPLY HEADER
            # =================================================
            cur.execute("""
                SELECT TOP 1
                    DELIVERYDATE,
                    DLVMODE,
                    DLVTERM,
                    CURRENCYCODE

                FROM PURCHRFQREPLYTABLE

                WHERE RFQID = ?
                  AND DATAAREAID = 'hi-q'
            """, (rfq_id,))

            reply = cur.fetchone()

            vendor_reply = {

                "expected_delivery_date":
                    format_ist_date_only(reply[0])
                    if reply and reply[0]
                    else None,

                "mode_of_delivery":
                    reply[1] if reply else None,

                "delivery_term":
                    reply[2] if reply else None,

                "currency":
                    reply[3] if reply else None
            }

            # =================================================
            # FILTERED ITEMS
            # =================================================
            cur.execute("""
                SELECT
                    RL.LINENUM,
                    RL.ITEMID,
                    IT.NAMEALIAS,
                    RL.QTYORDERED,
                    RL.PURCHUNIT,
                    RL.HIQ_TARGETPRICE,
                    RL.HIQ_COMMENTS,
                    RL.DELIVERYDATE

                FROM PURCHRFQLINE RL
                WITH (NOLOCK)

                LEFT JOIN INVENTTABLE IT
                    ON IT.ITEMID = RL.ITEMID

                INNER JOIN PDSAPPROVEDVENDORLIST AVL
                    ON UPPER(
                        LTRIM(RTRIM(AVL.ITEMID))
                    ) =
                    UPPER(
                        LTRIM(RTRIM(RL.ITEMID))
                    )

                   AND AVL.PDSAPPROVEDVENDOR = ?

                   AND AVL.DATAAREAID = 'hi-q'

                   AND AVL.VALIDFROM <= GETUTCDATE()

                   AND AVL.VALIDTO >= GETUTCDATE()

                WHERE RL.RFQID = ?

                ORDER BY RL.LINENUM
            """, (vendor_account, rfq_id))

            line_items = []

            for l in cur.fetchall():

                material_code = l[1]

                line_items.append({

                    "line_no":
                        int(l[0]),

                    "material_code":
                        material_code,

                    "material_description":
                        l[2] or "-",

                    "quantity":
                        float(l[3] or 0),

                    "uom":
                        l[4],

                    "target_price":
                        float(l[5] or 0),

                    "comments":
                        l[6] or " ",

                    "line_delivery_date":
                        format_ist_date_only(l[7]),

                    "vendor_remarks":
                        remarks_map.get(
                            material_code,
                            " "
                        )
                })

            result.append({

                "rfq_id":
                    rfq_id,

                "vendor_name":
                    profile["name"],

                "vendor_account":
                    vendor_account,

                "location":
                    profile["address"],

                "mode_of_delivery":
                    v[2],

                "delivery_term":
                    v[3],

                "payment_term":
                    v[4],

                "vendor_reply":
                    vendor_reply,

                "vendor_info": {

                    "contact_person":
                        profile["name"],

                    "mobile":
                        profile["phone"],

                    "email":
                        profile["email"],

                    "location":
                        profile["address"]
                },

                "hiq_requirement":
                    line_items
            })

    return {

        "case": {

            "rfq_case_id":
                case[0],

            "case_name":
                case[1],

            "created_date":
                format_ist_date_only(case[2]),

            "expiry_date":
                format_ist_date_only(case[3]),

            "delivery_date":
                format_ist_date_only(case[4]),

            "time_remaining":
                calculate_days_left(case[3]),

            "status":
                "On Bidding"
        },

        "vendors":
            result
    }


async def get_vendor_bidrfqs(
    rfq_case_id: str
):
    return await run_in_threadpool(
        get_vendor_bidrfqs_sync,
        rfq_case_id
    )


# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# from fastapi.concurrency import run_in_threadpool
# def fetch_on_bidding_cases_sync():
#     with get_connection() as conn:
#         cur = conn.cursor()

#         cur.execute("""
#             SELECT
#                 T.RFQCASEID,
#                 MAX(C.NAME)              AS CASE_NAME,
#                 MAX(C.CREATEDDATETIME)  AS CREATED_DATE,
#                 MAX(C.EXPIRYDATETIME)   AS EXPIRY_DATE,
#                 COUNT(DISTINCT T.VENDACCOUNT) AS VENDOR_COUNT,  
#                 CASE 
#                 WHEN CAST(MAX(C.EXPIRYDATETIME) AS DATE) 
#                     BETWEEN CAST(GETDATE() AS DATE) 
#                     AND CAST(DATEADD(DAY, 3, GETDATE()) AS DATE)
#                 THEN 1
#                 ELSE 0
#             END AS EXPIRY_STATUS

#             FROM PURCHRFQTABLE T

#             INNER JOIN PurchRFQCaseTable C
#                 ON C.RFQCASEID = T.RFQCASEID

#             WHERE T.DATAAREAID = 'hi-q'

#             GROUP BY T.RFQCASEID

#             HAVING 
#                 CAST(MAX(C.EXPIRYDATETIME) AS DATE) >= CAST(GETDATE() AS DATE)

#                 --MAX(C.EXPIRYDATETIME) >= GETUTCDATE()

#                 AND NOT EXISTS (
#                     SELECT 1
#                     FROM PURCHRFQLINE PL
#                     INNER JOIN PURCHRFQTABLE T2
#                         ON T2.RFQID = PL.RFQID
#                     WHERE T2.RFQCASEID = T.RFQCASEID
#                       AND PL.STATUS = 4
#                       AND PL.DATAAREAID = 'hi-q'
#                 )

#             ORDER BY MAX(C.CREATEDDATETIME) DESC
#         """)

#         rows = cur.fetchall()

#         return [
#             {
#                 "rfq_case_id": r[0],
#                 "case_name": r[1],
#                 "created_date": format_ist_date_only(r[2]),
#                 "expiry_date": format_ist_date_only(r[3]),
#                 "vendor_count": r[4],
#                 "expiry_status": bool(r[5]), 
#                 "status": "On Bidding"
#             }
#             for r in rows
#         ]

# async def fetch_on_bidding_cases():
#     return await run_in_threadpool(fetch_on_bidding_cases_sync)
# def fetch_vendor_profile_sync(vendor_account: str):

#     profile = {
#         "email": None,
#         "phone": None,
#         "address": None,
#         "name":None
#     }

#     with get_connection() as conn:
#         cursor = conn.cursor()

#         # 🔹 Fetch Email + Phone
#         electronic_query = """
#             SELECT TYPE, LOCATOR
#             FROM HIQ_vendorELECTRONICADDRESSVIEW WITH (NOLOCK)
#             WHERE ACCOUNTNUM = ?
#             AND ISPRIMARY1 = 1
#         """

#         cursor.execute(electronic_query, vendor_account)
#         electronic_rows = cursor.fetchall()

#         for row in electronic_rows:
#             type = row.TYPE
#             locator = row.LOCATOR
 
#             if type == 2:
#                 profile["email"] = locator
#             elif type == 1:
#                 profile["phone"] = locator

#         # 🔹 Fetch Address
#         address_query = """
#             SELECT TOP 1 ADDRESS,NAME
#             FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)
#             WHERE ACCOUNTNUM = ?
#             AND ISPRIMARY = 1
#         """

#         cursor.execute(address_query, vendor_account)
#         address_row = cursor.fetchone()

#         if address_row:
#             profile["address"] = address_row.ADDRESS
#             profile["name"]=address_row.NAME
#             # profile["city"]=address_row.city
#         cursor.close()

#     return profile 
# async def fetch_vendor_profile(vendor_account: str):
#     return await run_in_threadpool(fetch_vendor_profile_sync, vendor_account)
# def get_vendor_bidrfqs_sync(rfq_case_id: str):

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # =========================
#         # CASE HEADER
#         # =========================
#         cur.execute("""
#             SELECT TOP 1
#                 L.RFQCASEID,
#                 L.NAME,
#                 L.CREATEDDATETIME,
#                 L.EXPIRYDATETIME,
#                 L.DELIVERYDATE
#             FROM PurchRFQCaseTable L WITH (NOLOCK)
#             WHERE L.RFQCASEID = ?
#         """, (rfq_case_id,))

#         case = cur.fetchone()
#         if not case:
#             return {}

#         # =========================
#         # VENDORS
#         # =========================
#         cur.execute("""
#             SELECT
#                 T.RFQID,
#                 T.VENDACCOUNT,
#                 T.DLVMODE,
#                 T.DLVTERM,
#                 T.PAYMENT
#             FROM PURCHRFQTABLE T WITH (NOLOCK)
#             WHERE T.RFQCASEID = ?
#               AND T.DATAAREAID = 'hi-q'
#         """, (rfq_case_id,))

#         vendors = cur.fetchall()
#         result = []

#         for v in vendors:
#             rfq_id = v[0]
#             vendor_account = v[1]

#             profile = fetch_vendor_profile_sync(vendor_account)

#             # =========================
#             # Vendor Remarks
#             # =========================
#             cur.execute("""
#                 SELECT
#                     PL.ITEMID,
#                     RL.HIQ_COMMENTS
#                 FROM PURCHRFQREPLYLINE RL

#                 INNER JOIN PURCHRFQLINE PL
#                     ON PL.RECID = RL.RFQLINERECID

#                 WHERE RL.RFQID = ?
#                 AND RL.DATAAREAID = 'hi-q'
#             """, (rfq_id,))

#             remarks_map = {r[0]: r[1] for r in cur.fetchall()}
#             # =========================
#             # VENDOR REPLY HEADER (ADD HERE)
#             # =========================
#             cur.execute("""
#                 SELECT TOP 1
#                     DELIVERYDATE,
#                     DLVMODE,
#                     DLVTERM,
#                     CURRENCYCODE
#                 FROM PURCHRFQREPLYTABLE
#                 WHERE RFQID = ?
#                 --AND VENDACCOUNT = ?
#                 AND DATAAREAID = 'hi-q'
#             """, (rfq_id))

#             reply = cur.fetchone()

#             vendor_reply = {
#                 "expected_delivery_date": format_ist_date_only(reply[0]) if reply and reply[0] else None,
#                 "mode_of_delivery": reply[1] if reply else None,
#                 "delivery_term": reply[2] if reply else None,
#                 "currency":reply[3] if reply else None
#             }
#             # =========================
#             # FILTERED ITEMS (FIXED)
#             # =========================
#             cur.execute("""
#                 SELECT
#                     RL.LINENUM,
#                     RL.ITEMID,
#                     IT.NAMEALIAS,
#                     RL.QTYORDERED,
#                     RL.PURCHUNIT,
#                     RL.HIQ_TARGETPRICE,
#                     RL.HIQ_COMMENTS,
#                     RL.DELIVERYDATE AS LINE_DELIVERY_DATE

#                 FROM PURCHRFQLINE RL WITH (NOLOCK)

#                 LEFT JOIN INVENTTABLE IT WITH (NOLOCK)
#                     ON IT.ITEMID = RL.ITEMID

#                 INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#                     ON UPPER(LTRIM(RTRIM(AVL.ITEMID))) = UPPER(LTRIM(RTRIM(RL.ITEMID)))
#                     --ON AVL.ITEMID = RL.ITEMID
#                     AND AVL.PDSAPPROVEDVENDOR = ?
#                     AND AVL.DATAAREAID = 'hi-q'
#                     AND AVL.VALIDFROM <= GETUTCDATE()
#                     AND AVL.VALIDTO >= GETUTCDATE()

#                 WHERE RL.RFQID = ?
#                 ORDER BY RL.LINENUM
#             """, (vendor_account, rfq_id))

#             line_items = []

#             for l in cur.fetchall():
#                 material_code = l[1]

#                 line_items.append({
#                     "line_no": int(l[0]),
#                     "material_code": material_code,
#                     "material_description": l[2] or "-",
#                     "quantity": float(l[3] or 0),
#                     "uom": l[4],
#                     "target_price": float(l[5] or 0),
#                     "comments": l[6] or " ",
#                     "line_delivery_date": format_ist_date_only(l[7]),

#                     #REMARKS
#                     "vendor_remarks": remarks_map.get(material_code, " ")
#                 })

#             result.append({
#                 "rfq_id": rfq_id,
#                 "vendor_name": profile["name"],
#                 "vendor_account": vendor_account,
#                 "location": profile["address"],
#                 "mode_of_delivery": v[2],
#                 "delivery_term": v[3],
#                 "payment_term": v[4],
#                 "vendor_reply": vendor_reply,
#                 "vendor_info": {
#                     "contact_person": profile["name"],
#                     "mobile": profile["phone"],
#                     "email": profile["email"],
#                     "location": profile["address"]
#                 },

#                 "hiq_requirement": line_items
#             })

#         return {
#             "case": {
#                 "rfq_case_id": case[0],
#                 "case_name": case[1],
#                 "created_date": format_ist_date_only(case[2]),
#                 "expiry_date": format_ist_date_only(case[3]),
#                 "delivery_date": format_ist_date_only(case[4]),
#                 "time_remaining": calculate_days_left(case[3]),
#                 "status": "On Bidding"
#             },
#             "vendors": result
#         }
# async def get_vendor_bidrfqs(rfq_case_id: str):
#     return await run_in_threadpool(get_vendor_bidrfqs_sync, rfq_case_id)