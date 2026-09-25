import json
from typing import Optional, Set

from fastapi.concurrency import run_in_threadpool

from app.db.base import get_connection, get_d365_connection
from app.core.config import settings
from app.utils.date_utils import format_ist_date_only
from app.utils.remainingdate import calculate_days_left
from app.utils.lastused_time import format_last_edited


SCHEMA = settings.DB_SCHEMA
RFQ_REPLIES_TABLE = f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
VENDOR_USER_TABLE = f"{SCHEMA}.HIQ_VENDORPORTALUSER"


PURCH_STATUS_MAP = {
    0: "Confirmed",
    1: "Confirmed",
    2: "Received",
    3: "Invoiced",
    4: "Cancelled"
}


def _norm(value):
    return str(value or "").strip().upper()


# ============================================================
# VENDOR DASHBOARD KPIS
# ============================================================
def get_vendor_dashboard_kpis_sync(vendor_account: str):
    with get_d365_connection() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(DISTINCT ITEMID)
            FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)
            WHERE PDSAPPROVEDVENDOR = ?
              AND VALIDFROM <= GETUTCDATE()
              AND VALIDTO >= GETUTCDATE()
        """, vendor_account)
        materials = cur.fetchone()[0] or 0
        cur.execute("""SELECT
                        T.RFQID
                    FROM PurchRFQTable T WITH (NOLOCK)
                    INNER JOIN PurchRFQCaseTable C WITH (NOLOCK)
                        ON C.RFQCASEID = T.RFQCASEID
                    WHERE T.VENDACCOUNT = ?
                    AND T.DATAAREAID = 'hi-q'
                    AND CAST(DATEADD(MINUTE,330,C.EXPIRYDATETIME) AS DATE)
                        >= CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)
                    AND EXISTS (
                        SELECT 1
                        FROM PurchRFQCaseLine CL WITH (NOLOCK)
                        INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
                            ON AVL.ITEMID = CL.ITEMID
                            AND AVL.PDSAPPROVEDVENDOR = T.VENDACCOUNT
                            AND AVL.DATAAREAID = C.DATAAREAID
                        WHERE CL.RFQCASEID = C.RFQCASEID
                    )
                    """, vendor_account)
    #     cur.execute("""
    #         SELECT
    #             T.RFQID
    #         FROM PurchRFQTable T WITH (NOLOCK)
    #         INNER JOIN PurchRFQCaseTable C WITH (NOLOCK)
    #             ON C.RFQCASEID = T.RFQCASEID
    #         WHERE T.VENDACCOUNT = ?
    #           AND T.DATAAREAID = 'hi-q'
    #           AND CAST(DATEADD(MINUTE,330,C.EXPIRYDATETIME) AS DATE)
    #   >= CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)
    #                 --CAST(C.EXPIRYDATETIME AS DATE) >= CAST(GETDATE() AS DATE)
    #     """, vendor_account)
        d365_open_rfqs = [_norm(r[0]) for r in cur.fetchall()]

        cur.execute("""
            SELECT COUNT(DISTINCT PURCHID)
            FROM PURCHTABLE WITH (NOLOCK)
            WHERE ORDERACCOUNT = ?
              AND PURCHSTATUS IN (0,1)
        """, vendor_account)
        open_pos = cur.fetchone()[0] or 0

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(f"""
            SELECT RFQID, SUBMISSIONSTATUS
            FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
            WHERE VENDORACCOUNT = ?
        """, vendor_account)

        reply_map = {
            _norm(r[0]): int(r[1])
            for r in cur.fetchall()
        }

        cur.execute(f"""
            SELECT TOP 1 STATUS
            FROM {VENDOR_USER_TABLE} WITH (NOLOCK)
            WHERE VENDORACCOUNT = ?
        """, vendor_account)
        row = cur.fetchone()

    open_rfqs = 0
    for rfq_id in d365_open_rfqs:
        status = reply_map.get(rfq_id)
        if status in (0, 1) or status is None:
            open_rfqs += 1

    rfq_data = fetch_vendor_rfqs_sync(vendor_account)
    submitted_rfqs = len([
        r for r in rfq_data.get("rfqs", [])
        if r.get("status") == "Submitted"
    ])

    status = "Inactive"
    if row and row[0] in (1, 2):
        status = "Active"

    return {
        "materials_supported": materials,
        "open_rfqs": open_rfqs,
        "open_pos": open_pos,
        "submitted_rfqs": submitted_rfqs,
        "status": status
    }


async def get_vendor_dashboard_kpis(vendor_account: str):
    return await run_in_threadpool(get_vendor_dashboard_kpis_sync, vendor_account)


# ============================================================
# BID MATERIALS - D365 ONLY
# ============================================================
def fetch_bid_materials_sync(vendor_account: str):
    query = """
        SELECT
            LTRIM(RTRIM(P.ITEMID)) AS ITEMID,
            I.NAMEALIAS,
            P.VALIDFROM,
            P.VALIDTO
        FROM PDSAPPROVEDVENDORLIST P WITH (NOLOCK)
        INNER JOIN INVENTTABLE I WITH (NOLOCK)
            ON LTRIM(RTRIM(I.ITEMID)) = LTRIM(RTRIM(P.ITEMID))
           AND I.DATAAREAID = 'hi-q'
        WHERE P.PDSAPPROVEDVENDOR = ?
          AND P.DATAAREAID = 'hi-q'
          AND P.VALIDFROM <= GETUTCDATE()
          AND P.VALIDTO >= GETUTCDATE()
        ORDER BY LTRIM(RTRIM(P.ITEMID))
    """

    with get_d365_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, vendor_account)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    result = []
    for row in rows:
        data = dict(zip(cols, row))
        days_left = calculate_days_left(data["VALIDTO"])

        result.append({
            "material_id": data["ITEMID"],
            "material_description": data["NAMEALIAS"],
            "valid_from": format_ist_date_only(data["VALIDFROM"]),
            "expiry_date": format_ist_date_only(data["VALIDTO"]),
            "days_left": days_left,
            "expiry_status": True if isinstance(days_left, int) and days_left < 30 else False
        })

    return result


async def fetch_bid_materials(vendor_account: str):
    return await run_in_threadpool(fetch_bid_materials_sync, vendor_account)


# ============================================================
# VENDOR PROFILE - D365 ONLY
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
            SELECT TYPE, LOCATOR
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
            SELECT TOP 1 ADDRESS, NAME, CITY
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


async def fetch_vendor_profile(vendor_account: str):
    return await run_in_threadpool(fetch_vendor_profile_sync, vendor_account)


# ============================================================
# PORTAL PAYLOAD HELPERS
# ============================================================
def _get_latest_reply_map(vendor_account: str):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT
                ID,
                RFQID,
                RFQCASEID,
                VENDORACCOUNT,
                PAYLOADJSON,
                SUBMISSIONSTATUS,
                CONFIRMSAVE,
                CREATEDDATETIME,
                SENDTOD365AT
            FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY RFQID, VENDORACCOUNT
                        ORDER BY ID DESC
                    ) AS RN
                FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
                WHERE VENDORACCOUNT = ?
            ) X
            WHERE RN = 1
        """, vendor_account)

        cols = [c[0].lower() for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    return {
        _norm(r["rfqid"]): r
        for r in rows
    }

def _get_valid_payload_items_from_row(row):
    try:
        if not row or not row.get("payloadjson"):
            return None

        payload = json.loads(row["payloadjson"])
        items = payload.get("Item", []) or payload.get("rfqItems", [])

        if not any("lineStatus" in i for i in items):
            return None

        valid = set()

        for item in items:
            if str(item.get("lineStatus")).lower() == "true":

                valid.add((
                    int(float(item.get("lineNumber") or 0)),
                    _norm(item.get("itemNumber"))
                ))

        return valid

    except Exception as e:
        print("[PAYLOAD FILTER ERROR]", e)
        return None
# def _get_valid_payload_items_from_row(row) -> Optional[Set[str]]:
#     try:
#         if not row or not row.get("payloadjson"):
#             return None

#         payload = json.loads(row["payloadjson"])
#         items = payload.get("Item", []) or payload.get("rfqItems", [])

#         if not any("lineStatus" in i for i in items):
#             return None

#         valid = set()
#         for item in items:
#             if str(item.get("lineStatus")).lower() == "true":
#                 valid.add(_norm(item.get("itemNumber")))

#         return valid
#     except Exception:
#         return None


def _get_valid_payload_items(rfq_id: str, vendor_account: str) -> Optional[Set[str]]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT TOP 1 PAYLOADJSON
            FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
            WHERE UPPER(RFQID) = UPPER(?)
              AND UPPER(VENDORACCOUNT) = UPPER(?)
            ORDER BY ID DESC
        """, rfq_id, vendor_account)

        row = cur.fetchone()

    if not row or not row[0]:
        return None

    try:
        payload = json.loads(row[0])
        items = payload.get("Item", []) or payload.get("rfqItems", [])

        if not any("lineStatus" in i for i in items):
            return None

        return {
            _norm(i.get("itemNumber"))
            for i in items
            if str(i.get("lineStatus")).lower() == "true"
        }
    except Exception:
        return None


