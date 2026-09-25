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

RFQ_REPLIES_TABLE = f"{SCHEMA}.HIQ_VENDORRFQREPLIES"


# ============================================================
# HELPERS
# ============================================================
def normalize(val):
    return str(val or "").replace(" ", "").strip().upper()


# ============================================================
# UNDER REVIEW CASES
# ============================================================
def _get_underreview_cases_sync():

    final_result = []

    # ========================================================
    # STEP 1 - D365 CASES
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                RFQCASEID,
                MAX(NAME),
                MAX(CREATEDDATETIME),
                MAX(EXPIRYDATETIME)

            FROM PURCHRFQTABLE WITH (NOLOCK)

            WHERE DATAAREAID = 'hi-q'

            GROUP BY RFQCASEID

            ORDER BY MAX(CREATEDDATETIME) DESC
        """)

        cases = cur.fetchall()

    # ========================================================
    # STEP 2 - PORTAL REPLIES
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                RFQCASEID,
                RFQID,
                VENDORACCOUNT,
                PAYLOADJSON

            FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)

            WHERE SUBMISSIONSTATUS = 1
        """)

        portal_rows = cur.fetchall()

    # ========================================================
    # BUILD MAP
    # ========================================================
    portal_map = {}

    for row in portal_rows:

        key = (
            normalize(row[1]),
            normalize(row[2])
        )

        portal_map[key] = row[3]

    # ========================================================
    # PROCESS CASES
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        for c in cases:

            rfq_case_id = c[0]

            # =================================================
            # GET VENDORS
            # =================================================
            cur.execute("""
                SELECT
                    RFQID,
                    VENDACCOUNT

                FROM PURCHRFQTABLE WITH (NOLOCK)

                WHERE RFQCASEID = ?
                  AND DATAAREAID = 'hi-q'
            """, rfq_case_id)

            vendors = cur.fetchall()

            vendor_count = 0
            is_case_under_review = False

            for v in vendors:

                rfq_id = normalize(v[0])
                vendor_account = normalize(v[1])

                payload_json = portal_map.get(
                    (rfq_id, vendor_account)
                )

                if not payload_json:
                    continue

                # =============================================
                # PARSE PAYLOAD
                # =============================================
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

                        item_id = normalize(
                            item.get("itemNumber")
                        )

                        if item_id:
                            valid_items.add(item_id)

                if not valid_items:
                    continue

                # =============================================
                # CHECK D365 STATUS
                # =============================================
                cur.execute("""
                    SELECT
                        ITEMID,
                        STATUS

                    FROM PURCHRFQLINE WITH (NOLOCK)

                    WHERE RFQID = ?
                      AND DATAAREAID = 'hi-q'
                """, rfq_id)

                has_under_review = False

                for line in cur.fetchall():

                    item = normalize(line[0])
                    status = line[1]

                    if item in valid_items and status < 3:

                        has_under_review = True
                        break

                if has_under_review:

                    vendor_count += 1
                    is_case_under_review = True

            # =================================================
            # FINAL FILTER
            # =================================================
            if is_case_under_review:

                final_result.append({
                    "rfq_case_id": rfq_case_id,

                    "case_name": c[1],

                    "created_date": format_ist_date_only(c[2]),

                    "expiry_date": format_ist_date_only(c[3]),

                    "vendor_count": vendor_count,

                    "status": "Under Review"
                })

    return final_result


async def get_underreview_cases():
    return await run_in_threadpool(
        _get_underreview_cases_sync
    )


