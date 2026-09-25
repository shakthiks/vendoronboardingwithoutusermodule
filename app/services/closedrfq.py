import json

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

RFQ_REPLIES_TABLE = (
    f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
)


# ============================================================
# HELPERS
# ============================================================
def normalize(val):
    return str(val or "").strip().upper()


# ============================================================
# CLOSED CASES
# ============================================================
def get_closed_cases_sync():

    # ========================================================
    # PORTAL SUBMISSIONS
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT DISTINCT RFQCASEID

            FROM {RFQ_REPLIES_TABLE}
            WITH (NOLOCK)

            WHERE SUBMISSIONSTATUS = 1
        """)

        submitted_cases = {
            normalize(r[0])
            for r in cur.fetchall()
        }

    # ========================================================
    # D365 CLOSED CASES
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                T.RFQCASEID,
                MAX(T.NAME),
                MAX(T.CREATEDDATETIME),
                MAX(T.EXPIRYDATETIME),
                COUNT(DISTINCT T.VENDACCOUNT)

            FROM PURCHRFQTABLE T

            WHERE T.DATAAREAID = 'hi-q'

            GROUP BY T.RFQCASEID

            HAVING EXISTS (

                SELECT 1

                FROM PURCHRFQREPLYLINE RL
                WITH (NOLOCK)

                INNER JOIN PURCHRFQLINE PL
                WITH (NOLOCK)

                    ON PL.RECID = RL.RFQLINERECID

                   AND PL.DATAAREAID = 'hi-q'

                INNER JOIN PURCHRFQTABLE T2

                    ON T2.RFQID = RL.RFQID

                WHERE T2.RFQCASEID = T.RFQCASEID
                  AND RL.DATAAREAID = 'hi-q'
                  AND PL.STATUS >= 3

            )

            ORDER BY MAX(T.CREATEDDATETIME) DESC
        """)

        rows = cur.fetchall()

    result = []

    for r in rows:

        rfq_case_id = normalize(r[0])

        if rfq_case_id not in submitted_cases:
            continue

        result.append({

            "rfq_case_id": r[0],

            "case_name": r[1],

            "created_date":
                format_ist_date_only(r[2]),

            "expiry_date":
                format_ist_date_only(r[3]),

            "vendor_count": r[4],

            "status": "Closed"
        })

    return result