# ============================================================
# RFQ LIST - D365 + PORTAL SPLIT
# ============================================================
def fetch_vendor_rfqs_sync(vendor_account: str):
    result = []

    reply_map = _get_latest_reply_map(vendor_account)

    with get_d365_connection() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT
                L.RFQCASEID,
                T.RFQID,
                L.NAME AS RFQNAME,
                L.EXPIRYDATETIME AS CLOSING_DATE,
                L.DELIVERYDATE AS EXPECTED_DELIVERY_DATE,
                PT.DESCRIPTION AS PAYMENT_TERM,
                PM.NAME AS PAYMENT_MODE,
                DM.TXT AS DELIVERY_MODE,
                DT.TXT AS DELIVERY_TERM
            FROM PurchRFQCaseTable L WITH (NOLOCK)
            INNER JOIN PurchRFQTable T WITH (NOLOCK)
                ON T.RFQCASEID = L.RFQCASEID
               AND T.VENDACCOUNT = ?
            LEFT JOIN PAYMTERM PT WITH (NOLOCK)
                ON L.PAYMENT = PT.PAYMTERMID
            LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
                ON L.PAYMMODE = PM.PAYMMODE
            LEFT JOIN DLVMODE DM WITH (NOLOCK)
                ON L.DLVMODE = DM.CODE
            LEFT JOIN DLVTERM DT WITH (NOLOCK)
                ON L.DLVTERM = DT.CODE
            WHERE CAST(DATEADD(MINUTE,330,L.EXPIRYDATETIME) AS DATE)
      >= CAST(DATEADD(MINUTE,330,GETUTCDATE()) AS DATE)
        AND EXISTS (
                  SELECT 1
                  FROM PurchRFQCaseLine CL WITH (NOLOCK)
                  INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
                      ON AVL.ITEMID = CL.ITEMID
                     AND AVL.PDSAPPROVEDVENDOR = T.VENDACCOUNT
                     AND AVL.VALIDFROM <= GETUTCDATE()
                     AND AVL.VALIDTO >= GETUTCDATE()
                  WHERE CL.RFQCASEID = L.RFQCASEID
              )
            ORDER BY L.EXPIRYDATETIME ASC
        """, vendor_account)

        cols = [c[0] for c in cur.description]
        active_rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for data in active_rows:
            rfq_id = _norm(data["RFQID"])
            reply = reply_map.get(rfq_id)

            if not reply:
                result.append({
                    "rfq_id": data["RFQID"],
                    "rfq_case_id": data["RFQCASEID"],
                    "rfq_name": data["RFQNAME"] or "-",
                    "expiry_date": format_ist_date_only(data["CLOSING_DATE"]),
                    "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
                    "payment_term": data["PAYMENT_TERM"] or "-",
                    "payment_mode": data["PAYMENT_MODE"] or "-",
                    "delivery_mode": data["DELIVERY_MODE"] or "-",
                    "delivery_term": data["DELIVERY_TERM"] or "-",
                    "dates_left": calculate_days_left(data["CLOSING_DATE"]),
                    "last_edited": None,
                    "reply_id": None,
                    "confirm_save": None,
                    "status": "New",
                })
                continue

            if int(reply["submissionstatus"]) in (0, 2):
                result.append({
                    "rfq_id": data["RFQID"],
                    "rfq_case_id": data["RFQCASEID"],
                    "rfq_name": data["RFQNAME"] or "-",
                    "expiry_date": format_ist_date_only(data["CLOSING_DATE"]),
                    "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
                    "payment_term": data["PAYMENT_TERM"] or "-",
                    "payment_mode": data["PAYMENT_MODE"] or "-",
                    "delivery_mode": data["DELIVERY_MODE"] or "-",
                    "delivery_term": data["DELIVERY_TERM"] or "-",
                    "dates_left": calculate_days_left(data["CLOSING_DATE"]),
                    "last_edited": format_last_edited(reply["createddatetime"]) if reply["createddatetime"] else None,
                    "reply_id": reply["id"],
                    "confirm_save": reply["confirmsave"],
                    "status": "In Progress",
                })

        submitted_ids = [
            rfq_id for rfq_id, reply in reply_map.items()
            if int(reply["submissionstatus"]) == 1
        ]

        for rfq_id in submitted_ids:
            reply = reply_map[rfq_id]

            cur.execute("""
                SELECT
                    L.RFQCASEID,
                    T.RFQID,
                    L.NAME AS RFQNAME,
                    L.EXPIRYDATETIME AS CLOSING_DATE,
                    L.DELIVERYDATE AS EXPECTED_DELIVERY_DATE,
                    PT.DESCRIPTION AS PAYMENT_TERM,
                    PM.NAME AS PAYMENT_MODE,
                    DM.TXT AS DELIVERY_MODE,
                    DT.TXT AS DELIVERY_TERM
                FROM PurchRFQTable T WITH (NOLOCK)
                INNER JOIN PurchRFQCaseTable L WITH (NOLOCK)
                    ON L.RFQCASEID = T.RFQCASEID
                LEFT JOIN PAYMTERM PT ON L.PAYMENT = PT.PAYMTERMID
                LEFT JOIN VENDPAYMMODETABLE PM ON L.PAYMMODE = PM.PAYMMODE
                LEFT JOIN DLVMODE DM ON L.DLVMODE = DM.CODE
                LEFT JOIN DLVTERM DT ON L.DLVTERM = DT.CODE
                WHERE T.RFQID = ?
                  AND T.VENDACCOUNT = ?
            """, rfq_id, vendor_account)

            row = cur.fetchone()
            if not row:
                continue

            cols = [c[0] for c in cur.description]
            data = dict(zip(cols, row))

            valid_items = _get_valid_payload_items_from_row(reply)
            if not valid_items:
                continue



            cur.execute("""
                SELECT
                    LINENUM,
                    ITEMID,
                    STATUS
                FROM PURCHRFQLINE WITH (NOLOCK)
                WHERE RFQID = ?
                AND DATAAREAID = 'hi-q'
            """, rfq_id)

            is_valid_submitted = False

            for line in cur.fetchall():

                line_num = int(float(line[0] or 0))
                item = _norm(line[1])
                status = line[2]

                if (line_num, item) in valid_items and status < 3:
                    is_valid_submitted = True
                    break



            # cur.execute("""
            #     SELECT ITEMID, STATUS
            #     FROM PURCHRFQLINE WITH (NOLOCK)
            #     WHERE RFQID = ?
            #       AND DATAAREAID = 'hi-q'
            # """, rfq_id)

            # is_valid_submitted = False
            # for line in cur.fetchall():
            #     item = _norm(line[0])
            #     status = line[1]
            #     if item in valid_items and status < 3:
            #         is_valid_submitted = True
            #         break

            if not is_valid_submitted:
                continue

            result.append({
                "rfq_id": data["RFQID"],
                "rfq_case_id": data["RFQCASEID"],
                "rfq_name": data["RFQNAME"] or "-",
                "expiry_date": format_ist_date_only(data["CLOSING_DATE"]),
                "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
                "payment_term": data["PAYMENT_TERM"] or "-",
                "payment_mode": data["PAYMENT_MODE"] or "-",
                "delivery_mode": data["DELIVERY_MODE"] or "-",
                "delivery_term": data["DELIVERY_TERM"] or "-",
                "dates_left": calculate_days_left(data["CLOSING_DATE"]),
                "last_edited": format_ist_date_only(reply["sendtod365at"]),
                "reply_id": None,
                "confirm_save": None,
                "status": "Submitted",
            })

    result.sort(key=lambda x: x["expiry_date"] or "")

    return {
        "total": len(result),
        "rfqs": result
    }


async def fetch_vendor_rfqs(vendor_account: str):
    return await run_in_threadpool(fetch_vendor_rfqs_sync, vendor_account)


