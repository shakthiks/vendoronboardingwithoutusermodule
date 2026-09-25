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
RFQ_BID_HEADER_TABLE = f"{SCHEMA}.HIQ_VENDORBIDSUBMISSIONHEADER"


# ============================================================
# HELPERS
# ============================================================
def normalize(val):
    return str(val or "").strip().upper()


# ============================================================
# VENDOR PROFILE
# ============================================================
def fetch_vendor_profile_sync(vendor_account: str):

    profile = {
        "email": None,
        "phone": None,
        "address": None,
        "name": None,
        "city": None
    }

    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                TYPE,
                LOCATOR
            FROM HIQ_vendorELECTRONICADDRESSVIEW WITH (NOLOCK)
            WHERE ACCOUNTNUM = ?
              AND ISPRIMARY1 = 1
        """, vendor_account)

        for row in cur.fetchall():

            if row.TYPE == 2:
                profile["email"] = row.LOCATOR

            elif row.TYPE == 1:
                profile["phone"] = row.LOCATOR

        cur.execute("""
            SELECT TOP 1
                ADDRESS,
                NAME,
                CITY
            FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)
            WHERE ACCOUNTNUM = ?
              AND ISPRIMARY = 1
        """, vendor_account)

        row = cur.fetchone()

        if row:
            profile["address"] = row.ADDRESS
            profile["name"] = row.NAME
            profile["city"] = row.CITY

    return profile


# ============================================================
# EXPIRED CASE LIST
# RFQCASEID LEVEL
# ============================================================
def get_expired_cases_sync():

    # ========================================================
    # STEP 1 - LATEST PORTAL REPLIES + BID HEADER
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            WITH LatestReply AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY RFQCASEID, VENDORACCOUNT
                        ORDER BY ID DESC
                    ) AS RN
                FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
            ),
            LatestHeader AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY RFQID, VENDORACCOUNT
                        ORDER BY ID DESC
                    ) AS RN
                FROM {RFQ_BID_HEADER_TABLE} WITH (NOLOCK)
            )
            SELECT
                R.RFQCASEID,
                R.RFQID,
                R.VENDORACCOUNT,
                R.SUBMISSIONSTATUS,
                R.DRAFTLINECOUNT,
                R.CONFIRMEDLINECOUNT,
                H.STATUS AS BIDHEADERSTATUS
            FROM LatestReply R
            LEFT JOIN LatestHeader H
                ON H.RFQID = R.RFQID
               AND H.VENDORACCOUNT = R.VENDORACCOUNT
               AND H.RN = 1
            WHERE R.RN = 1
        """)

        cols = [c[0] for c in cur.description]

        reply_rows = [
            dict(zip(cols, r))
            for r in cur.fetchall()
        ]

    reply_map = {}

    for r in reply_rows:

        key = (
            normalize(r["RFQCASEID"]),
            normalize(r["VENDORACCOUNT"])
        )

        reply_map[key] = r

    # ========================================================
    # STEP 2 - EXPIRED CASES FROM D365
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                T.RFQCASEID,
                MAX(C.NAME) AS CASE_NAME,
                MAX(C.CREATEDDATETIME) AS CREATED_DATE,
                MAX(C.EXPIRYDATETIME) AS EXPIRY_DATE,
                COUNT(DISTINCT T.VENDACCOUNT) AS VENDOR_COUNT
            FROM PURCHRFQTABLE T WITH (NOLOCK)
            INNER JOIN PurchRFQCaseTable C WITH (NOLOCK)
                ON C.RFQCASEID = T.RFQCASEID
            WHERE T.DATAAREAID = 'hi-q'
            GROUP BY T.RFQCASEID
            HAVING
                CAST(
                    DATEADD(MINUTE, 330, MAX(C.EXPIRYDATETIME))
                    AS DATE
                )
                <
                CAST(
                    DATEADD(MINUTE, 330, GETUTCDATE())
                    AS DATE
                )
            ORDER BY MAX(C.CREATEDDATETIME) DESC
        """)

        cases = cur.fetchall()

    result = []

    # ========================================================
    # STEP 3 - CHECK CASE HAS EXPIRED VENDORS
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        for case in cases:

            rfq_case_id = normalize(case[0])

            cur.execute("""
                SELECT
                    RFQID,
                    VENDACCOUNT
                FROM PURCHRFQTABLE WITH (NOLOCK)
                WHERE RFQCASEID = ?
                  AND DATAAREAID = 'hi-q'
            """, rfq_case_id)

            vendors = cur.fetchall()

            expired_vendor_count = 0

            for vendor in vendors:

                vendor_account = normalize(vendor[1])

                latest_reply = reply_map.get(
                    (
                        rfq_case_id,
                        vendor_account
                    )
                )

                include_vendor = False

                # NOT OPENED
                if not latest_reply:
                    include_vendor = True

                # DRAFTED / PARTIAL DRAFT
                elif (
                    latest_reply["SUBMISSIONSTATUS"] == 1
                    and
                    (latest_reply["DRAFTLINECOUNT"] or 0) > 0
                    and
                    latest_reply.get("BIDHEADERSTATUS") == 2
                ):
                    include_vendor = True

                if include_vendor:
                    expired_vendor_count += 1

            if expired_vendor_count == 0:
                continue

            result.append({
                "rfq_case_id": case[0],
                "case_name": case[1],
                "created_date": format_ist_date_only(case[2]),
                "expiry_date": format_ist_date_only(case[3]),
                "vendor_count": case[4],
                "expired_vendor_count": expired_vendor_count,
                "status": "Expired"
            })

    return result


async def get_expired_cases():
    return await run_in_threadpool(
        get_expired_cases_sync
    )


# ============================================================
# EXPIRED CASE DETAIL
# RFQCASEID LEVEL
# Shows all expired vendors/RFQs under one case
# ============================================================
def get_vendor_expired_rfqs_sync(rfq_case_id: str):

    rfq_case_id = normalize(rfq_case_id)

    # ========================================================
    # STEP 1 - CASE HEADER
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
        # GET ALL VENDORS/RFQS UNDER THIS CASE
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
    # STEP 2 - LATEST REPLY MAP FOR THIS CASE
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            WITH LatestReply AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY RFQID, VENDORACCOUNT
                        ORDER BY ID DESC
                    ) AS RN
                FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
                WHERE UPPER(RFQCASEID) = UPPER(?)
            ),
            LatestHeader AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY RFQID, VENDORACCOUNT
                        ORDER BY ID DESC
                    ) AS RN
                FROM {RFQ_BID_HEADER_TABLE} WITH (NOLOCK)
                WHERE UPPER(RFQCASEID) = UPPER(?)
            )
            SELECT
                R.RFQCASEID,
                R.RFQID,
                R.VENDORACCOUNT,
                R.PAYLOADJSON,
                R.SUBMISSIONSTATUS,
                R.DRAFTLINECOUNT,
                R.CONFIRMEDLINECOUNT,
                R.ID,
                H.STATUS AS BIDHEADERSTATUS
            FROM LatestReply R
            LEFT JOIN LatestHeader H
                ON H.RFQID = R.RFQID
               AND H.VENDORACCOUNT = R.VENDORACCOUNT
               AND H.RN = 1
            WHERE R.RN = 1
        """, (rfq_case_id, rfq_case_id))

        cols = [c[0] for c in cur.description]

        reply_rows = [
            dict(zip(cols, r))
            for r in cur.fetchall()
        ]

    reply_map = {}

    for r in reply_rows:

        key = (
            normalize(r["RFQID"]),
            normalize(r["VENDORACCOUNT"])
        )

        reply_map[key] = r

    result = []

    # ========================================================
    # STEP 3 - BUILD VENDOR DETAIL
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        for vendor in vendors:

            rfq_id = normalize(vendor[0])
            vendor_account = normalize(vendor[1])

            latest_reply = reply_map.get(
                (
                    rfq_id,
                    vendor_account
                )
            )

            include_vendor = False
            expired_status = None
            payload_json = None

            # ------------------------------------------------
            # NOT OPENED
            # ------------------------------------------------
            if not latest_reply:

                include_vendor = True
                expired_status = "Not Opened"

            # ------------------------------------------------
            # DRAFTED / PARTIAL DRAFT
            # ------------------------------------------------
            elif (
                latest_reply["SUBMISSIONSTATUS"] == 1
                and
                (latest_reply["DRAFTLINECOUNT"] or 0) > 0
                and
                latest_reply.get("BIDHEADERSTATUS") == 2
            ):

                include_vendor = True
                expired_status = "Drafted"
                payload_json = latest_reply.get("PAYLOADJSON")

            if not include_vendor:
                continue

            profile = fetch_vendor_profile_sync(
                vendor_account
            )

            # =================================================
            # PAYLOAD MAP
            # Only useful for Drafted vendors
            # Key = (lineNumber, itemNumber)
            # =================================================
            payload_map = {}

            if payload_json:

                try:
                    payload = json.loads(payload_json)

                    for item in payload.get("Item", []):

                        line_number = item.get("lineNumber")
                        item_number = item.get("itemNumber")

                        if line_number is None or not item_number:
                            continue

                        key = (
                            int(float(line_number)),
                            normalize(item_number)
                        )

                        payload_map[key] = {
                            "unit_price": item.get("unitPrice"),
                            "net_amount": item.get("netAmount"),
                            "vendor_comments": item.get("vendorComments"),
                            "line_status": item.get("lineStatus"),
                            "delivery_date": item.get("deliveryDate")
                        }

                except Exception:
                    payload_map = {}

            # =================================================
            # VENDOR REPLY HEADER
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
            # LINE ITEMS
            # For Not Opened: show all approved RFQ lines
            # For Drafted: show only draft/unconfirmed lines if payload exists
            # =================================================
            cur.execute("""
                SELECT
                    PL.LINENUM,
                    PL.ITEMID,
                    IT.NAMEALIAS,
                    PL.QTYORDERED,
                    PL.PURCHUNIT,
                    PL.HIQ_TARGETPRICE,
                    PL.HIQ_COMMENTS,
                    PL.CURRENCYCODE,
                    PL.DELIVERYDATE
                FROM PURCHRFQLINE PL WITH (NOLOCK)

                LEFT JOIN INVENTTABLE IT WITH (NOLOCK)
                    ON IT.ITEMID = PL.ITEMID

                INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
                    ON UPPER(LTRIM(RTRIM(AVL.ITEMID)))
                     = UPPER(LTRIM(RTRIM(PL.ITEMID)))
                   AND AVL.PDSAPPROVEDVENDOR = ?
                   AND AVL.DATAAREAID = 'hi-q'
                   AND AVL.VALIDFROM <= GETUTCDATE()
                   AND AVL.VALIDTO >= GETUTCDATE()

                WHERE PL.RFQID = ?
                  AND PL.DATAAREAID = 'hi-q'

                ORDER BY PL.LINENUM
            """, (vendor_account, rfq_id))

            line_items = []

            for line in cur.fetchall():

                line_no = int(line[0] or 0)
                item_id = normalize(line[1])

                key = (
                    line_no,
                    item_id
                )

                saved = payload_map.get(
                    key,
                    {}
                )

                # For Drafted expired:
                # If lineStatus = true, vendor already confirmed this line.
                # Expiry tab should show only missed/draft lines.
                if expired_status == "Drafted":

                    if saved.get("line_status") is True:
                        continue

                line_items.append({
                    "line_no": line_no,
                    "material_code": line[1],
                    "material_description": line[2] or "-",
                    "quantity": float(line[3] or 0),
                    "uom": line[4] or "-",
                    "target_price": float(line[5] or 0),
                    "currency": line[7] or "-",
                    "comments": line[6] or " ",
                    "line_delivery_date":
                        format_ist_date_only(line[8]),

                    "unit_price":
                        float(saved.get("unit_price") or 0)
                        if saved.get("unit_price") not in [None, ""]
                        else "",

                    "net_amount":
                        float(saved.get("net_amount") or 0)
                        if saved.get("net_amount") not in [None, ""]
                        else "",

                    "vendor_remarks":
                        saved.get("vendor_comments") or " ",

                    "vendor_delivery_date":
                        saved.get("delivery_date") or "",

                    "status":
                        expired_status
                })

            if not line_items:
                continue

            result.append({
                "rfq_id": rfq_id,
                "vendor_name": profile["name"],
                "vendor_account": vendor_account,
                "location": profile["address"],
                "mode_of_delivery": vendor[2],
                "delivery_term": vendor[3],
                "payment_term": vendor[4],
                "expired_status": expired_status,

                "vendor_info": {
                    "contact_person": profile["name"],
                    "mobile": profile["phone"],
                    "email": profile["email"],
                    "location": profile["address"]
                },

                "vendor_reply": vendor_reply,

                "hiq_requirement": line_items
            })

    return {
        "case": {
            "rfq_case_id": case[0],
            "case_name": case[1],
            "created_date": format_ist_date_only(case[2]),
            "expiry_date": format_ist_date_only(case[3]),
            "delivery_date": format_ist_date_only(case[4]),
            "time_remaining": calculate_days_left(case[3]),
            "status": "Expired"
        },
        "vendors": result
    }


async def get_vendor_expired_rfqs(rfq_case_id: str):
    return await run_in_threadpool(
        get_vendor_expired_rfqs_sync,
        rfq_case_id
    )