async def get_closed_cases():
    return await run_in_threadpool(
        get_closed_cases_sync
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
        "name": None,
        "city": None
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
                NAME,
                CITY

            FROM HIQ_vendorPostalADDRESSVIEW
            WITH (NOLOCK)

            WHERE ACCOUNTNUM = ?
              AND ISPRIMARY = 1
        """, vendor_account)

        row = cursor.fetchone()

        if row:

            profile["address"] = row.ADDRESS

            profile["name"] = row.NAME

            profile["city"] = row.CITY

    return profile


async def fetch_vendor_profile(
    vendor_account: str
):
    return await run_in_threadpool(
        fetch_vendor_profile_sync,
        vendor_account
    )


# ============================================================
# CLOSED RFQ DETAIL
# ============================================================
def get_vendor_closed_rfqs_sync(
    rfq_case_id: str
):

    # ========================================================
    # STEP 1 - PORTAL PAYLOADS
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                RFQID,
                VENDORACCOUNT,
                PAYLOADJSON

            FROM {RFQ_REPLIES_TABLE}

            WHERE RFQCASEID = ?
              AND SUBMISSIONSTATUS = 1
        """, (rfq_case_id,))

        portal_rows = cur.fetchall()

    payload_map = {}

    for row in portal_rows:

        key = (
            normalize(row[0]),
            normalize(row[1])
        )

        payload_map[key] = row[2]

    # ========================================================
    # STEP 2 - D365
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        # ====================================================
        # CASE HEADER
        # ====================================================
        cur.execute("""
            SELECT TOP 1
                RFQCASEID,
                NAME,
                CREATEDDATETIME,
                EXPIRYDATETIME,
                DELIVERYDATE

            FROM PurchRFQCaseTable

            WHERE RFQCASEID = ?
        """, (rfq_case_id,))

        case = cur.fetchone()

        if not case:
            return {}

        # ====================================================
        # VENDORS
        # ====================================================
        cur.execute("""
            SELECT
                RFQID,
                VENDACCOUNT,
                DLVMODE,
                DLVTERM,
                PAYMENT

            FROM PURCHRFQTABLE

            WHERE RFQCASEID = ?
              AND DATAAREAID = 'hi-q'
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
            # VENDOR REPLY HEADER
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
            # PAYLOAD
            # =================================================
            payload_json = payload_map.get(
                (
                    normalize(rfq_id),
                    normalize(vendor_account)
                )
            )

            payload_status_map = {}

            if payload_json:

                try:

                    payload = json.loads(
                        payload_json
                    )

                    for item in payload.get(
                        "Item",
                        []
                    ):

                        payload_status_map[
                            normalize(
                                item.get(
                                    "itemNumber"
                                )
                            )
                        ] = {

                            "unit_price":
                                item.get(
                                    "unitPrice"
                                ),

                            "vendor_comments":
                                item.get(
                                    "vendorComments"
                                ),

                            "line_status":
                                item.get(
                                    "lineStatus"
                                )
                        }

                except Exception:
                    pass

            # =================================================
            # LINE ITEMS
            # =================================================
            cur.execute("""
                SELECT
                    RL.LINENUM,
                    PL.ITEMID,
                    IT.NAMEALIAS,
                    PL.QTYORDERED,
                    RL.PURCHUNIT,

                    RL.PURCHPRICE,
                    RL.LINEAMOUNT,
                    RL.HIQ_COMMENTS,

                    PL.HIQ_TARGETPRICE,

                    PL.DELIVERYDATE
                        AS LINE_DELIVERY_DATE,

                    RL.DELIVERYDATE
                        AS VENDORREPLY_DELIVERY_DATE,

                    CASE PL.STATUS
                        WHEN 3 THEN 'Rejected'
                        WHEN 4 THEN 'Accepted'
                        WHEN 5 THEN 'Cancelled'
                        WHEN 6 THEN 'Declined'
                        ELSE 'Closed'
                    END AS HIQ_DECISION,

                    PL.HIQ_COMMENTS

                FROM PURCHRFQREPLYLINE RL

                INNER JOIN PURCHRFQLINE PL

                    ON PL.RECID
                       = RL.RFQLINERECID

                   AND PL.DATAAREAID = 'hi-q'

                LEFT JOIN INVENTTABLE IT

                    ON IT.ITEMID = PL.ITEMID

                INNER JOIN PDSAPPROVEDVENDORLIST AVL

                    ON AVL.ITEMID = PL.ITEMID

                   AND AVL.PDSAPPROVEDVENDOR = ?

                WHERE RL.RFQID = ?
                  AND RL.DATAAREAID = 'hi-q'
                  AND PL.STATUS >= 3

                ORDER BY RL.LINENUM
            """, (
                vendor_account,
                rfq_id
            ))

            line_items = []

            for l in cur.fetchall():

                item_id = normalize(l[1])

                saved = payload_status_map.get(
                    item_id,
                    {}
                )

                line_items.append({

                    "line_no":
                        int(l[0]),

                    "material_code":
                        l[1],

                    "material_description":
                        l[2],

                    "quantity":
                        float(l[3] or 0),

                    "uom":
                        l[4],

                    "unit_price":
                        float(l[5] or 0),

                    "net_amount":
                        float(l[6] or 0),

                    "vendor_remarks":
                        l[7] or " ",

                    "target_price":
                        float(l[8] or 0),

                    "line_delivery_date":
                        format_ist_date_only(l[9]),

                    "vendorreply_delivery_date":
                        format_ist_date_only(l[10]),

                    "status":
                        l[11],

                    "comments":
                        l[12] or " "
                })

            if line_items:

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

                    "vendor_info": {

                        "contact_person":
                            profile["name"],

                        "mobile":
                            profile["phone"],

                        "email":
                            profile["email"]
                    },

                    "vendor_reply":
                        vendor_reply,

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
                "Closed"
        },

        "vendors":
            result
    }


async def get_vendor_closed_rfqs(
    rfq_case_id: str
):
    return await run_in_threadpool(
        get_vendor_closed_rfqs_sync,
        rfq_case_id
    )


# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# from fastapi.concurrency import run_in_threadpool
# import json 
# def get_closed_cases_sync():
#     with get_connection() as conn:
#         cur = conn.cursor()

#         cur.execute("""
#             SELECT
#                 T.RFQCASEID,
#                 MAX(T.NAME),
#                 MAX(T.CREATEDDATETIME),
#                 MAX(T.EXPIRYDATETIME),
#                 COUNT(DISTINCT T.VENDACCOUNT)

#             FROM PURCHRFQTABLE T

#             WHERE T.DATAAREAID = 'hi-q'

#             GROUP BY T.RFQCASEID

#             HAVING 
#                 --  Submission exists
#                 EXISTS (
#                     SELECT 1
#                     FROM HIQ_VENDORRFQREPLIES R
#                     WHERE R.RFQ_CASE_ID = T.RFQCASEID
#                       AND R.SUBMISSION_STATUS = 1
#                 )

#                 -- Still under review (NOT finalized)
#                 AND EXISTS (
#                     SELECT 1
#                     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
#                     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#                         ON PL.RECID = RL.RFQLINERECID
#                         AND PL.DATAAREAID = 'hi-q'
#                     INNER JOIN PURCHRFQTABLE T2
#                         ON T2.RFQID = RL.RFQID
#                     WHERE T2.RFQCASEID = T.RFQCASEID
#                       AND RL.DATAAREAID = 'hi-q'
#                       AND PL.STATUS >= 3   
#                 )

#             ORDER BY MAX(T.CREATEDDATETIME) DESC
#         """)

#         rows = cur.fetchall()

#         return [
#             {
#                 "rfq_case_id": r[0],
#                 "case_name": r[1],
#                 "created_date": format_ist_date_only(r[2]),
#                 "expiry_date": format_ist_date_only(r[3]),
#                 "vendor_count": r[4],
#                 "status": "Closed"
#             }
#             for r in rows
#         ]
# async def get_closed_cases():
#     return await run_in_threadpool(get_closed_cases_sync)
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
#             SELECT TOP 1 ADDRESS,NAME,city
#             FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)
#             WHERE ACCOUNTNUM = ?
#             AND ISPRIMARY = 1
#         """

#         cursor.execute(address_query, vendor_account)
#         address_row = cursor.fetchone()

#         if address_row:
#             profile["address"] = address_row.ADDRESS
#             profile["name"]=address_row.NAME
#             profile["city"]=address_row.city
#         cursor.close()

#     return profile 
# async def fetch_vendor_profile(vendor_account: str):
#     return await run_in_threadpool(fetch_vendor_profile_sync, vendor_account)

# def get_vendor_closed_rfqs_sync(rfq_case_id: str):

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # =========================
#         # CASE HEADER
#         # =========================
#         cur.execute("""
#             SELECT TOP 1
#                 RFQCASEID,
#                 NAME,
#                 CREATEDDATETIME,
#                 EXPIRYDATETIME,
#                 DELIVERYDATE
#             FROM PurchRFQCaseTable
#             WHERE RFQCASEID = ?
#         """, (rfq_case_id,))

#         case = cur.fetchone()
#         if not case:
#             return {}

#         # =========================
#         # VENDORS
#         # =========================
#         cur.execute("""
#             SELECT RFQID, VENDACCOUNT, DLVMODE, DLVTERM, PAYMENT
#             FROM PURCHRFQTABLE
#             WHERE RFQCASEID = ?
#               AND DATAAREAID = 'hi-q'
#         """, (rfq_case_id,))

#         vendors = cur.fetchall()
#         result = []

#         for v in vendors:
#             rfq_id = v[0]
#             vendor_account = v[1]

#             profile = fetch_vendor_profile_sync(vendor_account)
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
#             # GET SUBMITTED PAYLOAD
#             # =========================
#             cur.execute("""
#                 SELECT TOP 1 PAYLOAD_JSON
#                 FROM HIQ_VENDORRFQREPLIES
#                 WHERE RFQ_ID = ?
#                   AND VENDOR_ACCOUNT = ?
#                   AND SUBMISSION_STATUS = 1
#                 ORDER BY CREATED_AT DESC
#             """, (rfq_id, vendor_account))

#             row = cur.fetchone()

#             payload_map = {}

#             if row and row[0]:
#                 try:
#                     payload = json.loads(row[0])

#                     for item in payload.get("Item", []):
#                         payload_map[item.get("itemNumber")] = {
#                             "unit_price": item.get("unitPrice"),
#                             "vendor_comments": item.get("vendorComments"),
#                             "line_status": item.get("lineStatus")
#                         }
#                 except:
#                     pass

#             # =========================
#             # APPROVED ITEMS ONLY
#             # =========================
#             cur.execute("""
#                 SELECT
#                     RL.LINENUM,
#                     PL.ITEMID,
#                     IT.NAMEALIAS,
#                     PL.QTYORDERED,
#                     RL.PURCHUNIT,

#                     --  FROM REPLY TABLE (vendor data)
#                     RL.PURCHPRICE,
#                     RL.LINEAMOUNT,
#                     RL.HIQ_COMMENTS,

#                     -- FROM RFQ LINE (target)
#                     PL.HIQ_TARGETPRICE,
#                     PL.DELIVERYDATE AS LINE_DELIVERY_DATE,
#                     RL.DELIVERYDATE AS VENDORREPLY_DELIVERY_DATE,
#                     CASE PL.STATUS
#                         WHEN 3 THEN 'Rejected'
#                         WHEN 4 THEN 'Accepted'
#                         WHEN 5 THEN 'Cancelled'
#                         WHEN 6 THEN 'Declined'
#                         ELSE        'Closed'
#                     END                         AS HIQ_DECISIONz,
#                     PL.HIQ_COMMENTS

#                 FROM PURCHRFQREPLYLINE RL   

#                 INNER JOIN PURCHRFQLINE PL
#                     ON PL.RECID = RL.RFQLINERECID
#                     AND PL.DATAAREAID = 'hi-q'

#                 LEFT JOIN INVENTTABLE IT
#                     ON IT.ITEMID = PL.ITEMID

#                 INNER JOIN PDSAPPROVEDVENDORLIST AVL
#                     ON AVL.ITEMID = PL.ITEMID
#                     AND AVL.PDSAPPROVEDVENDOR = ?
#                     --AND AVL.VALIDFROM <= GETDATE()
#                     --AND AVL.VALIDTO >= GETDATE()

#                 WHERE RL.RFQID = ?
#                 AND RL.DATAAREAID = 'hi-q'
#                 AND PL.STATUS >= 3   

#                 ORDER BY RL.LINENUM
#             """, (vendor_account, rfq_id))

#             line_items = []

#             for l in cur.fetchall():
#                 item_id = l[1]
#                 saved = payload_map.get(item_id, {})
#                 line_items.append({
#                     "line_no": int(l[0]),          # LINENUM
#                     "material_code": l[1],         # ITEMID
#                     "material_description": l[2],  # NAMEALIAS
#                     "quantity": float(l[3] or 0),  # QTYORDERED
#                     "uom": l[4],                  # PURCHUNIT

#                     # FROM REPLY TABLE
#                     "unit_price": float(l[5] or 0),      # PURCHPRICE
#                     "net_amount": float(l[6] or 0),      # LINEAMOUNT
#                     "vendor_remarks": l[7] or " ",       # HIQ_COMMENTS

#                     # FROM RFQ LINE
#                     "target_price": float(l[8] or 0),
#                     "line_delivery_date": format_ist_date_only(l[9]),
#                     "vendorreply8_delivery_date": format_ist_date_only(l[10]),

#                     # STATUS
#                     "status": l[11],
#                     "comments":l[12]
#                 })
#                 # line_items.append({
#                 #     "line_no": int(l[0]),
#                 #     "material_code": item_id,
#                 #     "material_description": l[2] or "-",
#                 #     "quantity": float(l[3] or 0),
#                 #     "uom": l[4],
#                 #     "status":l[7],
#                 #     "unit_price": l[8],
#                 #     "vendor_remarks": l[9],
#                 #     "net_amount":l[10],
#                 #     # "line_status": l[10],
#                 #     # #FROM PAYLOAD
#                 #     # "unit_price": saved.get("unit_price", 0),
#                 #     # "vendor_remarks": saved.get("vendor_comments") or "-",
#                 #     # "line_status": saved.get("line_status", False),
                  

#                 #     # HIQ DATA
#                 #     "target_price": float(l[5] or 0),
#                 #     "comments": l[6] or "-"
#                 # })
#             if line_items:
#                 result.append({
#                     "rfq_id": rfq_id,
#                     "vendor_name": profile["name"],
#                     "vendor_account": vendor_account,
#                     "location": profile["address"],
#                     "mode_of_delivery": v[2],
#                     "delivery_term": v[3],
#                     "payment_term": v[4],

#                     "vendor_info": {
#                         "contact_person": profile["name"],
#                         "mobile": profile["phone"],
#                         "email": profile["email"]
#                     },
#                     "vendor_reply": vendor_reply,

#                     "hiq_requirement": line_items
#                 })
                

#         return {
#             "case": {
#                 "rfq_case_id": case[0],
#                 "case_name": case[1],
#                 "created_date": format_ist_date_only(case[2]),
#                 "expiry_date": format_ist_date_only(case[3]),
#                 "delivery_date": format_ist_date_only(case[4]),
#                 "time_remaining": calculate_days_left(case[3]),
#                 "status": "Closed"
#             },
#             "vendors": result
#         }
    

# async def get_vendor_closed_rfqs(rfq_case_id: str):
#     return await run_in_threadpool(get_vendor_closed_rfqs_sync, rfq_case_id)