# ============================================================
# PO LIST - D365 ONLY
# ============================================================
def fetch_po_list_sync(vendor_account: str):
    query="""
SELECT

    P.PURCHID, 

    R.RFQID,

    P.CREATEDDATETIME,

    SUM(L.LINEAMOUNT) AS TOTAL_AMOUNT,

    P.PURCHSTATUS,

    P.CURRENCYCODE

FROM PURCHTABLE P WITH (NOLOCK)

LEFT JOIN PURCHLINE L WITH (NOLOCK)

    ON L.PURCHID = P.PURCHID

LEFT JOIN (

    SELECT PURCHID, MAX(RFQID) AS RFQID

    FROM PURCHRFQLINE

    GROUP BY PURCHID

) R

    ON R.PURCHID = P.PURCHID

WHERE 

    P.ORDERACCOUNT = ?

    
GROUP BY

    P.PURCHID,

    R.RFQID,

    P.PURCHSTATUS,

    P.CREATEDDATETIME,

    P.CURRENCYCODE

ORDER BY P.CREATEDDATETIME DESC;
 
    """
    # query = """
    #     SELECT
    #         P.PURCHID,
    #         R.RFQID,
    #         P.CREATEDDATETIME,
    #         SUM(L.LINEAMOUNT) AS TOTAL_AMOUNT,
    #         P.PURCHSTATUS,
    #         P.CURRENCYCODE
    #     FROM PURCHTABLE P WITH (NOLOCK)
    #     LEFT JOIN PURCHLINE L WITH (NOLOCK)
    #         ON L.PURCHID = P.PURCHID
    #     LEFT JOIN PURCHRFQLINE R WITH (NOLOCK)
    #         ON R.PURCHID = L.PURCHID AND R.LINENUM = L.LINENUMBER
    #     WHERE P.ORDERACCOUNT = ?
    #     GROUP BY
    #         P.PURCHID,
    #         R.RFQID,
    #         P.PURCHSTATUS,
    #         P.CREATEDDATETIME,
    #         P.CURRENCYCODE
    #     ORDER BY P.CREATEDDATETIME DESC
    # """

    with get_d365_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, vendor_account)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    return [dict(zip(cols, r)) for r in rows]


def get_po_list_sync(vendor_account: str):
    data = fetch_po_list_sync(vendor_account)

    PURCH_STATUS_MAP_LIST = {
        0: "Open",
        1: "Open",
        2: "Received",
        3: "Invoiced",
        4: "Cancelled"
    }

    return [
        {
            "po_id": row["PURCHID"],
            "rfq_id": row["RFQID"],
            "created_date": format_ist_date_only(row["CREATEDDATETIME"]),
            "total_amount": float(row["TOTAL_AMOUNT"] or 0),
            "purch_state": PURCH_STATUS_MAP_LIST.get(row["PURCHSTATUS"]),
            "currency": row["CURRENCYCODE"]
        }
        for row in data
    ]


async def fetch_po_list(vendor_account: str):
    return await run_in_threadpool(fetch_po_list_sync, vendor_account)


async def get_po_list(vendor_account: str):
    return await run_in_threadpool(get_po_list_sync, vendor_account)


# ============================================================
# RFQ DETAIL - D365 + PORTAL SPLIT
# ============================================================
def fetch_rfq_detail_sync(rfq_id: str, vendor_account: str, status: str):
    with get_d365_connection() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT
                L.RFQCASEID,
                T.RFQID,
                L.NAME,
                L.EXPIRYDATETIME,
                L.CREATEDDATETIME,
                L.DELIVERYDATE,
                L.HIQ_COMMENTS AS COMMENTS,
                PT.DESCRIPTION,
                PM.NAME,
                DM.TXT,
                DT.TXT
            FROM PurchRFQCaseTable L WITH (NOLOCK)
            INNER JOIN PurchRFQTable T WITH (NOLOCK)
                ON T.RFQCASEID = L.RFQCASEID
               AND T.VENDACCOUNT = ?
            LEFT JOIN PAYMTERM PT ON L.PAYMENT = PT.PAYMTERMID
            LEFT JOIN VENDPAYMMODETABLE PM ON L.PAYMMODE = PM.PAYMMODE
            LEFT JOIN DLVMODE DM ON L.DLVMODE = DM.CODE
            LEFT JOIN DLVTERM DT ON L.DLVTERM = DT.CODE
            WHERE T.RFQID = ?
        """, vendor_account, rfq_id)

        header_row = cur.fetchone()
        if not header_row:
            return {
                "success": False,
                "message": "RFQ not found"
            }

        cols = [c[0] for c in cur.description]
        header = dict(zip(cols, header_row))

        items = []

        if status in ["New", "In Progress"]:
            cur.execute("""
                SELECT
                    RL.LINENUM,
                    RL.ITEMID,
                    IT.NAMEALIAS,
                    RL.QTYORDERED,
                    RL.PURCHUNIT,
                    RL.HIQ_TARGETPRICE,
                    RL.HIQ_COMMENTS,
                    RL.CURRENCYCODE,
                    RL.DELIVERYDATE
                FROM PurchRFQLine RL WITH (NOLOCK)
                LEFT JOIN INVENTTABLE IT WITH (NOLOCK)
                    ON IT.ITEMID = RL.ITEMID
                WHERE RL.RFQID = ?
                  AND EXISTS (
                      SELECT 1
                      FROM PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
                      WHERE AVL.ITEMID = RL.ITEMID
                        AND AVL.PDSAPPROVEDVENDOR = ?
                        AND AVL.VALIDFROM <= GETUTCDATE()
                        AND AVL.VALIDTO >= GETUTCDATE()
                  )
                ORDER BY RL.LINENUM
            """, rfq_id, vendor_account)

            line_cols = [c[0] for c in cur.description]
            d365_lines = [dict(zip(line_cols, r)) for r in cur.fetchall()]

        elif status == "Submitted":
            cur.execute("""
                SELECT
                    RL.LINENUM,
                    PL.ITEMID,
                    RL.NAME,
                    RL.PURCHQTY,
                    RL.PURCHUNIT,
                    RL.PURCHPRICE,
                    RL.LINEAMOUNT,
                    RL.DELIVERYDATE,
                    RL.HIQ_COMMENTS,
                    PL.HIQ_TARGETPRICE,
                    PL.CURRENCYCODE,
                    RL.DELIVERYDATE AS VENDORREPLY_DELIVERY_DATE,
                    PL.DELIVERYDATE AS LINE_DELIVERY_DATE,
                    PL.STATUS
                FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
                INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
                    ON PL.RECID = RL.RFQLINERECID
                WHERE RL.RFQID = ?
                  AND PL.STATUS < 3
                  AND EXISTS (
                      SELECT 1
                      FROM PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
                      WHERE AVL.ITEMID = PL.ITEMID
                        AND AVL.PDSAPPROVEDVENDOR = ?
                        AND AVL.VALIDFROM <= GETUTCDATE()
                        AND AVL.VALIDTO >= GETUTCDATE()
                  )
            """, rfq_id, vendor_account)

            line_cols = [c[0] for c in cur.description]
            d365_lines = [dict(zip(line_cols, r)) for r in cur.fetchall()]
        else:
            d365_lines = []
    draft_map = {}
    valid_lines = set()

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(f"""
            SELECT TOP 1 PAYLOADJSON
            FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
            WHERE RFQID = ?
            AND VENDORACCOUNT = ?
            ORDER BY ID DESC
        """, rfq_id, vendor_account)

        row = cur.fetchone()

    if row and row[0]:

        payload = json.loads(row[0])

        for item in payload.get("Item", []) or payload.get("rfqItems", []):

            item_no = _norm(
                item.get("itemNumber")
            )

            line_no = int(
                float(
                    item.get("lineNumber", 0)
                )
            )

            key = (
                item_no,
                line_no
            )

            draft_map[key] = {

                "unit_price":
                    item.get("unitPrice"),

                "remarks":
                    item.get("vendorComments"),

                "line_status":
                    item.get("lineStatus")
            }

            if str(
                item.get("lineStatus")
            ).lower() == "true":

                valid_lines.add(key)


    # =====================================================
    # NEW / IN PROGRESS
    # =====================================================
    if status in ["New", "In Progress"]:

        for data in d365_lines:

            key = (
                _norm(data["ITEMID"]),
                int(
                    float(
                        data["LINENUM"]
                    )
                )
            )

            saved = draft_map.get(
                key,
                {}
            )

            items.append({

                "line_num":
                    data["LINENUM"],

                "item_id":
                    data["ITEMID"],

                "item_name":
                    data["NAMEALIAS"],

                "quantity":
                    data["QTYORDERED"],

                "uom":
                    data["PURCHUNIT"],

                "target_price":
                    float(
                        data["HIQ_TARGETPRICE"] or 0
                    ),

                "comments":
                    data["HIQ_COMMENTS"],

                "line_delivery_date":
                    format_ist_date_only(
                        data["DELIVERYDATE"]
                    ),

                "unit_price":
                    saved.get(
                        "unit_price"
                    ),

                "remarks":
                    saved.get(
                        "remarks"
                    ),

                "line_status":
                    (
                        "In Progress"
                        if status == "In Progress"
                        else "New"
                    )
            })


    # =====================================================
    # SUBMITTED
    # =====================================================
    elif status == "Submitted":

        for data in d365_lines:

            key = (
                _norm(data["ITEMID"]),
                int(
                    float(
                        data["LINENUM"]
                    )
                )
            )

            # ONLY show submitted lines
            if key not in valid_lines:
                continue

            items.append({

                "line_num":
                    data["LINENUM"],

                "item_id":
                    data["ITEMID"],

                "item_name":
                    data["NAME"],

                "quantity":
                    data["PURCHQTY"],

                "uom":
                    data["PURCHUNIT"],

                "unit_price":
                    float(
                        data["PURCHPRICE"] or 0
                    ),

                "net_amount":
                    float(
                        data["LINEAMOUNT"] or 0
                    ),

                "target_price":
                    float(
                        data["HIQ_TARGETPRICE"] or 0
                    ),

                "currency":
                    data["CURRENCYCODE"],

                "remarks":
                    data.get(
                        "HIQ_COMMENTS"
                    ),

                "line_delivery_date":
                    format_ist_date_only(
                        data["LINE_DELIVERY_DATE"]
                    ),

                "vendorreply_delivery_date":
                    format_ist_date_only(
                        data["VENDORREPLY_DELIVERY_DATE"]
                    ),

                "line_status":
                    "Under Review"
            })
    # draft_map = {}
    # valid_items = set()

    # with get_connection() as conn:
    #     cur = conn.cursor()
    #     cur.execute(f"""
    #         SELECT TOP 1 PAYLOADJSON
    #         FROM {RFQ_REPLIES_TABLE} WITH (NOLOCK)
    #         WHERE RFQID = ?
    #           AND VENDORACCOUNT = ?
    #         ORDER BY ID DESC
    #     """, rfq_id, vendor_account)

    #     row = cur.fetchone()

    # if row and row[0]:
    #     payload = json.loads(row[0])
    #     for item in payload.get("Item", []) or payload.get("rfqItems", []):
    #         item_no = _norm(item.get("itemNumber"))
    #         draft_map[item_no] = {
    #             "unit_price": item.get("unitPrice"),
    #             "remarks": item.get("vendorComments"),
    #             "line_status": item.get("lineStatus")
    #         }
    #         if str(item.get("lineStatus")).lower() == "true":
    #             valid_items.add(item_no)

    # if status in ["New", "In Progress"]:
    #     for data in d365_lines:
    #         saved = draft_map.get(_norm(data["ITEMID"]), {})
    #         items.append({
    #             "line_num": data["LINENUM"],
    #             "item_id": data["ITEMID"],
    #             "item_name": data["NAMEALIAS"],
    #             "quantity": data["QTYORDERED"],
    #             "uom": data["PURCHUNIT"],
    #             "target_price": float(data["HIQ_TARGETPRICE"] or 0),
    #             "comments": data["HIQ_COMMENTS"],
    #             "line_delivery_date": format_ist_date_only(data["DELIVERYDATE"]),
    #             "unit_price": saved.get("unit_price"),
    #             "remarks": saved.get("remarks"),
    #             "line_status": "In Progress" if status == "In Progress" else "New"
    #         })

    # elif status == "Submitted":
    #     for data in d365_lines:
    #         if _norm(data["ITEMID"]) not in valid_items:
    #             continue

    #         items.append({
    #             "line_num": data["LINENUM"],
    #             "item_id": data["ITEMID"],
    #             "item_name": data["NAME"],
    #             "quantity": data["PURCHQTY"],
    #             "uom": data["PURCHUNIT"],
    #             "unit_price": float(data["PURCHPRICE"] or 0),
    #             "net_amount": float(data["LINEAMOUNT"] or 0),
    #             "target_price": float(data["HIQ_TARGETPRICE"] or 0),
    #             "currency": data["CURRENCYCODE"],
    #             "remarks": data.get("HIQ_COMMENTS"),
    #             "line_delivery_date": format_ist_date_only(data["LINE_DELIVERY_DATE"]),
    #             "vendorreply_delivery_date": format_ist_date_only(data["VENDORREPLY_DELIVERY_DATE"]),
    #             "line_status": "Under Review"
    #         })

    return {
        "success": True,
        "data": {
            "rfq_id": rfq_id,
            "rfq_case_id": header["RFQCASEID"],
            "document_title": header["NAME"],
            "closing_date": format_ist_date_only(header["EXPIRYDATETIME"]),
            "items": items
        }
    }