# ============================================================
# VENDOR PROFILE
# ============================================================
def _fetch_vendor_profile_sync(vendor_account: str):

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
            SELECT
                TYPE,
                LOCATOR

            FROM HIQ_vendorELECTRONICADDRESSVIEW WITH (NOLOCK)

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

            FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)

            WHERE ACCOUNTNUM = ?
              AND ISPRIMARY = 1
        """, vendor_account)

        row = cursor.fetchone()

        if row:

            profile["address"] = row.ADDRESS
            profile["name"] = row.NAME

    return profile


async def fetch_vendor_profile(vendor_account: str):
    return await run_in_threadpool(
        _fetch_vendor_profile_sync,
        vendor_account
    )


# ============================================================
# VENDOR UNDER REVIEW RFQS
# ============================================================
def _get_vendor_underreview_rfqs_sync(
    rfq_case_id: str
):

    result = []

    # ========================================================
    # CASE HEADER
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT TOP 1
                RFQCASEID,
                NAME,
                CREATEDDATETIME,
                EXPIRYDATETIME,
                DELIVERYDATE

            FROM PurchRFQCaseTable WITH (NOLOCK)

            WHERE RFQCASEID = ?
        """, rfq_case_id)

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

            FROM PURCHRFQTABLE WITH (NOLOCK)

            WHERE RFQCASEID = ?
              AND DATAAREAID = 'hi-q'
        """, rfq_case_id)

        vendors = cur.fetchall()

    # ========================================================
    # PORTAL REPLIES
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                RFQID,
                VENDORACCOUNT,
                PAYLOADJSON

            FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)

            WHERE SUBMISSIONSTATUS = 1
        """)

        portal_rows = cur.fetchall()

    portal_map = {}

    for row in portal_rows:

        key = (
            normalize(row[0]),
            normalize(row[1])
        )

        portal_map[key] = row[2]

    # ========================================================
    # D365 PROCESSING
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        for v in vendors:

            rfq_id = normalize(v[0])
            vendor_account = normalize(v[1])

            profile = _fetch_vendor_profile_sync(
                vendor_account
            )

            # =================================================
            # REPLY HEADER
            # =================================================
            cur.execute("""
                SELECT TOP 1
                    DELIVERYDATE,
                    DLVMODE,
                    DLVTERM,
                    CURRENCYCODE

                FROM PURCHRFQREPLYTABLE WITH (NOLOCK)

                WHERE RFQID = ?
                  AND DATAAREAID = 'hi-q'
            """, rfq_id)

            reply = cur.fetchone()

            vendor_reply = {
                "expected_delivery_date":
                    format_ist_date_only(reply[0])
                    if reply and reply[0] else None,

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
            payload_json = portal_map.get(
                (rfq_id, vendor_account)
            )

            if not payload_json:
                continue

            payload_map = {}

            try:

                payload = json.loads(payload_json)

                for item in payload.get("Item", []):

                    key = normalize(
                        item.get("itemNumber")
                    )

                    status = str(
                        item.get("lineStatus")
                    ).lower()

                    if status in ["true", "1"]:
                        payload_map[key] = True

            except Exception:
                continue

            if not payload_map:
                continue

            # =================================================
            # D365 LINES
            # =================================================
            cur.execute("""
                SELECT
                    ITEMID,
                    STATUS

                FROM PURCHRFQLINE WITH (NOLOCK)

                WHERE RFQID = ?
                  AND DATAAREAID = 'hi-q'
            """, rfq_id)

            d365_lines = cur.fetchall()

            valid_items = set()
            is_under_review = False

            for line in d365_lines:

                item_id = normalize(line[0])
                status = line[1]

                if item_id in payload_map:

                    valid_items.add(item_id)

                    if status < 3:
                        is_under_review = True

            if not valid_items:
                continue

            if not is_under_review:
                continue

            # =================================================
            # LINE DETAILS
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
                    PL.DELIVERYDATE,
                    RL.DELIVERYDATE,
                    PL.STATUS,
                    PL.HIQ_COMMENTS

                FROM PURCHRFQLINE PL WITH (NOLOCK)

                LEFT JOIN PURCHRFQREPLYLINE RL WITH (NOLOCK)
                    ON RL.RFQLINERECID = PL.RECID
                   AND RL.RFQID = ?
                   AND RL.DATAAREAID = 'hi-q'

                LEFT JOIN INVENTTABLE IT WITH (NOLOCK)
                    ON IT.ITEMID = PL.ITEMID

                WHERE PL.RFQID = ?
                  AND PL.DATAAREAID = 'hi-q'
            """, rfq_id, rfq_id)

            line_items = []

            for line in cur.fetchall():

                item_id = normalize(line[1])

                if item_id not in valid_items:
                    continue

                line_items.append({
                    "line_no":
                        int(line[0] or 0),

                    "material_code":
                        line[1],

                    "material_description":
                        line[2] or " ",

                    "quantity":
                        float(line[3] or 0),

                    "uom":
                        line[4] or "-",

                    "unit_price":
                        float(line[5] or 0),

                    "net_amount":
                        float(line[6] or 0),

                    "vendor_remarks":
                        line[7] or " ",

                    "target_price":
                        float(line[8] or 0),

                    "line_delivery_date":
                        format_ist_date_only(line[9]),

                    "vendorreply_delivery_date":
                        format_ist_date_only(line[10]),

                    "status":
                        "Under Review"
                        if line[11] < 3
                        else "Completed",

                    "comments":
                        line[12] or " "
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

    if not result:
        return {}

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
                "Under Review"
        },

        "vendors":
            result
    }


async def get_vendor_underreview_rfqs(
    rfq_case_id: str
):
    return await run_in_threadpool(
        _get_vendor_underreview_rfqs_sync,
        rfq_case_id
    )


# from app.db.base import get_connection
# from app.utils.date_utils import format_date,format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# import json 
# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only,format_ist_date_only
# import json
# from fastapi.concurrency import run_in_threadpool
# def _get_underreview_cases_sync():

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # =========================
#         # STEP 1: GET ALL CASES
#         # =========================
#         cur.execute("""
#             SELECT
#                 RFQCASEID,
#                 MAX(NAME),
#                 MAX(CREATEDDATETIME),
#                 MAX(EXPIRYDATETIME)
#             FROM PURCHRFQTABLE
#             WHERE DATAAREAID = 'hi-q'
#             GROUP BY RFQCASEID
#             ORDER BY MAX(CREATEDDATETIME) DESC
#         """)

#         cases = cur.fetchall()
#         final_result = []

#         for c in cases:
#             rfq_case_id = c[0]

#             # =========================
#             # STEP 2: GET VENDORS
#             # =========================
#             cur.execute("""
#                 SELECT RFQID, VENDACCOUNT
#                 FROM PURCHRFQTABLE
#                 WHERE RFQCASEID = ?
#                   AND DATAAREAID = 'hi-q'
#             """, (rfq_case_id,))

#             vendors = cur.fetchall()

#             vendor_count = 0
#             is_case_under_review = False

#             for v in vendors:
#                 rfq_id = v[0]
#                 vendor_account = v[1]

#                 # =========================
#                 # STEP 3: GET PAYLOAD
#                 # =========================
#                 cur.execute("""
#                     SELECT TOP 1 PAYLOAD_JSON
#                     FROM HIQ_VENDORRFQREPLIES
#                     WHERE RFQ_ID = ?
#                       AND VENDOR_ACCOUNT = ?
#                       AND SUBMISSION_STATUS = 1
#                     ORDER BY CREATED_AT DESC
#                 """, (rfq_id, vendor_account))

#                 row = cur.fetchone()
#                 if not row or not row[0]:
#                     continue  # no submission

#                 # =========================
#                 # STEP 4: PARSE PAYLOAD
#                 # =========================
#                 try:
#                     payload = json.loads(row[0])
#                 except:
#                     continue

#                 valid_items = set()

#                 for item in payload.get("Item", []):
#                     status = item.get("lineStatus")
#                     if status is True or str(status).lower() == "true":
#                         item_id = str(item.get("itemNumber") or "").strip().upper()
#                         if item_id:
#                             valid_items.add(item_id)

#                 # skip invalid vendors
#                 if not valid_items:
#                     continue

#                 # =========================
#                 # STEP 5: CHECK D365 STATUS
#                 # =========================
#                 cur.execute("""
#                     SELECT ITEMID, STATUS
#                     FROM PURCHRFQLINE
#                     WHERE RFQID = ?
#                       AND DATAAREAID = 'hi-q'
#                 """, (rfq_id,))

#                 has_under_review = False

#                 for l in cur.fetchall():
#                     item = str(l[0]).strip().upper()
#                     status = l[1]

#                     if item in valid_items and status < 3:
#                         has_under_review = True
#                         break

#                 # ✅ COUNT ONLY IF THIS VENDOR HAS UNDER-REVIEW ITEMS
#                 if has_under_review:
#                     vendor_count += 1
#                     is_case_under_review = True

#             # =========================
#             # STEP 6: FINAL FILTER
#             # =========================
#             if is_case_under_review:
#                 final_result.append({
#                     "rfq_case_id": rfq_case_id,
#                     "case_name": c[1],
#                     "created_date": format_ist_date_only(c[2]),
#                     "expiry_date": format_ist_date_only(c[3]),
#                     "vendor_count": vendor_count,
#                     "status": "Under Review"
#                 })

#         return final_result
# async def get_underreview_cases():
#     return await run_in_threadpool(_get_underreview_cases_sync)
# def _fetch_vendor_profile_sync(vendor_account: str):

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
#     return await run_in_threadpool(_fetch_vendor_profile_sync, vendor_account)
# def  _get_vendor_underreview_rfqs_sync(rfq_case_id: str):

#     def normalize(val):
#         return str(val or "").replace(" ", "").strip().upper()

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

#             profile = _fetch_vendor_profile_sync(vendor_account)

#             # =========================
#             # VENDOR REPLY
#             # =========================
#             cur.execute("""
#                 SELECT TOP 1 DELIVERYDATE, DLVMODE, DLVTERM, CURRENCYCODE
#                 FROM PURCHRFQREPLYTABLE
#                 WHERE RFQID = ?
#                   AND DATAAREAID = 'hi-q'
#             """, (rfq_id,))

#             reply = cur.fetchone()

#             vendor_reply = {
#                 "expected_delivery_date": format_ist_date_only(reply[0]) if reply and reply[0] else None,
#                 "mode_of_delivery": reply[1] if reply else None,
#                 "delivery_term": reply[2] if reply else None,
#                 "currency": reply[3] if reply else None
#             }

#             # =========================
#             # PAYLOAD
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
#             if not row or not row[0]:
#                 continue

#             payload_map = {}

#             try:
#                 payload = json.loads(row[0])

#                 for item in payload.get("Item", []):
#                     key = normalize(item.get("itemNumber"))

#                     status = str(item.get("lineStatus")).lower()

#                     if status in ["true", "1"]:
#                         payload_map[key] = True
#             except:
#                 continue

#             if not payload_map:
#                 continue

#             # =========================
#             # D365 LINES
#             # =========================
#             cur.execute("""
#                 SELECT ITEMID, STATUS
#                 FROM PURCHRFQLINE
#                 WHERE RFQID = ?
#                   AND DATAAREAID = 'hi-q'
#             """, (rfq_id,))

#             d365_lines = cur.fetchall()

#             valid_items = set()
#             is_under_review = False

#             for l in d365_lines:
#                 item_id = normalize(l[0])
#                 status = l[1]

#                 if item_id in payload_map:
#                     valid_items.add(item_id)

#                     if status < 3:
#                         is_under_review = True

#             if not valid_items:
#                 continue

#             if not is_under_review:
#                 continue

#             # =========================
#             # LINE DETAILS (SAFE JOIN)
#             # =========================
#             cur.execute("""
#                 SELECT
#                     RL.LINENUM,
#                     PL.ITEMID,
#                     IT.NAMEALIAS,
#                     PL.QTYORDERED,
#                     RL.PURCHUNIT,
#                     RL.PURCHPRICE,
#                     RL.LINEAMOUNT,
#                     RL.HIQ_COMMENTS,
#                     PL.HIQ_TARGETPRICE,
#                     PL.DELIVERYDATE,
#                     RL.DELIVERYDATE,
#                     PL.STATUS,
#                     PL.HIQ_COMMENTS
#                 FROM PURCHRFQLINE PL
#                 LEFT JOIN PURCHRFQREPLYLINE RL
#                     ON RL.RFQLINERECID = PL.RECID
#                     AND RL.RFQID = ?
#                     AND RL.DATAAREAID = 'hi-q'
#                 LEFT JOIN INVENTTABLE IT
#                     ON IT.ITEMID = PL.ITEMID
#                 WHERE PL.RFQID = ?
#                   AND PL.DATAAREAID = 'hi-q'
#             """, (rfq_id, rfq_id))

#             line_items = []

#             for l in cur.fetchall():
#                 item_id = normalize(l[1])

#                 if item_id not in valid_items:
#                     continue

#                 line_items.append({
#                     "line_no": int(l[0] or 0),
#                     "material_code": l[1],
#                     "material_description": l[2] or " ",
#                     "quantity": float(l[3] or 0),
#                     "uom": l[4] or "-",
#                     "unit_price": float(l[5] or 0),
#                     "net_amount": float(l[6] or 0),
#                     "vendor_remarks": l[7] or " ",
#                     "target_price": float(l[8] or 0),
#                     "line_delivery_date": format_ist_date_only(l[9]),
#                     "vendorreply_delivery_date": format_ist_date_only(l[10]),
#                     "status": "Under Review" if l[11] < 3 else "Completed",
#                     "comments": l[12] or " "
#                 })

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

#         if not result:
#             return {}

#         return {
#             "case": {
#                 "rfq_case_id": case[0],
#                 "case_name": case[1],
#                 "created_date": format_ist_date_only(case[2]),
#                 "expiry_date": format_ist_date_only(case[3]),
#                 "delivery_date": format_ist_date_only(case[4]),
#                 "time_remaining": calculate_days_left(case[3]),
#                 "status": "Under Review"
#             },
#             "vendors": result
#         }

# async def get_vendor_underreview_rfqs(rfq_case_id: str):
#     return await run_in_threadpool(_get_vendor_underreview_rfqs_sync, rfq_case_id)