async def fetch_rfq_detail(rfq_id: str, vendor_account: str, status: str):
    return await run_in_threadpool(
        fetch_rfq_detail_sync,
        rfq_id,
        vendor_account,
        status
    )


# ============================================================
# PO DETAILS - D365 ONLY
# ============================================================
def fetch_po_details_sync(purch_id: str, vendor_account: str):
    query = """
        SELECT
            P.PURCHID,
            P.CREATEDDATETIME AS ISSUEDATE,
            P.PURCHSTATUS AS HEADERSTATUS,
            P.ORDERACCOUNT,
            (
                SELECT TOP 1 R.RFQID
                FROM PURCHRFQLINE R WITH (NOLOCK)
                WHERE R.PURCHID = P.PURCHID
            ) AS RFQID,
            (
                SELECT TOP 1 J.PURCHORDERDATE
                FROM VENDPURCHORDERJOUR J WITH (NOLOCK)
                WHERE J.PURCHID = P.PURCHID
                ORDER BY J.PURCHORDERDATE DESC
            ) AS CONFIRMEDDATE,
            L.LINENUMBER,
            L.DELIVERYDATE AS LINEDELIVERYDATE,
            L.ITEMID AS ITEM,
            EP.SEARCHNAME AS DESCRIPTION,
            PC.NAME AS PROCUREMENTCATEGORY,
            L.QTYORDERED AS QTY,
            L.PURCHUNIT AS UOM,
            L.PURCHPRICE AS UNITPRICE,
            L.LINEAMOUNT AS NETAMOUNT,
            L.PURCHSTATUS AS LINESTATUS,
            L.CURRENCYCODE
        FROM PURCHTABLE P WITH (NOLOCK)
        LEFT JOIN PURCHLINE L WITH (NOLOCK)
            ON L.PURCHID = P.PURCHID
           AND L.ISDELETED = 0
        LEFT JOIN ECORESPRODUCT EP WITH (NOLOCK)
            ON EP.DISPLAYPRODUCTNUMBER = L.ITEMID
        LEFT JOIN EcoResCategory PC WITH (NOLOCK)
            ON PC.RECID = L.PROCUREMENTCATEGORY
        WHERE P.PURCHID = ?
          AND P.ORDERACCOUNT = ?
    """

    with get_d365_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, purch_id, vendor_account)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()

    return [dict(zip(cols, row)) for row in rows]


def get_po_details(purch_id: str, vendor_account: str):
    data = fetch_po_details_sync(purch_id, vendor_account)

    if not data:
        return None

    first_row = data[0]
    total_value = sum(float(row["NETAMOUNT"] or 0) for row in data)

    header = {
        "po_number": first_row["PURCHID"],
        "rfq_number": first_row["RFQID"],
        "header_status": PURCH_STATUS_MAP.get(first_row["HEADERSTATUS"], "Unknown"),
        "issue_date": format_ist_date_only(first_row["ISSUEDATE"]),
        "confirmed_date": format_ist_date_only(first_row["CONFIRMEDDATE"]),
        "total_value": total_value
    }

    lines = []
    for row in data:
        lines.append({
            "line_number": row["LINENUMBER"],
            "item": row["ITEM"],
            "description": row["DESCRIPTION"],
            "procurement_category": row["PROCUREMENTCATEGORY"] or "-",
            "quantity": float(row["QTY"] or 0),
            "uom": row["UOM"],
            "unit_price": float(row["UNITPRICE"] or 0),
            "net_amount": float(row["NETAMOUNT"] or 0),
            "expected_delivery_date": format_ist_date_only(row["LINEDELIVERYDATE"]) if row["LINEDELIVERYDATE"] else None,
            "currency": row["CURRENCYCODE"]
        })

    return {
        "header": header,
        "lines": lines
    }



# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# from typing import List, Dict, Any, Optional, Set
# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.remainingdate import calculate_days_left, format_expiry_label
# from app.utils.lastused_time import format_last_edited
# import json 
# from fastapi.concurrency import run_in_threadpool
# def get_vendor_dashboard_kpis_sync(vendor_account: str):

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # =========================
#         # 1. MATERIALS SUPPORTED
#         # =========================
#         cur.execute("""
#             SELECT COUNT(DISTINCT ITEMID)
#             FROM PDSAPPROVEDVENDORLIST
#             WHERE PDSAPPROVEDVENDOR = ?
#               AND VALIDFROM <= GETUTCDATE()
#               AND VALIDTO >= GETUTCDATE()
#         """, (vendor_account,))
#         materials = cur.fetchone()[0] or 0

#         # =========================
#         # 2. OPEN RFQs
#         # =========================
#         cur.execute("""
#               SELECT COUNT(DISTINCT T.RFQID) AS total_count
# FROM PurchRFQTable T
# JOIN PurchRFQCaseTable C
#     ON T.RFQCASEID = C.RFQCASEID
# LEFT JOIN HIQ_VENDORRFQREPLIES R
#     ON T.RFQID = R.RFQ_ID
#     AND T.VENDACCOUNT = R.VENDOR_ACCOUNT
# WHERE T.VENDACCOUNT = ?
#   AND T.DATAAREAID = 'hi-q'
#   AND CAST(C.EXPIRYDATETIME AS DATE) >= CAST(GETDATE() AS DATE)
#   --AND C.EXPIRYDATETIME >= GETUTCDATE()
#   AND (
#         R.SUBMISSION_STATUS IN (0,1)
#         OR R.RFQ_ID IS NULL
#       )
#         """, (vendor_account,))
#         open_rfqs = cur.fetchone()[0] or 0

#         # =========================
#         # 3. SUBMITTED RFQs
#         # =========================
#         rfq_data = fetch_vendor_rfqs_sync(vendor_account)

#         submitted_rfqs = len([
#             r for r in rfq_data.get("rfqs", [])
#             if r.get("status") == "Submitted"
#         ])
#         # =========================
#         # 4. OPEN POs
#         # =========================
#         cur.execute("""
#             SELECT COUNT(DISTINCT PURCHID)
#             FROM PURCHTABLE
#             WHERE ORDERACCOUNT = ?
#               AND PURCHSTATUS IN (0,1)
#         """, (vendor_account,))
#         open_pos = cur.fetchone()[0] or 0

#         # =========================
#         # 5. STATUS
#         # =========================
#         cur.execute("""
#             SELECT TOP 1 STATUS
#             FROM HIQ_VendorPortalUser
#             WHERE VENDOR_ACCOUNT = ?
#         """, (vendor_account,))
#         row = cur.fetchone()

#         status = "Inactive"
#         if row and row[0] in (1, 2):
#             status = "Active"

#         # =========================
#         # FINAL RESPONSE
#         # =========================
#         return {
#             "materials_supported": materials,
#             "open_rfqs": open_rfqs,
#             "open_pos": open_pos,
#             "submitted_rfqs": submitted_rfqs,
#             "status": status
#         }

# async def get_vendor_dashboard_kpis(vendor_account: str):
#     return await run_in_threadpool(get_vendor_dashboard_kpis_sync, vendor_account)   
# def fetch_bid_materials_sync(vendor_account: str):

#     query = """
#         SELECT
#             LTRIM(RTRIM(P.ITEMID))  AS ITEMID,
#             I.NAMEALIAS,
#             P.VALIDFROM,
#             P.VALIDTO
#         FROM PDSAPPROVEDVENDORLIST P WITH (NOLOCK)
#         INNER JOIN INVENTTABLE I WITH (NOLOCK)
#             ON LTRIM(RTRIM(I.ITEMID)) = LTRIM(RTRIM(P.ITEMID))
#             AND I.DATAAREAID = 'hi-q'
#         WHERE P.PDSAPPROVEDVENDOR = ?
#           AND P.DATAAREAID        = 'hi-q'
#           AND P.VALIDFROM        <= GETUTCDATE()
#           AND P.VALIDTO          >= GETUTCDATE()
#         ORDER BY LTRIM(RTRIM(P.ITEMID))
#     """

#     with get_connection() as conn:
#         cursor = conn.cursor()
#         cursor.execute(query, vendor_account)

#         columns = [col[0] for col in cursor.description]
#         rows = cursor.fetchall()

#         result = []
#         for row in rows:
#             data = dict(zip(columns, row))
#             days_left = calculate_days_left(data["VALIDTO"])  # call it once, reuse

#             result.append({
#                 "material_id": data["ITEMID"],
#                 "material_description": data["NAMEALIAS"],
#                 "valid_from": format_ist_date_only(data["VALIDFROM"]),
#                 "expiry_date": format_ist_date_only(data["VALIDTO"]),
#                 "days_left": days_left,
#                 "expiry_status": True if isinstance(days_left, int) and days_left < 30 else False
#             })
#         # for row in rows:
#         #     data = dict(zip(columns, row))

#         #     result.append({
#         #         "material_id": data["ITEMID"],
#         #         "material_description": data["NAMEALIAS"],
#         #         "valid_from": format_ist_date_only(data["VALIDFROM"]),
#         #         "expiry_date": format_ist_date_only(data["VALIDTO"]),
#         #         "days_left":calculate_days_left(data["VALIDTO"]),
#         #         "expiry_status": True if calculate_days_left < 30 else False
#         #    })

#         return result

# async def fetch_bid_materials(vendor_account: str):
#     return await run_in_threadpool(fetch_bid_materials_sync, vendor_account)
# #profile Details
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
# #rfq active
# def _get_valid_payload_items(rfq_id: str, vendor_account: str, cur) -> Optional[Set[str]]:
#     try:
#         cur.execute("""
#             SELECT TOP 1 PAYLOAD_JSON
#             FROM HIQ_VENDORRFQREPLIES WITH (NOLOCK)
#             WHERE UPPER(RFQ_ID)=UPPER(?) AND UPPER(VENDOR_ACCOUNT)=UPPER(?)
#             ORDER BY ID DESC
#         """, (rfq_id, vendor_account))

#         row = cur.fetchone()
#         if not row or not row[0]:
#             return None

#         payload = json.loads(row[0])
#         items = payload.get("Item", [])

#         has_line_status = any("lineStatus" in i for i in items)

#         if not has_line_status:
#             return None  # OLD RFQ

#         valid = set()
#         for i in items:
#             if str(i.get("lineStatus")).lower() == "true":
#                 valid.add(str(i.get("itemNumber")).strip().upper())

#         return valid

#     except:
#         return None
# def fetch_vendor_rfqs_sync(vendor_account: str):
#     """
#     Returns New + In Progress RFQs combined in one list.
#     New        = not yet replied, vendor approved for at least one item
#     In Progress = draft or failed reply already saved
#     """


#     new_query = """
#         SELECT
#             L.RFQCASEID,
#             T.RFQID,
#             L.NAME              AS RFQNAME,
#             L.EXPIRYDATETIME    AS CLOSING_DATE,
#             L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
#             PT.DESCRIPTION      AS PAYMENT_TERM,
#             PM.NAME             AS PAYMENT_MODE,
#             DM.TXT              AS DELIVERY_MODE,
#             DT.TXT              AS DELIVERY_TERM

#         FROM PurchRFQCaseTable L WITH (NOLOCK)

#         INNER JOIN PurchRFQTable T WITH (NOLOCK)
#             ON  T.RFQCASEID   = L.RFQCASEID
#             AND T.VENDACCOUNT = ?

#         LEFT JOIN PAYMTERM PT WITH (NOLOCK)
#             ON L.PAYMENT = PT.PAYMTERMID

#         LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
#             ON L.PAYMMODE = PM.PAYMMODE

#         LEFT JOIN DLVMODE DM WITH (NOLOCK)
#             ON L.DLVMODE = DM.CODE

#         LEFT JOIN DLVTERM DT WITH (NOLOCK)
#             ON L.DLVTERM = DT.CODE

#         --WHERE L.EXPIRYDATETIME >= GETUTCDATE()
#         WHERE CAST(L.EXPIRYDATETIME AS DATE) >= CAST(GETDATE() AS DATE)

#           -- vendor approved for at least one item
#           AND EXISTS (
#               SELECT 1
#               FROM PurchRFQCaseLine CL WITH (NOLOCK)
#               INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
#                   ON  AVL.ITEMID            = CL.ITEMID
#                   AND AVL.PDSAPPROVEDVENDOR = T.VENDACCOUNT
#                   AND AVL.VALIDFROM        <= GETUTCDATE()
#                   AND AVL.VALIDTO          >= GETUTCDATE()
#               WHERE CL.RFQCASEID = L.RFQCASEID
#           )

#           -- NOT yet replied 
#           AND NOT EXISTS (
#               SELECT 1
#               FROM HIQ_VendorRFQReplies R WITH (NOLOCK)
#               WHERE R.RFQ_ID         = T.RFQID
#                 AND R.VENDOR_ACCOUNT = T.VENDACCOUNT
#           )

#         ORDER BY L.EXPIRYDATETIME ASC
#     """
    
#     # ── IN PROGRESS RFQs ─────────────────────────────────────
#     inprogress_query = """
#     SELECT
#         L.RFQCASEID,
#         T.RFQID,
#         L.NAME              AS RFQNAME,
#         L.EXPIRYDATETIME    AS CLOSING_DATE,
#         L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
#         PT.DESCRIPTION      AS PAYMENT_TERM,
#         PM.NAME             AS PAYMENT_MODE,
#         DM.TXT              AS DELIVERY_MODE,
#         DT.TXT              AS DELIVERY_TERM,
#         R.CREATED_AT        AS LAST_EDITED,
#         R.ID                AS REPLY_ID,
#         R.CONFIRM_SAVE      AS CONFIRM_SAVE

#     FROM PurchRFQCaseTable L WITH (NOLOCK)

#     INNER JOIN PurchRFQTable T WITH (NOLOCK)
#         ON  T.RFQCASEID   = L.RFQCASEID
#         AND T.VENDACCOUNT = ?

#     -- FIX: Always pick latest record (no status filter here)
#     INNER JOIN (
#         SELECT *,
#             ROW_NUMBER() OVER (
#                 PARTITION BY RFQ_ID, VENDOR_ACCOUNT
#                 ORDER BY ID DESC
#             ) AS RN
#         FROM HIQ_VendorRFQReplies WITH (NOLOCK)
#     ) R
#         ON  R.RFQ_ID         = T.RFQID
#         AND R.VENDOR_ACCOUNT = T.VENDACCOUNT
#         AND R.RN             = 1

#     LEFT JOIN PAYMTERM PT WITH (NOLOCK)
#         ON L.PAYMENT = PT.PAYMTERMID

#     LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
#         ON L.PAYMMODE = PM.PAYMMODE

#     LEFT JOIN DLVMODE DM WITH (NOLOCK)
#         ON L.DLVMODE = DM.CODE

#     LEFT JOIN DLVTERM DT WITH (NOLOCK)
#         ON L.DLVTERM = DT.CODE

#     -- FIX: Filter AFTER picking latest
#     WHERE 
#         R.SUBMISSION_STATUS IN (0, 2)
#         AND (
#     CAST(L.EXPIRYDATETIME AS DATE) >= CAST(GETDATE() AS DATE)
#     OR R.SUBMISSION_STATUS = 2
# )

#     ORDER BY 
#         L.EXPIRYDATETIME ASC
# """  
#     submitted_query = """
#         SELECT
#             L.RFQCASEID,
#             T.RFQID,
#             L.NAME              AS RFQNAME,
#             L.EXPIRYDATETIME    AS CLOSING_DATE,
#             L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
#             PT.DESCRIPTION      AS PAYMENT_TERM,
#             PM.NAME             AS PAYMENT_MODE,
#             DM.TXT              AS DELIVERY_MODE,
#             DT.TXT              AS DELIVERY_TERM,
#             MAX(R.SEND_TO_D365_AT) AS SUBMITTED_ON

#         FROM HIQ_VENDORRFQREPLIES R WITH (NOLOCK)

#         INNER JOIN PurchRFQTable T WITH (NOLOCK)
#             ON T.RFQID = R.RFQ_ID
#             AND T.VENDACCOUNT = R.VENDOR_ACCOUNT

#         INNER JOIN PurchRFQCaseTable L WITH (NOLOCK)
#             ON L.RFQCASEID = R.RFQ_CASE_ID

#         LEFT JOIN PAYMTERM PT ON L.PAYMENT = PT.PAYMTERMID
#         LEFT JOIN VENDPAYMMODETABLE PM ON L.PAYMMODE = PM.PAYMMODE
#         LEFT JOIN DLVMODE DM ON L.DLVMODE = DM.CODE
#         LEFT JOIN DLVTERM DT ON L.DLVTERM = DT.CODE

#         WHERE R.VENDOR_ACCOUNT = ?
#           AND R.SUBMISSION_STATUS = 1

#         GROUP BY
#             L.RFQCASEID,
#             T.RFQID,
#             L.NAME,
#             L.EXPIRYDATETIME,
#             L.DELIVERYDATE,
#             PT.DESCRIPTION,
#             PM.NAME,
#             DM.TXT,
#             DT.TXT

#         ORDER BY MAX(R.SEND_TO_D365_AT) DESC
#         """

 
#     result = []

#     with get_connection() as conn:
#         cursor = conn.cursor()

#         # Fetch NEW
#         cursor.execute(new_query, vendor_account)
#         cols = [c[0] for c in cursor.description]
#         for row in cursor.fetchall():
#             data = dict(zip(cols, row))
#             result.append({
#                 "rfq_id":        data["RFQID"],
#                 "rfq_case_id":   data["RFQCASEID"],
#                 "rfq_name":      data["RFQNAME"]       or "-",
#                 "expiry_date":   format_ist_date_only(data["CLOSING_DATE"]),
#                 "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
#                 "payment_term":  data["PAYMENT_TERM"]  or "-",
#                 "payment_mode":  data["PAYMENT_MODE"]  or "-",
#                 "delivery_mode": data["DELIVERY_MODE"] or "-",
#                 "delivery_term": data["DELIVERY_TERM"] or "-",
#                 "dates_left":    calculate_days_left(data["CLOSING_DATE"]),
#                 "last_edited":   None,
#                 "reply_id":      None,
#                 "confirm_save":  None,
#                 "status":        "New",
#             })

#         # Fetch IN PROGRESS
#         cursor.execute(inprogress_query, vendor_account)
#         cols = [c[0] for c in cursor.description]
#         for row in cursor.fetchall():
#             data        = dict(zip(cols, row))
#             last_edited = data["LAST_EDITED"]
#             result.append({
#                 "rfq_id":        data["RFQID"],
#                 "rfq_case_id":   data["RFQCASEID"],
#                 "rfq_name":      data["RFQNAME"]       or "-",
#                 "expiry_date":   format_ist_date_only(data["CLOSING_DATE"]),
#                 "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
#                 "payment_term":  data["PAYMENT_TERM"]  or "-",
#                 "payment_mode":  data["PAYMENT_MODE"]  or "-",
#                 "delivery_mode": data["DELIVERY_MODE"] or "-",
#                 "delivery_term": data["DELIVERY_TERM"] or "-",
#                 "dates_left":    calculate_days_left(data["CLOSING_DATE"]),
#                 "last_edited":   format_last_edited(last_edited) if last_edited else None,
#                 "reply_id":      data["REPLY_ID"],
#                 "confirm_save":  data["CONFIRM_SAVE"],
#                 "status":        "In Progress",
#             })


#      # Fetch SUBMITTED
#         cursor.execute(submitted_query, vendor_account)
#         cols = [c[0] for c in cursor.description]

#         submitted_result = []

#         for row in cursor.fetchall():
#             data = dict(zip(cols, row))
#             rfq_id = data["RFQID"]

#             # ============================
#             # 🔥 STEP 1: Get payload valid items
#             # ============================
#             valid_items = _get_valid_payload_items(rfq_id, vendor_account, cursor)

#             if not valid_items:
#                 continue

#             # ============================
#             # 🔥 STEP 2: Get RFQ lines
#             # ============================
#             cursor.execute("""
#                 SELECT ITEMID, STATUS
#                 FROM PURCHRFQLINE WITH (NOLOCK)
#                 WHERE RFQID = ?
#                   AND DATAAREAID = 'hi-q'
#             """, (rfq_id,))

#             lines = cursor.fetchall()

#             is_valid_submitted = False

#             for l in lines:
#                 item = str(l[0]).strip().upper()   # ✅ HANDLE SPACE + CASE
#                 status = l[1]

#                 # ✅ ONLY consider payload active items
#                 if item in valid_items:
#                     if status < 3:
#                         is_valid_submitted = True
#                         break

#             # ❌ Skip if no valid line
#             if not is_valid_submitted:
#                 continue

#             # ============================
#             # ✅ ADD TO RESULT
#             # ============================
#             submitted_result.append({
#                 "rfq_id":        data["RFQID"],
#                 "rfq_case_id":   data["RFQCASEID"],
#                 "rfq_name":      data["RFQNAME"] or "-",
#                 "expiry_date":   format_ist_date_only(data["CLOSING_DATE"]),
#                 "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
#                 "payment_term":  data["PAYMENT_TERM"] or "-",
#                 "payment_mode":  data["PAYMENT_MODE"] or "-",
#                 "delivery_mode": data["DELIVERY_MODE"] or "-",
#                 "delivery_term": data["DELIVERY_TERM"] or "-",
#                 "dates_left":    calculate_days_left(data["CLOSING_DATE"]),
#                 "last_edited":   format_ist_date_only(data["SUBMITTED_ON"]),
#                 "reply_id":      None,
#                 "confirm_save":  None,
#                 "status":        "Submitted",
#             })

#         result.extend(submitted_result)
#         # cursor.execute(submitted_query, vendor_account)
#         # cols = [c[0] for c in cursor.description]

#         # for row in cursor.fetchall():
#         #     data = dict(zip(cols, row))

#         #     result.append({
#         #         "rfq_id":        data["RFQID"],
#         #         "rfq_case_id":   data["RFQCASEID"],
#         #         "rfq_name":      data["RFQNAME"] or "-",
#         #         "expiry_date":   format_ist_date_only(data["CLOSING_DATE"]),
#         #         "delivery_date": format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
#         #         "payment_term":  data["PAYMENT_TERM"] or "-",
#         #         "payment_mode":  data["PAYMENT_MODE"] or "-",
#         #         "delivery_mode": data["DELIVERY_MODE"] or "-",
#         #         "delivery_term": data["DELIVERY_TERM"] or "-",
#         #         "dates_left":    calculate_days_left(data["CLOSING_DATE"]),
#         #         "last_edited":   format_ist_date_only(data["SUBMITTED_ON"]),
#         #         "reply_id":      None,
#         #         "confirm_save":  None,
#         #         "status":        "Submitted",
#         #     })

#     # Sort combined by expiry date
#     result.sort(key=lambda x: x["expiry_date"] or "")

#     return {
#         "total": len(result),
#         "rfqs":  result
#     } 


# async def fetch_vendor_rfqs(vendor_account: str):
#     return await run_in_threadpool(fetch_vendor_rfqs_sync, vendor_account)
# def fetch_po_list_sync(vendor_account: str):
#     query = """
#         SELECT 
#     P.PURCHID,
#     R.RFQID,
#     --P.DOCUMENTSTATE,
#     P.CREATEDDATETIME,
#     SUM(L.LINEAMOUNT) AS TOTAL_AMOUNT,
#     p.PURCHSTATUS,
#     P.CURRENCYCODE

# FROM PURCHTABLE P WITH (NOLOCK)

# LEFT JOIN PURCHLINE L WITH (NOLOCK)
#     ON L.PURCHID = P.PURCHID

# LEFT JOIN PURCHRFQLINE R WITH (NOLOCK)
#     ON R.PURCHID = P.PURCHID  
# WHERE  P.ORDERACCOUNT = ? 
# ---P.DOCUMENTSTATE in (40,30) 
# GROUP BY 
#     P.PURCHID,
#     R.RFQID,
#     --P.DOCUMENTSTATE,
#     p.PURCHSTATUS,
#     P.CREATEDDATETIME,
#     P.CURRENCYCODE 
# ORDER BY P.CREATEDDATETIME DESC
#     """

#     with get_connection() as conn:
#         cursor = conn.cursor()
#         cursor.execute(query, vendor_account)

#         columns = [col[0] for col in cursor.description]
#         rows = cursor.fetchall()
#         if not rows:
#             return []  

#         return [dict(zip(columns, row)) for row in rows]

# # po details

# def get_po_list_sync(vendor_account: str):
#     data = fetch_po_list_sync(vendor_account)

#     result = []
#     if not data:
#             return []  

#     # DocumentState Mapping
#     DOCUMENT_STATE_MAP = {
#         0: "Draft",
#         10: "InReview",
#         20: "Rejected",
#         30: "Approved",
#         35: "InExternalReview",
#         40: "Confirmed",
#         50: "Finalized"
#     }
#     PURCH_STATUS_MAP = {
#     0: "Open",
#     1: "Open",
#     2: "Received",
#     3: "Invoiced",
#     4: "Cancelled"
# }

#     for row in data:
#         result.append({
#             "po_id": row["PURCHID"],
#             "rfq_id": row["RFQID"],
#             "created_date": format_ist_date_only(row["CREATEDDATETIME"]),
#             # "status": DOCUMENT_STATE_MAP.get(row["DOCUMENTSTATE"], "Unknown"),
#             "total_amount": float(row["TOTAL_AMOUNT"] or 0),
#             "purch_state": PURCH_STATUS_MAP.get(row["PURCHSTATUS"]),
#             "currency":row["CURRENCYCODE"]
#         })

#     return result 


# from app.db.base import get_connection
# from app.utils.date_utils import format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# import json

# from datetime import datetime
# import json
# def fetch_rfq_detail_sync(rfq_id: str, vendor_account: str, status: str):

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # =========================
#         # HEADER
#         # =========================
#         cur.execute("""
#             SELECT
#                 L.RFQCASEID,
#                 T.RFQID,
#                 L.NAME,
#                 L.EXPIRYDATETIME,
#                 L.CREATEDDATETIME,
#                 L.DELIVERYDATE,
#                 L.HIQ_COMMENTS AS COMMENTS,
#                 PT.DESCRIPTION,
#                 PM.NAME,
#                 DM.TXT,
#                 DT.TXT
#             FROM PurchRFQCaseTable L
#             INNER JOIN PurchRFQTable T
#                 ON T.RFQCASEID = L.RFQCASEID
#                AND T.VENDACCOUNT = ?
#             LEFT JOIN PAYMTERM PT ON L.PAYMENT = PT.PAYMTERMID
#             LEFT JOIN VENDPAYMMODETABLE PM ON L.PAYMMODE = PM.PAYMMODE
#             LEFT JOIN DLVMODE DM ON L.DLVMODE = DM.CODE
#             LEFT JOIN DLVTERM DT ON L.DLVTERM = DT.CODE
#             WHERE T.RFQID = ?
#         """, (vendor_account, rfq_id))

#         header_row = cur.fetchone()
#         cols = [c[0] for c in cur.description]
#         header = dict(zip(cols, header_row))

#         items = []

#         # ==========================================================
#         # 🔥 NEW / IN PROGRESS
#         # ==========================================================
#         if status in ["New", "In Progress"]:

#             cur.execute("""
#                 SELECT
#                     RL.LINENUM,
#                     RL.ITEMID,
#                     IT.NAMEALIAS,
#                     RL.QTYORDERED,
#                     RL.PURCHUNIT,
#                     RL.HIQ_TARGETPRICE,
#                     RL.HIQ_COMMENTS,
#                     RL.CURRENCYCODE,
#                     RL.DELIVERYDATE
#                 FROM PurchRFQLine RL
#                 LEFT JOIN INVENTTABLE IT ON IT.ITEMID = RL.ITEMID
#                 WHERE RL.RFQID = ?

#                 -- ✅ FIX: Approved materials only
#                 AND EXISTS (
#                     SELECT 1
#                     FROM PDSAPPROVEDVENDORLIST AVL
#                     WHERE AVL.ITEMID = RL.ITEMID
#                       AND AVL.PDSAPPROVEDVENDOR = ?
#                       AND AVL.VALIDFROM <= GETUTCDATE()
#                       AND AVL.VALIDTO >= GETUTCDATE()
#                 )

#                 ORDER BY RL.LINENUM
#             """, (rfq_id, vendor_account))

#             rows = cur.fetchall()
#             cols = [c[0] for c in cur.description]

#             draft_map = {}

#             if status == "In Progress":
#                 cur.execute("""
#                     SELECT TOP 1 PAYLOAD_JSON
#                     FROM HIQ_VENDORRFQREPLIES
#                     WHERE RFQ_ID = ?
#                       AND VENDOR_ACCOUNT = ?
#                       AND SUBMISSION_STATUS IN (0,2)
#                     ORDER BY ID DESC
#                 """, (rfq_id, vendor_account))

#                 draft = cur.fetchone()
#                 if draft:
#                     payload = json.loads(draft[0])

#                     for item in payload.get("Item", []):
#                         draft_map[item["itemNumber"]] = {
#                             "unit_price": item.get("unitPrice"),
#                             "remarks": item.get("vendorComments"),
#                             "line_status": item.get("lineStatus")
#                         }

#             for r in rows:
#                 data = dict(zip(cols, r))
#                 saved = draft_map.get(data["ITEMID"], {})

#                 items.append({
#                     "line_num": data["LINENUM"],
#                     "item_id": data["ITEMID"],
#                     "item_name": data["NAMEALIAS"],
#                     "quantity": data["QTYORDERED"],
#                     "uom": data["PURCHUNIT"],
#                     "target_price": float(data["HIQ_TARGETPRICE"] or 0),
#                     "comments": data["HIQ_COMMENTS"],
#                     "line_delivery_date": format_ist_date_only(data["DELIVERYDATE"]),
#                     "unit_price": saved.get("unit_price"),
#                     "remarks": saved.get("remarks"),
#                     "line_status": "In Progress" if status == "In Progress" else "New"
#                 })

#         # ==========================================================
#         # 🔥 SUBMITTED
#         # ==========================================================
#         elif status == "Submitted":

#             # payload filtering
#             cur.execute("""
#                 SELECT TOP 1 PAYLOAD_JSON
#                 FROM HIQ_VENDORRFQREPLIES
#                 WHERE RFQ_ID = ?
#                   AND VENDOR_ACCOUNT = ?
#                 ORDER BY ID DESC
#             """, (rfq_id, vendor_account))

#             row = cur.fetchone()
#             valid_items = set()

#             if row:
#                 payload = json.loads(row[0])
#                 for item in payload.get("Item", []):
#                     if str(item.get("lineStatus")).lower() == "true":
#                         valid_items.add(item.get("itemNumber"))

#             cur.execute("""
#                 SELECT
#                     RL.LINENUM,
#                     PL.ITEMID,
#                     RL.NAME,
#                     RL.PURCHQTY,
#                     RL.PURCHUNIT,
#                     RL.PURCHPRICE,
#                     RL.LINEAMOUNT,
#                     RL.DELIVERYDATE,
#                     RL.HIQ_COMMENTS,
#                     PL.HIQ_TARGETPRICE,
#                     PL.CURRENCYCODE,
#                     RL.DELIVERYDATE AS VENDORREPLY_DELIVERY_DATE,
#                     PL.DELIVERYDATE AS LINE_DELIVERY_DATE,
#                     PL.STATUS
#                 FROM PURCHRFQREPLYLINE RL
#                 INNER JOIN PURCHRFQLINE PL
#                     ON PL.RECID = RL.RFQLINERECID
#                 WHERE RL.RFQID = ?
#                   AND PL.STATUS < 3

#                   -- ✅ FIX: approved materials
#                   AND EXISTS (
#                       SELECT 1
#                       FROM PDSAPPROVEDVENDORLIST AVL
#                       WHERE AVL.ITEMID = PL.ITEMID
#                         AND AVL.PDSAPPROVEDVENDOR = ?
#                         AND AVL.VALIDFROM <= GETUTCDATE()
#                         AND AVL.VALIDTO >= GETUTCDATE()
#                   )
#             """, (rfq_id, vendor_account))

#             rows = cur.fetchall()
#             cols = [c[0] for c in cur.description]

#             for r in rows:
#                 data = dict(zip(cols, r))

#                 if data["ITEMID"] not in valid_items:
#                     continue

#                 items.append({
#                     "line_num": data["LINENUM"],
#                     "item_id": data["ITEMID"],
#                     "item_name": data["NAME"],
#                     "quantity": data["PURCHQTY"],
#                     "uom": data["PURCHUNIT"],
#                     "unit_price": float(data["PURCHPRICE"] or 0),
#                     "net_amount": float(data["LINEAMOUNT"] or 0),
#                     "target_price": float(data["HIQ_TARGETPRICE"] or 0),
#                     "currency": data["CURRENCYCODE"],
#                     "remarks": data.get("HIQ_COMMENTS"),
#                     "line_delivery_date": format_ist_date_only(data["LINE_DELIVERY_DATE"]),
#                     "vendorreply_delivery_date": format_ist_date_only(data["VENDORREPLY_DELIVERY_DATE"]),
#                     "line_status": "Under Review"
#                 })

#     return {
#         "success": True,
#         "data": {
#             "rfq_id": rfq_id,
#             "rfq_case_id": header["RFQCASEID"],
#             "document_title": header["NAME"],
#             "closing_date": format_ist_date_only(header["EXPIRYDATETIME"]),
#             "items": items
#         }
#     }
# async def fetch_rfq_detail(rfq_id: str, vendor_account: str, status: str):
#     return await run_in_threadpool(
#         fetch_rfq_detail_sync, rfq_id, vendor_account, status
#     )
# # po line details
# from app.utils.date_utils import format_ist_date_only
# from app.db.base import get_connection
 
 
# # ---------------------------------------------------------
# # PurchStatus Enum Mapping (Header & Line Both Use Same)
# # ---------------------------------------------------------
# PURCH_STATUS_MAP = {
#     0: "Confirmed",
#     1: "Confirmed",
#     2: "Received",
#     3: "Invoiced",
#     4: "Cancelled"
# }
 
# # ---------------------------------------------------------
# # Fetch Data From Database
# # ---------------------------------------------------------
# def fetch_po_details_sync(purch_id: str, vendor_account: str):
#     query="""
# SELECT
#     P.PURCHID,
#     P.CREATEDDATETIME AS ISSUEDATE,
#     P.PURCHSTATUS AS HEADERSTATUS,
#     P.ORDERACCOUNT,

#     (
#         SELECT TOP 1 R.RFQID
#         FROM PURCHRFQLINE R
#         WHERE R.PURCHID = P.PURCHID
#     ) AS RFQID,

#     (
#         SELECT TOP 1 J.PURCHORDERDATE
#         FROM VENDPURCHORDERJOUR J
#         WHERE J.PURCHID = P.PURCHID
#         ORDER BY J.PURCHORDERDATE DESC
#     ) AS CONFIRMEDDATE,

#     L.LINENUMBER,
#     L.DELIVERYDATE AS LINEDELIVERYDATE,
#     L.ITEMID AS ITEM,
#     EP.SEARCHNAME AS DESCRIPTION,
#     PC.NAME AS PROCUREMENTCATEGORY,   -- ✅ NEW
#     L.QTYORDERED AS QTY,
#     L.PURCHUNIT AS UOM,
#     L.PURCHPRICE AS UNITPRICE,
#     L.LINEAMOUNT AS NETAMOUNT,
#     L.PURCHSTATUS AS LINESTATUS,
#     L.CURRENCYCODE

# FROM PURCHTABLE P WITH (NOLOCK)

# LEFT JOIN PURCHLINE L WITH (NOLOCK)
#     ON L.PURCHID = P.PURCHID
#     AND L.ISDELETED = 0 

# LEFT JOIN ECORESPRODUCT EP WITH (NOLOCK)
#     ON EP.DISPLAYPRODUCTNUMBER = L.ITEMID

# LEFT JOIN EcoResCategory PC WITH (NOLOCK)  
#     ON PC.RECID = L.PROCUREMENTCATEGORY

# WHERE P.PURCHID = ?
#   AND P.ORDERACCOUNT = ?
# """

#     with get_connection() as conn:
#         cursor = conn.cursor()
#         cursor.execute(query, purch_id, vendor_account)
 
#         columns = [col[0] for col in cursor.description]
#         rows = cursor.fetchall()
 
#         return [dict(zip(columns, row)) for row in rows]
 
 
# # ---------------------------------------------------------
# # Main Service Function
# # ---------------------------------------------------------
# def get_po_details(purch_id: str, vendor_account: str):
 
#     data = fetch_po_details_sync(purch_id, vendor_account)
 
#     if not data:
#         return None
 
#     first_row = data[0]
 
#     # ---------------------------------------------------------
#     # Calculate Total From Line Amounts
#     # ---------------------------------------------------------
#     total_value = sum(float(row["NETAMOUNT"] or 0) for row in data)
 
#     # ---------------------------------------------------------
#     # Header Section
#     # ---------------------------------------------------------
#     header = {
#         "po_number": first_row["PURCHID"],
#         "rfq_number": first_row["RFQID"],
#         "header_status": PURCH_STATUS_MAP.get(
#             first_row["HEADERSTATUS"], "Unknown"
#         ),
#         "issue_date": format_ist_date_only(first_row["ISSUEDATE"]),
#         "confirmed_date": format_ist_date_only(first_row["CONFIRMEDDATE"]),
#         "total_value": total_value
#     }
 
#     # ---------------------------------------------------------
#     # Line Section (Delivery Date From Line Only)
#     # ---------------------------------------------------------
#     lines = []
 
#     for row in data:
#         lines.append({
#             "line_number": row["LINENUMBER"],
#             "item": row["ITEM"],
#             "description": row["DESCRIPTION"],
#             "procurement_category": row["PROCUREMENTCATEGORY"] or "-",
#             "quantity": float(row["QTY"] or 0),
#             "uom": row["UOM"],
#             "unit_price": float(row["UNITPRICE"] or 0),
#             "net_amount": float(row["NETAMOUNT"] or 0),
#             "expected_delivery_date": format_ist_date_only(
#                 row["LINEDELIVERYDATE"]
#             ) if row["LINEDELIVERYDATE"] else None,
#             "currency":row["CURRENCYCODE"]
#             # "line_status": PURCH_STATUS_MAP.get(
#             #     row["LINESTATUS"], "Unknown"
#             # )
#         })
 
#     return {
#         "header": header,
#         "lines": lines
#     }
 
# async def fetch_po_list(vendor_account: str):
#     return await run_in_threadpool(fetch_po_list_sync, vendor_account)
# async def get_po_list(vendor_account: str):
#     return await run_in_threadpool(get_po_list_sync, vendor_account)
