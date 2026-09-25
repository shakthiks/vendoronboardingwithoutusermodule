import json

from typing import (
    List,
    Dict,
    Any
)

from fastapi.concurrency import (
    run_in_threadpool
)

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
# VENDOR PROFILE
# ============================================================
def fetch_vendor_profile_sync(
    vendor_account: str
):

    profile = {
        "email":   None,
        "phone":   None,
        "address": None,
        "name":    None,
        "city":    None
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
            profile["name"]    = row.NAME
            profile["city"]    = row.CITY

    return profile


async def fetch_vendor_profile(
    vendor_account: str
):
    return await run_in_threadpool(
        fetch_vendor_profile_sync,
        vendor_account
    )


# ============================================================
# APPROVED ITEMS
# ============================================================
def _get_approved_items(
    vendor_account: str
) -> List[str]:

    try:

        with get_d365_connection() as conn:

            cur = conn.cursor()

            cur.execute("""
                SELECT ITEMID

                FROM PDSAPPROVEDVENDORLIST
                WITH (NOLOCK)

                WHERE PDSAPPROVEDVENDOR = ?
                  AND DATAAREAID        = 'hi-q'
                  AND VALIDFROM        <= GETUTCDATE()
                  AND VALIDTO          >= GETUTCDATE()
            """, (vendor_account,))

            return [
                str(r[0])
                for r in cur.fetchall()
            ]

    except Exception as e:

        print(
            f"[APPROVED VENDOR] "
            f"D365 fetch error "
            f"for {vendor_account}: {e}"
        )

        return []


# ============================================================
# RFQ HISTORY
# ============================================================

def get_rfq_history_sync(vendor_account: str):

    vendor_account = vendor_account.upper().strip()

    print("\n" + "=" * 80)
    print("RFQ HISTORY START")
    print("Vendor Account:", vendor_account)
    print("=" * 80)

    result = []

    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                RFQCASEID,
                RFQID,
                SUBMISSIONSTATUS,
                DRAFTLINECOUNT,
                SENDTOD365AT
            FROM {RFQ_REPLIES_TABLE}
            WITH (NOLOCK)
            WHERE VENDORACCOUNT = ?
        """, (vendor_account,))

        portal_rows = cur.fetchall()

    print("Portal Rows:", len(portal_rows))

    portal_map = {}

    for row in portal_rows:

        rfq_id = normalize(row[1])

        portal_map[rfq_id] = {
            "case_id": row[0],
            "submission_status": row[2],
            "draft_line_count": row[3],
            "submitted_on": row[4]
        }

    print("Portal Map Count:", len(portal_map))

    with get_d365_connection() as conn:

        cur = conn.cursor()

        print("Running D365 Query...")

        cur.execute("""
            SELECT
                L.RFQCASEID,
                T.RFQID,
                L.CREATEDDATETIME,
                L.EXPIRYDATETIME,
                L.DELIVERYDATE,
                PM.NAME AS PAYMENT_MODE,
                PT.DESCRIPTION AS PAYMENT_TERM,
                DM.TXT AS MODE_OF_DELIVERY,
                DT.TXT AS DELIVERY_TERM
            FROM PurchRFQCaseTable L
            WITH (NOLOCK)

            INNER JOIN PurchRFQTable T
            WITH (NOLOCK)
                ON T.RFQCASEID = L.RFQCASEID
               AND T.VENDACCOUNT = ?

            LEFT JOIN VENDPAYMMODETABLE PM
                ON L.PAYMMODE = PM.PAYMMODE

            LEFT JOIN PAYMTERM PT
                ON L.PAYMENT = PT.PAYMTERMID

            LEFT JOIN DLVMODE DM
                ON L.DLVMODE = DM.CODE

            LEFT JOIN DLVTERM DT
                ON L.DLVTERM = DT.CODE

            WHERE (
                CAST(DATEADD(MINUTE,330,L.EXPIRYDATETIME) AS DATE)
                    < CAST(GETUTCDATE() AS DATE)

                OR

                EXISTS (
                    SELECT 1
                    FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
                    INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
                        ON PL.RECID = RL.RFQLINERECID
                       AND PL.DATAAREAID = 'hi-q'
                    WHERE RL.RFQID = T.RFQID
                      AND RL.DATAAREAID = 'hi-q'
                      AND PL.STATUS >= 3
                )
            )

            ORDER BY L.EXPIRYDATETIME DESC
        """, (vendor_account,))

        rows = cur.fetchall()

        print("D365 Rows Found:", len(rows))

        for r in rows[:5]:
            print("ROW:", r)

        cols = [c[0] for c in cur.description]

    for row in rows:

        data = dict(zip(cols, row))

        rfq_id = normalize(data["RFQID"])

        portal = portal_map.get(rfq_id)

        status = "Expired"
        expired_status = "Not Opened"

        if portal:
            if (
                portal["submission_status"] == 1
                or int(portal["draft_line_count"] or 0) > 0
            ):
                expired_status = "Drafted"

        result.append({
            "rfq_no": data["RFQID"],
            "case_id": data["RFQCASEID"],
            "created_date": format_ist_date_only(
                data["CREATEDDATETIME"]
            ),
            "expiry_date": format_ist_date_only(
                data["EXPIRYDATETIME"]
            ),
            "delivery_date": format_ist_date_only(
                data["DELIVERYDATE"]
            ),
            "mode_of_delivery": data["MODE_OF_DELIVERY"] or "-",
            "delivery_term": data["DELIVERY_TERM"] or "-",
            "payment_term": data["PAYMENT_TERM"] or "-",
            "payment_mode": data["PAYMENT_MODE"] or "-",
            "expired_status": expired_status,
            "status": status
        })

    print("Final Result Count:", len(result))
    print("=" * 80)

    return {
        "status": "success",
        "count": len(result),
        "data": result
    }

# def get_rfq_history_sync(
#     vendor_account: str
# ):

#     result = []

#     # ========================================================
#     # STEP 1 — PORTAL REPLIES MAP
#     # Read vendor's saved/submitted replies from portal DB
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT
#                 RFQCASEID,
#                 RFQID,
#                 SUBMISSIONSTATUS,
#                 DRAFTLINECOUNT,
#                 SENDTOD365AT

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE VENDORACCOUNT = ?
#         """, (vendor_account,))

#         portal_rows = cur.fetchall()

#     portal_map = {}

#     for row in portal_rows:

#         rfq_id = normalize(row[1])

#         portal_map[rfq_id] = {
#             "case_id":           row[0],
#             "submission_status": row[2],
#             "draft_line_count":  row[3],
#             "submitted_on":      row[4]
#         }

#     # ========================================================
#     # STEP 2 — D365 RFQs FOR HISTORY
#     #
#     # Only show in history if:
#     #   A) Expired    — EXPIRYDATETIME DATE < today DATE
#     #   B) Completed  — D365 lines STATUS >= 3
#     #                   (accepted/rejected by HiQ)
#     #
#     # FIX: CAST to DATE to ignore time part
#     # FIX: Alias DM.TXT and DT.TXT separately
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         cur.execute("""
#             SELECT
#                 L.RFQCASEID,
#                 T.RFQID,
#                 L.CREATEDDATETIME,
#                 L.EXPIRYDATETIME,
#                 L.DELIVERYDATE,
#                 PM.NAME         AS PAYMENT_MODE,
#                 PT.DESCRIPTION  AS PAYMENT_TERM,
#                 DM.TXT          AS MODE_OF_DELIVERY,
#                 DT.TXT          AS DELIVERY_TERM

#             FROM PurchRFQCaseTable L
#             WITH (NOLOCK)

#             INNER JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON  T.RFQCASEID  = L.RFQCASEID
#                 AND T.VENDACCOUNT = ?

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             WHERE (

#                 -- ============================================
#                 -- A) EXPIRED
#                 -- Compare DATE only — ignore time
#                 -- So RFQ expiring today stays in active tab
#                 -- and moves to history only from tomorrow
#                 -- ============================================
#                 --CAST(L.EXPIRYDATETIME AS DATE)
#                   CAST(DATEADD(MINUTE, 330, L.EXPIRYDATETIME) As Date)  
#                     < CAST(GETUTCDATE() AS DATE)

#                 OR

#                 -- ============================================
#                 -- B) COMPLETED
#                 -- Vendor submitted + HiQ accepted/rejected
#                 -- lines (PurchRFQLine STATUS >= 3)
#                 -- ============================================
#                 EXISTS (
#                     SELECT 1
#                     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
#                     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#                         ON  PL.RECID      = RL.RFQLINERECID
#                         AND PL.DATAAREAID = 'hi-q'
#                     WHERE RL.RFQID      = T.RFQID
#                       AND RL.DATAAREAID = 'hi-q'
#                       AND PL.STATUS     >= 3
#                 )
#             )

#             ORDER BY L.EXPIRYDATETIME DESC
#         """, (vendor_account,))

#         rows = cur.fetchall()

#         cols = [c[0] for c in cur.description]

#     # ========================================================
#     # STEP 3 — BUILD RESULT WITH STATUS LOGIC
#     # ========================================================
#     for row in rows:

#         data   = dict(zip(cols, row))
#         rfq_id = normalize(data["RFQID"])
#         portal = portal_map.get(rfq_id)

#         # ====================================================
#         # CHECK — D365 lines accepted/rejected
#         # ====================================================
#         with get_d365_connection() as conn:

#             cur = conn.cursor()

#             cur.execute("""
#                 SELECT TOP 1 1

#                 FROM PURCHRFQREPLYLINE RL
#                 WITH (NOLOCK)

#                 INNER JOIN PURCHRFQLINE PL
#                 WITH (NOLOCK)

#                     ON  PL.RECID      = RL.RFQLINERECID
#                     AND PL.DATAAREAID = 'hi-q'

#                 WHERE RL.RFQID      = ?
#                   AND RL.DATAAREAID = 'hi-q'
#                   AND PL.STATUS     >= 3
#             """, (rfq_id,))

#             completed = cur.fetchone()

#         # ====================================================
#         # DETERMINE STATUS
#         #
#         # Priority:
#         #   1. Completed  — D365 has decided lines
#         #   2. Drafted    — vendor saved/submitted draft
#         #   3. Not Opened — vendor never touched it
#         # ====================================================
#         if completed:

#             # ================================================
#             # COMPLETED — HiQ accepted or rejected lines
#             # ================================================
#             status         = "Completed"
#             expired_status = "-"

#         elif portal and (
#             portal["submission_status"] == 1
#             or int(portal["draft_line_count"] or 0) > 0
#         ):

#             # ================================================
#             # DRAFTED — vendor saved draft or submitted
#             # but HiQ has not yet decided
#             # ================================================
#             status         = "Expired"
#             expired_status = "Drafted"

#         else:

#             # ================================================
#             # NOT OPENED — vendor never opened/filled it
#             # (not in portal replies table at all, or
#             #  no draft lines saved)
#             # ================================================
#             status         = "Expired"
#             expired_status = "Not Opened"

#         result.append({

#             "rfq_no":   data["RFQID"],
#             "case_id":  data["RFQCASEID"],

#             "created_date":  format_ist_date_only(
#                                  data["CREATEDDATETIME"]
#                              ),
#             "expiry_date":   format_ist_date_only(
#                                  data["EXPIRYDATETIME"]
#                              ),
#             "delivery_date": format_ist_date_only(
#                                  data["DELIVERYDATE"]
#                              ),

#             # FIX: use correct aliased column names
#             "mode_of_delivery": data["MODE_OF_DELIVERY"] or "-",
#             "delivery_term":    data["DELIVERY_TERM"]    or "-",
#             "payment_term":     data["PAYMENT_TERM"]     or "-",
#             "payment_mode":     data["PAYMENT_MODE"]     or "-",

#             "expired_status": expired_status,
#             "status":         status
#         })

#     return {
#         "status": "success",
#         "count":  len(result),
#         "data":   result
#     }


async def get_rfq_history(
    vendor_account: str
):
    return await run_in_threadpool(
        get_rfq_history_sync,
        vendor_account
    )


# ============================================================
# EXPIRED RFQ DETAIL
# ============================================================
def fetch_rfq_detail_sync(
    rfq_id: str,
    vendor_account: str
) -> Dict[str, Any]:

    # ========================================================
    # APPROVED ITEMS
    # ========================================================
    approved_items = _get_approved_items(vendor_account)

    # ========================================================
    # D365 HEADER + LINE DATA
    # ========================================================
    with get_d365_connection() as conn:

        cursor = conn.cursor()

        # ====================================================
        # HEADER
        # ====================================================
        cursor.execute("""
            SELECT TOP 1

                L.RFQCASEID,
                T.RFQID,
                L.NAME              AS DOCUMENT_TITLE,
                L.EXPIRYDATETIME    AS CLOSING_DATE,
                L.CREATEDDATETIME   AS ISSUE_DATE,
                L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
                PT.DESCRIPTION      AS PAYMENT_TERM,
                PM.NAME             AS METHOD_OF_PAYMENT,
                DM.TXT              AS MODE_OF_DELIVERY,
                DT.TXT              AS DELIVERY_TERM,
                T.HIQ_TERMSANDCONDITIONS,
                T.CURRENCYCODE

            FROM PurchRFQCaseTable L
            WITH (NOLOCK)

            INNER JOIN PurchRFQTable T
            WITH (NOLOCK)

                ON  T.RFQCASEID  = L.RFQCASEID
                AND T.VENDACCOUNT = ?

            LEFT JOIN PAYMTERM PT
                ON L.PAYMENT = PT.PAYMTERMID

            LEFT JOIN VENDPAYMMODETABLE PM
                ON L.PAYMMODE = PM.PAYMMODE

            LEFT JOIN DLVMODE DM
                ON L.DLVMODE = DM.CODE

            LEFT JOIN DLVTERM DT
                ON L.DLVTERM = DT.CODE

            WHERE T.RFQID = ?
        """, (vendor_account, rfq_id))

        row = cursor.fetchone()

        if not row:
            return {
                "success": False,
                "message": "RFQ not found"
            }

        cols   = [c[0] for c in cursor.description]
        header = dict(zip(cols, row))

        # ====================================================
        # LINE ITEMS
        # ====================================================
        lines = []

        if approved_items:

            placeholders = ",".join(
                ["?" for _ in approved_items]
            )

            cursor.execute(f"""
                SELECT

                    RL.LINENUM,
                    RL.ITEMID           AS MATERIAL_CODE,
                    IT.NAMEALIAS        AS MATERIAL_DESCRIPTION,
                    RL.QTYORDERED       AS QUANTITY,
                    RL.PURCHUNIT        AS UOM,
                    RL.HIQ_TARGETPRICE  AS TARGETPRICE,
                    RL.HIQ_COMMENTS     AS COMMENTS,
                    RL.CURRENCYCODE,
                    RL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
                    RPL.DELIVERYDATE    AS VENDORREPLY_DELIVERY_DATE

                FROM PurchRFQLine RL
                WITH (NOLOCK)

                LEFT JOIN PURCHRFQREPLYLINE RPL
                WITH (NOLOCK)

                    ON  RPL.RFQLINERECID = RL.RECID
                    AND RPL.DATAAREAID   = 'hi-q'

                LEFT JOIN INVENTTABLE IT
                WITH (NOLOCK)

                    ON IT.ITEMID = RL.ITEMID

                INNER JOIN PDSAPPROVEDVENDORLIST AVL
                WITH (NOLOCK)

                    ON  AVL.ITEMID            = RL.ITEMID
                    AND AVL.PDSAPPROVEDVENDOR = ?
                    AND AVL.VALIDFROM        <= GETUTCDATE()
                    AND AVL.VALIDTO          >= GETUTCDATE()

                WHERE RL.RFQID    = ?
                  AND RL.ITEMID IN ({placeholders})

                ORDER BY RL.LINENUM
            """, ([vendor_account, rfq_id] + approved_items))

            line_rows = cursor.fetchall()
            line_cols = [c[0] for c in cursor.description]
            lines     = [dict(zip(line_cols, r)) for r in line_rows]

    # ========================================================
    # PORTAL DRAFT DATA
    # ========================================================
    saved_price_map = {}
    saved_header    = {}

    with get_connection() as conn:

        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT TOP 1
                PAYLOADJSON

            FROM {RFQ_REPLIES_TABLE}
            WITH (NOLOCK)

            WHERE RFQID         = ?
              AND VENDORACCOUNT  = ?

            ORDER BY ID DESC
        """, (rfq_id, vendor_account))

        draft_row = cursor.fetchone()

    # ========================================================
    # DRAFT PARSE
    # ========================================================
    if draft_row and draft_row[0]:

        try:

            payload = json.loads(draft_row[0])

            saved_header = {
                "modeOfDelivery":      payload.get("modeOfDelivery",      ""),
                "DeliveryTerms":       payload.get("DeliveryTerms",        ""),
                "methodOfPayment":     payload.get("methodOfPayment",      ""),
                "termsOfPayment":      payload.get("termsOfPayment",       ""),
                "replyDeliveryDate":   payload.get("replyDeliveryDate",    ""),
                "replyDeliveryTerms":  payload.get("replyDeliveryTerms",   ""),
                "replyModeOfDelivery": payload.get("replyModeOfDelivery",  ""),
                "vendorComments":      payload.get("vendorComments",       "")
            }
            for item in payload.get("Item", []):

                item_number = normalize(
                    item.get("itemNumber")
                )

                line_number = int(
                    float(
                        item.get("lineNumber") or 0
                    )
                )

                key = (
                    item_number,
                    line_number
                )

                saved_price_map[key] = {

                    "unit_price":
                        float(
                            item.get(
                                "unitPrice"
                            ) or 0
                        ),

                    "net_amount":
                        float(
                            item.get(
                                "netAmount"
                            ) or 0
                        ),

                    "vendor_comments":
                        item.get(
                            "vendorComments",
                            ""
                        ),

                    "line_status":
                        item.get(
                            "lineStatus",
                            False
                        )
                }

            # for item in payload.get("Item", []):

            #     item_number = item.get("itemNumber")

            #     if item_number:

            #         saved_price_map[item_number] = {
            #             "unit_price":      float(item.get("unitPrice")   or 0),
            #             "net_amount":      float(item.get("netAmount")   or 0),
            #             "vendor_comments": item.get("vendorComments",   ""),
            #             "line_status":     item.get("lineStatus",       False)
            #         }

        except Exception as e:
            print(f"[DRAFT PARSE ERROR] {e}")

    # ========================================================
    # BUILD LINE ITEMS
    # ========================================================
    line_items = []

    for item in lines:

        material_code = normalize(
            item["MATERIAL_CODE"]
        )

        line_num = int(
            float(
                item["LINENUM"] or 0
            )
        )

        key = (
            material_code,
            line_num
        )

        saved = saved_price_map.get(
            key,
            {}
        )

        print(
            f"LINE={line_num} "
            f"ITEM={material_code} "
            f"STATUS={saved.get('line_status')}"
        )

        if saved.get("line_status") is True:
            continue

        line_items.append({

            "line_num":
                line_num,

            "item_name":
                item["MATERIAL_DESCRIPTION"],

            "item_id":
                item["MATERIAL_CODE"],

            "quantity":
                item["QUANTITY"],

            "uom":
                item["UOM"],

            "target_price":
                round(
                    float(
                        item["TARGETPRICE"] or 0
                    ),
                    2
                ),

            "currency":
                item["CURRENCYCODE"],

            "comments":
                item["COMMENTS"],

            "unit_price":
                saved.get(
                    "unit_price",
                    ""
                ),

            "net_amount":
                saved.get(
                    "net_amount",
                    ""
                ),

            "vendor_comments":
                saved.get(
                    "vendor_comments",
                    ""
                ),

            "rfq_delivery_date":
                format_ist_date_only(
                    item.get(
                        "LINE_DELIVERY_DATE"
                    )
                ),

            "vendor_delivery_date":
                format_ist_date_only(
                    item.get(
                        "VENDORREPLY_DELIVERY_DATE"
                    )
                ),

            "hiq_decision":
                "Expired"
        })

    print(
        "FINAL LINE COUNT:",
        len(line_items)
    )
    # line_items = []

    # for item in lines:

    #     material_code = item["MATERIAL_CODE"]
    #     saved         = saved_price_map.get(material_code, {})

    #     if saved.get("line_status", False):
    #         continue

    #     line_items.append({
    #         "line_num":           int(item["LINENUM"]),
    #         "item_name":          item["MATERIAL_DESCRIPTION"],
    #         "item_id":            material_code,
    #         "quantity":           item["QUANTITY"],
    #         "uom":                item["UOM"],
    #         "target_price":       round(float(item["TARGETPRICE"] or 0), 2),
    #         "currency":           item["CURRENCYCODE"],
    #         "comments":           item["COMMENTS"],
    #         "unit_price":         saved.get("unit_price",      ""),
    #         "net_amount":         saved.get("net_amount",      ""),
    #         "vendor_comments":    saved.get("vendor_comments", ""),
    #         "rfq_delivery_date":  format_ist_date_only(
    #                                   item.get("LINE_DELIVERY_DATE")
    #                               ),
    #         "vendor_delivery_date": format_ist_date_only(
    #                                     item.get("VENDORREPLY_DELIVERY_DATE")
    #                                 ),
    #         "hiq_decision": "Expired"
    #     })

    # ========================================================
    # FINAL RESPONSE
    # ========================================================
    return {

        "success":   True,
        "has_draft": bool(saved_price_map),

        "data": {

            "rfq_case_id":    header["RFQCASEID"],
            "rfq_id":         header["RFQID"],
            "document_title": header["DOCUMENT_TITLE"],

            "issue_date":   format_ist_date_only(header["ISSUE_DATE"]),
            "closing_date": format_ist_date_only(header["CLOSING_DATE"]),
            "time_remaining": calculate_days_left(header["CLOSING_DATE"]),
            "delivery_date": format_ist_date_only(
                                 header["EXPECTED_DELIVERY_DATE"]
                             ),

            "payment_term":  header["PAYMENT_TERM"]     or "-",
            "payment_mode":  header["METHOD_OF_PAYMENT"] or "-",
            "delivery_term": header["DELIVERY_TERM"]     or "-",
            "delivery_mode": header["MODE_OF_DELIVERY"]  or "-",

            "termsandconditions": header["HIQ_TERMSANDCONDITIONS"],
            "currency":           header["CURRENCYCODE"],

            "saved_mode_of_delivery":  saved_header.get("modeOfDelivery",      ""),
            "saved_delivery_terms":    saved_header.get("DeliveryTerms",        ""),
            "saved_method_of_payment": saved_header.get("methodOfPayment",      ""),
            "saved_terms_of_payment":  saved_header.get("termsOfPayment",       ""),
            "reply_delivery_date":     saved_header.get("replyDeliveryDate",    ""),
            "reply_delivery_mode":     saved_header.get("replyModeOfDelivery",  ""),
            "reply_delivery_term":     saved_header.get("replyDeliveryTerms",   ""),
            "saved_vendor_comments":   saved_header.get("vendorComments",       ""),

            "line_items": line_items
        }
    }


# ============================================================
# COMPLETED RFQ DETAIL
# ============================================================
def fetch_completed_rfq_detail_sync(
    rfq_id: str,
    vendor_account: str
) -> Dict[str, Any]:

    approved_items = _get_approved_items(vendor_account)

    # ========================================================
    # PORTAL DATA — get case id
    # ========================================================
    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(f"""
            SELECT TOP 1
                RFQCASEID

            FROM {RFQ_REPLIES_TABLE}
            WITH (NOLOCK)

            WHERE RFQID        = ?
              AND VENDORACCOUNT = ?
        """, (rfq_id, vendor_account))

        portal_row = cur.fetchone()

    if not portal_row:
        return {
            "success": False,
            "message": "RFQ not found"
        }

    rfq_case_id = portal_row[0]

    # ========================================================
    # D365 HEADER
    # ========================================================
    with get_d365_connection() as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT TOP 1

                RT.RFQID,
                RT.CURRENCYCODE,
                RT.DELIVERYDATE,
                RT.DLVMODE,
                RT.DLVTERM,
                RT.PAYMENT,
                RT.VENDREF,
                RT.TOTALSCORE,
                RT.RANK,
                RT.VALIDFROM,
                RT.VALIDTO,
                RT.VALIDITYDATESTART,
                RT.VALIDITYDATEEND,
                RT.REPLYPROGRESSSTATUS,
                RT.HIQ_COMMENTS,

                L.EXPIRYDATETIME,
                L.CREATEDDATETIME,
                L.DELIVERYDATE,
                L.NAME,

                PM.NAME         AS PAYMENT_MODE,
                PT.DESCRIPTION  AS PAYMENT_TERM,
                DM.TXT          AS MODE_OF_DELIVERY,
                DT.TXT          AS DELIVERY_TERM,

                T.HIQ_TERMSANDCONDITIONS

            FROM PURCHRFQREPLYTABLE RT
            WITH (NOLOCK)

            LEFT JOIN PurchRFQCaseTable L
            WITH (NOLOCK)

                ON L.RFQCASEID = ?

            LEFT JOIN PurchRFQTable T
            WITH (NOLOCK)

                ON  T.RFQCASEID  = L.RFQCASEID
                AND T.VENDACCOUNT = ?

            LEFT JOIN VENDPAYMMODETABLE PM
                ON L.PAYMMODE = PM.PAYMMODE

            LEFT JOIN PAYMTERM PT
                ON L.PAYMENT = PT.PAYMTERMID

            LEFT JOIN DLVMODE DM
                ON L.DLVMODE = DM.CODE

            LEFT JOIN DLVTERM DT
                ON L.DLVTERM = DT.CODE

            WHERE RT.RFQID      = ?
              AND RT.DATAAREAID = 'hi-q'
        """, (rfq_case_id, vendor_account, rfq_id))

        row = cur.fetchone()

        if not row:
            return {
                "success": False,
                "message": "RFQ not found"
            }

        cols   = [c[0] for c in cur.description]
        header = dict(zip(cols, row))

        # ====================================================
        # LINE ITEMS — only accepted/rejected (STATUS >= 3)
        # ====================================================
        lines = []

        if approved_items:

            placeholders = ",".join(
                ["?" for _ in approved_items]
            )

            cur.execute(f"""
                SELECT

                    RL.LINENUM,
                    RL.NAME,
                    RL.PURCHQTY,
                    RL.PURCHUNIT,
                    RL.PURCHPRICE,
                    RL.LINEAMOUNT,
                    RL.LINEDISC,
                    RL.LINEPERCENT,
                    RL.DELIVERYDATE,
                    RL.LEADTIME,
                    RL.HIQ_COMMENTS,
                    RL.VALIDFROM,
                    RL.VALIDTO,
                    RL.EXTERNALITEMID,
                    RL.MAXIMUMRETAILPRICE_IN,
                    PL.HIQ_TARGETPRICE,
                    PL.HIQ_COMMENTS,
                    PL.ITEMID,
                    PL.STATUS,
                    PL.CURRENCYCODE,
                    PL.PURCHID,
                    PL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
                    RL.DELIVERYDATE     AS VENDORREPLY_DELIVERY_DATE

                FROM PURCHRFQREPLYLINE RL
                WITH (NOLOCK)

                INNER JOIN PURCHRFQLINE PL
                WITH (NOLOCK)

                    ON  PL.RECID      = RL.RFQLINERECID
                    AND PL.DATAAREAID = 'hi-q'

                WHERE RL.RFQID      = ?
                  AND RL.DATAAREAID = 'hi-q'
                  AND PL.STATUS     >= 3
                  AND PL.ITEMID     IN ({placeholders})

                ORDER BY RL.LINENUM
            """, [rfq_id] + approved_items)

            line_rows = cur.fetchall()
            line_cols = [c[0] for c in cur.description]
            lines     = [dict(zip(line_cols, r)) for r in line_rows]

    return {

        "success": True,

        "data": {

            "rfq_id":         rfq_id,
            "rfq_case_id":    rfq_case_id,
            "document_title": header["NAME"],

            "issue_date":   format_ist_date_only(header["CREATEDDATETIME"]),
            "closing_date": format_ist_date_only(header["EXPIRYDATETIME"]),
            "delivery_date": format_ist_date_only(header["DELIVERYDATE"]),

            "currency":           header["CURRENCYCODE"],
            "termsandconditions": header["HIQ_TERMSANDCONDITIONS"],

            "line_items": [
                {
                    "line_num":   int(float(line["LINENUM"])),
                    "item_id":    line["ITEMID"],
                    "item_name":  line["NAME"],
                    "quantity":   float(line["PURCHQTY"]   or 0),
                    "uom":        line["PURCHUNIT"],
                    "unit_price": float(line["PURCHPRICE"] or 0),
                    "net_amount": float(line["LINEAMOUNT"] or 0),
                    "hiq_decision": (
                        "Accepted"
                        if line["STATUS"] == 4
                        else "Rejected"
                    )
                }
                for line in lines
            ]
        }
    }


# ============================================================
# RFQ HISTORY DETAIL
# ============================================================
def get_rfq_history_detail_sync(
    rfq_id: str,
    vendor_account: str,
    status: str
):

    status = (status or "").lower()

    if status == "completed":

        result = fetch_completed_rfq_detail_sync(
            rfq_id,
            vendor_account
        )

    elif status == "expired":

        result = fetch_rfq_detail_sync(
            rfq_id,
            vendor_account
        )

    else:

        return {
            "success": False,
            "message": "Invalid status. Use Completed or Expired."
        }

    if not result.get("success"):
        return result

    vendor_profile = fetch_vendor_profile_sync(vendor_account)

    return {

        "success": True,

        "type": status.capitalize(),

        "data": {

            **result.get("data"),

            "vendor_information": {
                "vendor_account": vendor_account,
                "vendor_name":    vendor_profile.get("name")    or "-",
                "email":          vendor_profile.get("email")   or "-",
                "phone":          vendor_profile.get("phone")   or "-",
                "address":        vendor_profile.get("address") or "-",
                "city":           vendor_profile.get("city")    or "-"
            }
        }
    }


async def get_rfq_history_detail(
    rfq_id: str,
    vendor_account: str,
    status: str
):
    return await run_in_threadpool(
        get_rfq_history_detail_sync,
        rfq_id,
        vendor_account,
        status
    )

# import json

# from typing import (
#     List,
#     Dict,
#     Any
# )

# from fastapi.concurrency import (
#     run_in_threadpool
# )

# from app.db.base import (
#     get_connection,
#     get_d365_connection
# )

# from app.core.config import settings

# from app.utils.date_utils import (
#     format_ist_date_only
# )

# from app.utils.remainingdate import (
#     calculate_days_left
# )


# SCHEMA = settings.DB_SCHEMA

# RFQ_REPLIES_TABLE = (
#     f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
# )


# # ============================================================
# # HELPERS
# # ============================================================
# def normalize(val):
#     return str(val or "").strip().upper()


# # ============================================================
# # VENDOR PROFILE
# # ============================================================
# def fetch_vendor_profile_sync(
#     vendor_account: str
# ):

#     profile = {

#         "email": None,

#         "phone": None,

#         "address": None,

#         "name": None,

#         "city": None
#     }

#     with get_d365_connection() as conn:

#         cursor = conn.cursor()

#         # ====================================================
#         # EMAIL + PHONE
#         # ====================================================
#         cursor.execute("""
#             SELECT
#                 TYPE,
#                 LOCATOR

#             FROM HIQ_vendorELECTRONICADDRESSVIEW
#             WITH (NOLOCK)

#             WHERE ACCOUNTNUM = ?
#               AND ISPRIMARY1 = 1
#         """, vendor_account)

#         for row in cursor.fetchall():

#             if row.TYPE == 2:
#                 profile["email"] = row.LOCATOR

#             elif row.TYPE == 1:
#                 profile["phone"] = row.LOCATOR

#         # ====================================================
#         # ADDRESS
#         # ====================================================
#         cursor.execute("""
#             SELECT TOP 1
#                 ADDRESS,
#                 NAME,
#                 CITY

#             FROM HIQ_vendorPostalADDRESSVIEW
#             WITH (NOLOCK)

#             WHERE ACCOUNTNUM = ?
#               AND ISPRIMARY = 1
#         """, vendor_account)

#         row = cursor.fetchone()

#         if row:

#             profile["address"] = row.ADDRESS

#             profile["name"] = row.NAME

#             profile["city"] = row.CITY

#     return profile


# async def fetch_vendor_profile(
#     vendor_account: str
# ):
#     return await run_in_threadpool(
#         fetch_vendor_profile_sync,
#         vendor_account
#     )


# # ============================================================
# # APPROVED ITEMS
# # ============================================================
# def _get_approved_items(
#     vendor_account: str
# ) -> List[str]:

#     try:

#         with get_d365_connection() as conn:

#             cur = conn.cursor()

#             cur.execute("""
#                 SELECT ITEMID

#                 FROM PDSAPPROVEDVENDORLIST
#                 WITH (NOLOCK)

#                 WHERE
#                     PDSAPPROVEDVENDOR = ?

#                   AND DATAAREAID = 'hi-q'

#                   AND VALIDFROM
#                         <= GETUTCDATE()

#                   AND VALIDTO
#                         >= GETUTCDATE()
#             """, (vendor_account,))

#             return [
#                 str(r[0]).strip()
#                 for r in cur.fetchall()
#             ]

#     except Exception as e:

#         print(
#             f"[APPROVED VENDOR] "
#             f"D365 fetch error "
#             f"for {vendor_account}: {e}"
#         )

#         return []


# # ============================================================
# # RFQ HISTORY
# # ============================================================
# def get_rfq_history_sync(
#     vendor_account: str
# ):

#     result = []

#     # ========================================================
#     # STEP 1 - PORTAL RFQS
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT
#                 RFQCASEID,
#                 RFQID,
#                 SUBMISSIONSTATUS,
#                 DRAFTLINECOUNT,
#                 SENDTOD365AT

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE VENDORACCOUNT = ?
#         """, (vendor_account,))

#         portal_rows = cur.fetchall()

#     portal_map = {}

#     for row in portal_rows:

#         rfq_id = normalize(row[1])

#         portal_map[rfq_id] = {

#             "case_id":
#                 row[0],

#             "submission_status":
#                 row[2],

#             "draft_line_count":
#                 row[3],

#             "submitted_on":
#                 row[4]
#         }

#     # ========================================================
#     # STEP 2 - D365 RFQS
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         cur.execute("""
#             SELECT
#                 L.RFQCASEID,
#                 T.RFQID,
#                 L.CREATEDDATETIME,
#                 L.EXPIRYDATETIME,
#                 L.DELIVERYDATE,
#                 PM.NAME,
#                 PT.DESCRIPTION,
#                 DM.TXT,
#                 DT.TXT

#             FROM PurchRFQCaseTable L
#             WITH (NOLOCK)

#             INNER JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON T.RFQCASEID = L.RFQCASEID
#                AND T.VENDACCOUNT = ?

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             ORDER BY L.EXPIRYDATETIME DESC
#         """, (vendor_account,))

#         rows = cur.fetchall()

#         cols = [
#             c[0]
#             for c in cur.description
#         ]

#     for row in rows:

#         data = dict(zip(cols, row))

#         rfq_id = normalize(data["RFQID"])

#         portal = portal_map.get(rfq_id)

#         # ====================================================
#         # STATUS
#         # ====================================================
#         status = "Expired"

#         expired_status = "Not Opened"

#         if portal:

#             if (
#                 portal["submission_status"] == 1
#             ):

#                 # ============================================
#                 # CHECK COMPLETED
#                 # ============================================
#                 with get_d365_connection() as conn:

#                     cur = conn.cursor()

#                     cur.execute("""
#                         SELECT TOP 1 1

#                         FROM PURCHRFQREPLYLINE RL
#                         WITH (NOLOCK)

#                         INNER JOIN PURCHRFQLINE PL
#                         WITH (NOLOCK)

#                             ON PL.RECID
#                                = RL.RFQLINERECID

#                            AND PL.DATAAREAID = 'hi-q'

#                         WHERE RL.RFQID = ?
#                           AND RL.DATAAREAID = 'hi-q'
#                           AND PL.STATUS >= 3
#                     """, (rfq_id,))

#                     completed = cur.fetchone()

#                 if completed:

#                     status = "Completed"

#                 else:

#                     expired_status = "Drafted"

#         result.append({

#             "rfq_no":
#                 data["RFQID"],

#             "case_id":
#                 data["RFQCASEID"],

#             "created_date":
#                 format_ist_date_only(
#                     data["CREATEDDATETIME"]
#                 ),

#             "expiry_date":
#                 format_ist_date_only(
#                     data["EXPIRYDATETIME"]
#                 ),

#             "delivery_date":
#                 format_ist_date_only(
#                     data["DELIVERYDATE"]
#                 ),

#             "mode_of_delivery":
#                 data["TXT"] or "-",

#             "delivery_term":
#                 data["TXT"] or "-",

#             "payment_term":
#                 data["DESCRIPTION"] or "-",

#             "payment_mode":
#                 data["NAME"] or "-",

#             "expired_status":
#                 expired_status,

#             "status":
#                 status
#         })

#     return {

#         "status": "success",

#         "count": len(result),

#         "data": result
#     }


# async def get_rfq_history(
#     vendor_account: str
# ):
#     return await run_in_threadpool(
#         get_rfq_history_sync,
#         vendor_account
#     )

# # ============================================================
# # EXPIRED RFQ DETAIL
# # ============================================================
# def fetch_rfq_detail_sync(
#     rfq_id: str,
#     vendor_account: str
# ) -> Dict[str, Any]:

#     # ========================================================
#     # APPROVED ITEMS
#     # ========================================================
#     approved_items = _get_approved_items(
#         vendor_account
#     )

#     # ========================================================
#     # D365 HEADER + LINE DATA
#     # ========================================================
#     with get_d365_connection() as conn:

#         cursor = conn.cursor()

#         # ====================================================
#         # HEADER
#         # ====================================================
#         cursor.execute("""
#             SELECT TOP 1

#                 L.RFQCASEID,

#                 T.RFQID,

#                 L.NAME AS DOCUMENT_TITLE,

#                 L.EXPIRYDATETIME AS CLOSING_DATE,

#                 L.CREATEDDATETIME AS ISSUE_DATE,

#                 L.DELIVERYDATE AS EXPECTED_DELIVERY_DATE,

#                 PT.DESCRIPTION AS PAYMENT_TERM,

#                 PM.NAME AS METHOD_OF_PAYMENT,

#                 DM.TXT AS MODE_OF_DELIVERY,

#                 DT.TXT AS DELIVERY_TERM,

#                 T.HIQ_TERMSANDCONDITIONS,

#                 T.CURRENCYCODE

#             FROM PurchRFQCaseTable L
#             WITH (NOLOCK)

#             INNER JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON T.RFQCASEID = L.RFQCASEID
#                AND T.VENDACCOUNT = ?

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             WHERE T.RFQID = ?
#         """, (
#             vendor_account,
#             rfq_id
#         ))

#         row = cursor.fetchone()

#         if not row:

#             return {
#                 "success": False,
#                 "message": "RFQ not found"
#             }

#         cols = [
#             c[0]
#             for c in cursor.description
#         ]

#         header = dict(zip(cols, row))

#         # ====================================================
#         # LINE ITEMS
#         # ====================================================
#         lines = []

#         if approved_items:

#             placeholders = ",".join(
#                 ["?" for _ in approved_items]
#             )

#             cursor.execute(f"""
#                 SELECT

#                     RL.LINENUM,

#                     RL.ITEMID AS MATERIAL_CODE,

#                     IT.NAMEALIAS AS MATERIAL_DESCRIPTION,

#                     RL.QTYORDERED AS QUANTITY,

#                     RL.PURCHUNIT AS UOM,

#                     RL.HIQ_TARGETPRICE AS TARGETPRICE,

#                     RL.HIQ_COMMENTS AS COMMENTS,

#                     RL.CURRENCYCODE,

#                     RL.DELIVERYDATE AS LINE_DELIVERY_DATE,

#                     RPL.DELIVERYDATE
#                         AS VENDORREPLY_DELIVERY_DATE

#                 FROM PurchRFQLine RL
#                 WITH (NOLOCK)

#                 LEFT JOIN PURCHRFQREPLYLINE RPL
#                 WITH (NOLOCK)

#                     ON RPL.RFQLINERECID = RL.RECID
#                    AND RPL.DATAAREAID = 'hi-q'

#                 LEFT JOIN INVENTTABLE IT
#                 WITH (NOLOCK)

#                     ON IT.ITEMID = RL.ITEMID

#                 INNER JOIN PDSAPPROVEDVENDORLIST AVL
#                 WITH (NOLOCK)

#                     ON AVL.ITEMID = RL.ITEMID
#                    AND AVL.PDSAPPROVEDVENDOR = ?
#                    AND AVL.VALIDFROM <= GETUTCDATE()
#                    AND AVL.VALIDTO >= GETUTCDATE()

#                 WHERE RL.RFQID = ?
#                   AND RL.ITEMID IN ({placeholders})

#                 ORDER BY RL.LINENUM
#             """, (
#                 [vendor_account, rfq_id]
#                 + approved_items
#             ))

#             line_rows = cursor.fetchall()

#             line_cols = [
#                 c[0]
#                 for c in cursor.description
#             ]

#             lines = [
#                 dict(zip(line_cols, r))
#                 for r in line_rows
#             ]

#     # ========================================================
#     # PORTAL DRAFT DATA
#     # ========================================================
#     saved_price_map = {}

#     saved_header = {}

#     with get_connection() as conn:

#         cursor = conn.cursor()

#         cursor.execute(f"""
#             SELECT TOP 1
#                 PAYLOADJSON

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE RFQID = ?
#               AND VENDORACCOUNT = ?

#             ORDER BY ID DESC
#         """, (
#             rfq_id,
#             vendor_account
#         ))

#         draft_row = cursor.fetchone()

#     # ========================================================
#     # DRAFT PARSE
#     # ========================================================
#     if draft_row and draft_row[0]:

#         try:

#             payload = json.loads(
#                 draft_row[0]
#             )

#             saved_header = {

#                 "modeOfDelivery":
#                     payload.get(
#                         "modeOfDelivery", ""
#                     ),

#                 "DeliveryTerms":
#                     payload.get(
#                         "DeliveryTerms", ""
#                     ),

#                 "methodOfPayment":
#                     payload.get(
#                         "methodOfPayment", ""
#                     ),

#                 "termsOfPayment":
#                     payload.get(
#                         "termsOfPayment", ""
#                     ),

#                 "replyDeliveryDate":
#                     payload.get(
#                         "replyDeliveryDate", ""
#                     ),

#                 "replyDeliveryTerms":
#                     payload.get(
#                         "replyDeliveryTerms", ""
#                     ),

#                 "replyModeOfDelivery":
#                     payload.get(
#                         "replyModeOfDelivery", ""
#                     ),

#                 "vendorComments":
#                     payload.get(
#                         "vendorComments", ""
#                     )
#             }

#             for item in payload.get("Item", []):

#                 item_number = item.get(
#                     "itemNumber"
#                 )

#                 if item_number:

#                     saved_price_map[
#                         item_number
#                     ] = {

#                         "unit_price":
#                             float(
#                                 item.get(
#                                     "unitPrice"
#                                 ) or 0
#                             ),

#                         "net_amount":
#                             float(
#                                 item.get(
#                                     "netAmount"
#                                 ) or 0
#                             ),

#                         "vendor_comments":
#                             item.get(
#                                 "vendorComments", ""
#                             ),

#                         "line_status":
#                             item.get(
#                                 "lineStatus",
#                                 False
#                             )
#                     }

#         except Exception as e:

#             print(
#                 f"[DRAFT PARSE ERROR] {e}"
#             )

#     # ========================================================
#     # BUILD LINE ITEMS
#     # ========================================================
#     line_items = []

#     for item in lines:

#         material_code = item[
#             "MATERIAL_CODE"
#         ]

#         saved = saved_price_map.get(
#             material_code,
#             {}
#         )

#         if saved.get(
#             "line_status",
#             False
#         ):
#             continue

#         line_items.append({

#             "line_num":
#                 int(item["LINENUM"]),

#             "item_name":
#                 item[
#                     "MATERIAL_DESCRIPTION"
#                 ],

#             "item_id":
#                 material_code,

#             "quantity":
#                 item["QUANTITY"],

#             "uom":
#                 item["UOM"],

#             "target_price":
#                 round(
#                     float(
#                         item[
#                             "TARGETPRICE"
#                         ] or 0
#                     ),
#                     2
#                 ),

#             "currency":
#                 item["CURRENCYCODE"],

#             "comments":
#                 item["COMMENTS"],

#             "unit_price":
#                 saved.get(
#                     "unit_price", ""
#                 ),

#             "net_amount":
#                 saved.get(
#                     "net_amount", ""
#                 ),

#             "vendor_comments":
#                 saved.get(
#                     "vendor_comments", ""
#                 ),

#             "rfq_delivery_date":
#                 format_ist_date_only(
#                     item.get(
#                         "LINE_DELIVERY_DATE"
#                     )
#                 ),

#             "vendor_delivery_date":
#                 format_ist_date_only(
#                     item.get(
#                         "VENDORREPLY_DELIVERY_DATE"
#                     )
#                 ),

#             "hiq_decision":
#                 "Expired"
#         })

#     # ========================================================
#     # FINAL RESPONSE
#     # ========================================================
#     return {

#         "success": True,

#         "has_draft":
#             bool(saved_price_map),

#         "data": {

#             "rfq_case_id":
#                 header["RFQCASEID"],

#             "rfq_id":
#                 header["RFQID"],

#             "document_title":
#                 header["DOCUMENT_TITLE"],

#             "issue_date":
#                 format_ist_date_only(
#                     header["ISSUE_DATE"]
#                 ),

#             "closing_date":
#                 format_ist_date_only(
#                     header["CLOSING_DATE"]
#                 ),

#             "time_remaining":
#                 calculate_days_left(
#                     header["CLOSING_DATE"]
#                 ),

#             "delivery_date":
#                 format_ist_date_only(
#                     header[
#                         "EXPECTED_DELIVERY_DATE"
#                     ]
#                 ),

#             "payment_term":
#                 header["PAYMENT_TERM"]
#                 or "-",

#             "payment_mode":
#                 header[
#                     "METHOD_OF_PAYMENT"
#                 ] or "-",

#             "delivery_term":
#                 header["DELIVERY_TERM"]
#                 or "-",

#             "delivery_mode":
#                 header[
#                     "MODE_OF_DELIVERY"
#                 ] or "-",

#             "termsandconditions":
#                 header[
#                     "HIQ_TERMSANDCONDITIONS"
#                 ],

#             "currency":
#                 header["CURRENCYCODE"],

#             "saved_mode_of_delivery":
#                 saved_header.get(
#                     "modeOfDelivery", ""
#                 ),

#             "saved_delivery_terms":
#                 saved_header.get(
#                     "DeliveryTerms", ""
#                 ),

#             "saved_method_of_payment":
#                 saved_header.get(
#                     "methodOfPayment", ""
#                 ),

#             "saved_terms_of_payment":
#                 saved_header.get(
#                     "termsOfPayment", ""
#                 ),

#             "reply_delivery_date":
#                 saved_header.get(
#                     "replyDeliveryDate", ""
#                 ),

#             "reply_delivery_mode":
#                 saved_header.get(
#                     "replyModeOfDelivery", ""
#                 ),

#             "reply_delivery_term":
#                 saved_header.get(
#                     "replyDeliveryTerms", ""
#                 ),

#             "saved_vendor_comments":
#                 saved_header.get(
#                     "vendorComments", ""
#                 ),

#             "line_items":
#                 line_items
#         }
#     }
# # ============================================================
# # COMPLETED RFQ DETAIL
# # ============================================================
# def fetch_completed_rfq_detail_sync(
#     rfq_id: str,
#     vendor_account: str
# ) -> Dict[str, Any]:

#     approved_items = _get_approved_items(
#         vendor_account
#     )

#     # ========================================================
#     # PORTAL DATA
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT TOP 1
#                 RFQCASEID

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE RFQID = ?
#               AND VENDORACCOUNT = ?
#         """, (
#             rfq_id,
#             vendor_account
#         ))

#         portal_row = cur.fetchone()

#     if not portal_row:

#         return {
#             "success": False,
#             "message": "RFQ not found"
#         }

#     rfq_case_id = portal_row[0]

#     # ========================================================
#     # D365 HEADER
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         cur.execute("""
#             SELECT TOP 1

#                 RT.RFQID,

#                 RT.CURRENCYCODE,

#                 RT.DELIVERYDATE,

#                 RT.DLVMODE,

#                 RT.DLVTERM,

#                 RT.PAYMENT,

#                 RT.VENDREF,

#                 RT.TOTALSCORE,

#                 RT.RANK,

#                 RT.VALIDFROM,

#                 RT.VALIDTO,

#                 RT.VALIDITYDATESTART,

#                 RT.VALIDITYDATEEND,

#                 RT.REPLYPROGRESSSTATUS,

#                 RT.HIQ_COMMENTS,

#                 L.EXPIRYDATETIME,

#                 L.CREATEDDATETIME,

#                 L.DELIVERYDATE,

#                 L.NAME,

#                 PM.NAME,

#                 PT.DESCRIPTION,

#                 DM.TXT,

#                 DT.TXT,

#                 T.HIQ_TERMSANDCONDITIONS

#             FROM PURCHRFQREPLYTABLE RT
#             WITH (NOLOCK)

#             LEFT JOIN PurchRFQCaseTable L
#             WITH (NOLOCK)

#                 ON L.RFQCASEID = ?

#             LEFT JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON T.RFQCASEID = L.RFQCASEID
#                AND T.VENDACCOUNT = ?

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             WHERE RT.RFQID = ?
#               AND RT.DATAAREAID = 'hi-q'
#         """, (
#             rfq_case_id,
#             vendor_account,
#             rfq_id
#         ))

#         row = cur.fetchone()

#         if not row:

#             return {
#                 "success": False,
#                 "message": "RFQ not found"
#             }

#         cols = [
#             c[0]
#             for c in cur.description
#         ]

#         header = dict(zip(cols, row))

#         lines = []

#         if approved_items:

#             placeholders = ",".join(
#                 ["?" for _ in approved_items]
#             )

#             cur.execute(f"""
#                 SELECT

#                     RL.LINENUM,

#                     RL.NAME,

#                     RL.PURCHQTY,

#                     RL.PURCHUNIT,

#                     RL.PURCHPRICE,

#                     RL.LINEAMOUNT,

#                     RL.LINEDISC,

#                     RL.LINEPERCENT,

#                     RL.DELIVERYDATE,

#                     RL.LEADTIME,

#                     RL.HIQ_COMMENTS,

#                     RL.VALIDFROM,

#                     RL.VALIDTO,

#                     RL.EXTERNALITEMID,

#                     RL.MAXIMUMRETAILPRICE_IN,

#                     PL.HIQ_TARGETPRICE,

#                     PL.HIQ_COMMENTS,

#                     PL.ITEMID,

#                     PL.STATUS,

#                     PL.CURRENCYCODE,

#                     PL.PURCHID,

#                     PL.DELIVERYDATE,

#                     RL.DELIVERYDATE

#                 FROM PURCHRFQREPLYLINE RL
#                 WITH (NOLOCK)

#                 INNER JOIN PURCHRFQLINE PL
#                 WITH (NOLOCK)

#                     ON PL.RECID
#                        = RL.RFQLINERECID

#                    AND PL.DATAAREAID = 'hi-q'

#                 WHERE RL.RFQID = ?
#                   AND RL.DATAAREAID = 'hi-q'
#                   AND PL.STATUS >= 3
#                   AND PL.ITEMID IN ({placeholders})

#                 ORDER BY RL.LINENUM
#             """, [rfq_id] + approved_items)

#             line_rows = cur.fetchall()

#             line_cols = [
#                 c[0]
#                 for c in cur.description
#             ]

#             lines = [
#                 dict(zip(line_cols, r))
#                 for r in line_rows
#             ]

#     return {

#         "success": True,

#         "data": {

#             "rfq_id":
#                 rfq_id,

#             "rfq_case_id":
#                 rfq_case_id,

#             "document_title":
#                 header["NAME"],

#             "issue_date":
#                 format_ist_date_only(
#                     header["CREATEDDATETIME"]
#                 ),

#             "closing_date":
#                 format_ist_date_only(
#                     header["EXPIRYDATETIME"]
#                 ),

#             "delivery_date":
#                 format_ist_date_only(
#                     header["DELIVERYDATE"]
#                 ),

#             "currency":
#                 header["CURRENCYCODE"],

#             "termsandconditions":
#                 header["HIQ_TERMSANDCONDITIONS"],

#             "line_items": [

#                 {

#                     "line_num":
#                         int(float(line["LINENUM"])),

#                     "item_id":
#                         line["ITEMID"],

#                     "item_name":
#                         line["NAME"],

#                     "quantity":
#                         float(line["PURCHQTY"] or 0),

#                     "uom":
#                         line["PURCHUNIT"],

#                     "unit_price":
#                         float(line["PURCHPRICE"] or 0),

#                     "net_amount":
#                         float(line["LINEAMOUNT"] or 0),

#                     "hiq_decision":
#                         "Accepted"
#                         if line["STATUS"] == 4
#                         else "Rejected"

#                 }

#                 for line in lines
#             ]
#         }
#     }


# # ============================================================
# # RFQ HISTORY DETAIL
# # ============================================================
# def get_rfq_history_detail_sync(
#     rfq_id: str,
#     vendor_account: str,
#     status: str
# ):

#     status = (
#         status or ""
#     ).lower()

#     if status == "completed":

#         result = fetch_completed_rfq_detail_sync(
#             rfq_id,
#             vendor_account
#         )

#     elif status == "expired":

#         result = fetch_rfq_detail_sync(
#             rfq_id,
#             vendor_account
#         )

#     else:

#         return {

#             "success": False,

#             "message":
#                 "Invalid status"
#         }

#     if not result.get("success"):
#         return result

#     vendor_profile = fetch_vendor_profile_sync(
#         vendor_account
#     )

#     return {

#         "success": True,

#         "type":
#             status.capitalize(),

#         "data": {

#             **result.get("data"),

#             "vendor_information": {

#                 "vendor_account":
#                     vendor_account,

#                 "vendor_name":
#                     vendor_profile.get("name") or "-",

#                 "email":
#                     vendor_profile.get("email") or "-",

#                 "phone":
#                     vendor_profile.get("phone") or "-",

#                 "address":
#                     vendor_profile.get("address") or "-",

#                 "city":
#                     vendor_profile.get("city") or "-"
#             }
#         }
#     }

# async def get_rfq_history_detail(
#     rfq_id: str,
#     vendor_account: str,
#     status: str
# ):
#     return await run_in_threadpool(
#         get_rfq_history_detail_sync,
#         rfq_id,
#         vendor_account,
#         status
#     )



# # from app.db.base import get_connection
# # from app.utils.date_utils import format_ist_date_only
# # import json
# # from app.utils.remainingdate import calculate_days_left
# # from typing import List, Dict, Any
# # from app.db.base import get_connection
# # from app.utils.date_utils import format_ist_date_only
# # from typing import List, Dict, Any
# # from fastapi.concurrency import run_in_threadpool
# # def get_rfq_history_sync(vendor_account: str):

# #     result = []

# #     with get_connection() as conn:
# #         cur = conn.cursor()

# #         # =========================
# #         # COMPLETED RFQs
# #         # Taken from fetch_completed_rfqs
# #         # =========================
# #         cur.execute("""
# #             SELECT DISTINCT
# #                 R.RFQ_CASE_ID,
# #                 R.RFQ_ID,
# #                 L.CREATEDDATETIME           AS ISSUE_DATE,
# #                 L.EXPIRYDATETIME            AS CLOSING_DATE,
# #                 L.DELIVERYDATE              AS EXPECTED_DELIVERY_DATE,
# #                 MAX(R.SEND_TO_D365_AT)      AS SUBMITTED_ON,
# #                 PM.NAME                     AS PAYMENT_MODE,
# #                 PT.DESCRIPTION              AS PAYMENT_TERM,
# #                 DM.TXT                      AS DELIVERY_MODE,
# #                 DT.TXT                      AS DELIVERY_TERM

# #             FROM HIQ_VENDORRFQREPLIES R WITH (NOLOCK)

# #             LEFT JOIN PurchRFQCaseTable L WITH (NOLOCK)
# #                 ON L.RFQCASEID = R.RFQ_CASE_ID

# #             LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN DLVMODE DM WITH (NOLOCK)
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT WITH (NOLOCK)
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE R.VENDOR_ACCOUNT    = ?
# #               AND R.SUBMISSION_STATUS = 1

# #               AND EXISTS (
# #                   SELECT 1
# #                   FROM PURCHRFQREPLYLINE RL2 WITH (NOLOCK)
# #                   INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
# #                       ON  PL.RECID      = RL2.RFQLINERECID
# #                       AND PL.DATAAREAID = 'hi-q'
# #                   WHERE RL2.RFQID      = R.RFQ_ID
# #                     AND RL2.DATAAREAID = 'hi-q'
# #                     AND PL.STATUS      >= 3
# #               )

# #             GROUP BY
# #                 R.RFQ_CASE_ID,
# #                 R.RFQ_ID,
# #                 L.CREATEDDATETIME,
# #                 L.EXPIRYDATETIME,
# #                 L.DELIVERYDATE,
# #                 PM.NAME,
# #                 PT.DESCRIPTION,
# #                 DM.TXT,
# #                 DT.TXT

# #             ORDER BY MAX(R.SEND_TO_D365_AT) DESC
# #         """, (vendor_account,))

# #         completed_rows = cur.fetchall()
# #         completed_cols = [c[0] for c in cur.description]

# #         for row in completed_rows:
# #             data = dict(zip(completed_cols, row))
# #             result.append({
# #                 "rfq_no":           data["RFQ_ID"],
# #                 "case_id":          data["RFQ_CASE_ID"],
# #                 "created_date":     format_ist_date_only(data["ISSUE_DATE"]),
# #                 "expiry_date":      format_ist_date_only(data["CLOSING_DATE"]),
# #                 "delivery_date":    format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
# #                 "mode_of_delivery": data["DELIVERY_MODE"]  or "-",
# #                 "delivery_term":    data["DELIVERY_TERM"]  or "-",
# #                 "payment_term":     data["PAYMENT_TERM"]   or "-",
# #                 "payment_mode":     data["PAYMENT_MODE"]   or "-",
# #                 "status":           "Completed"
# #             })

# #         # =========================
# #         # EXPIRED RFQs
# #         # Taken from fetch_vendor_expired_rfqs
# #         # =========================
# #         cur.execute("""
# #             SELECT
# #                 L.RFQCASEID,
# #                 T.RFQID,
# #                 L.CREATEDDATETIME   AS ISSUE_DATE,
# #                 L.EXPIRYDATETIME,
# #                 L.DELIVERYDATE,
# #                 PT.DESCRIPTION      AS PAYMENT_TERM,
# #                 PM.NAME             AS PAYMENT_MODE,
# #                 DM.TXT              AS DELIVERY_MODE,
# #                 DT.TXT              AS DELIVERY_TERM,
# #                 CASE
# #                     WHEN NOT EXISTS (
# #                         SELECT 1
# #                         FROM HIQ_VendorRFQReplies R
# #                         WHERE R.RFQ_CASE_ID    = L.RFQCASEID
# #                           AND R.VENDOR_ACCOUNT = T.VENDACCOUNT
# #                     ) THEN 'Not Opened'

# #                     WHEN EXISTS (
# #                         SELECT 1
# #                         FROM HIQ_VendorRFQReplies R
# #                         WHERE R.RFQ_CASE_ID        = L.RFQCASEID
# #                           AND R.VENDOR_ACCOUNT     = T.VENDACCOUNT
# #                           AND R.SUBMISSION_STATUS  = 1
# #                           AND R.DRAFTLINECOUNT     > 0
# #                     ) THEN 'Drafted'

# #                     ELSE 'Not Opened'
# #                 END AS EXPIRED_STATUS

# #             FROM PurchRFQCaseTable L WITH (NOLOCK)

# #             INNER JOIN PurchRFQTable T WITH (NOLOCK)
# #                 ON  T.RFQCASEID   = L.RFQCASEID
# #                 AND T.VENDACCOUNT = ?

# #             LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN DLVMODE DM WITH (NOLOCK)
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT WITH (NOLOCK)
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE L.EXPIRYDATETIME < GETUTCDATE()

# #             AND (
# #                 NOT EXISTS (
# #                     SELECT 1
# #                     FROM HIQ_VendorRFQReplies R WITH (NOLOCK)
# #                     WHERE R.RFQ_CASE_ID    = L.RFQCASEID
# #                       AND R.VENDOR_ACCOUNT = T.VENDACCOUNT
# #                 )
# #                 OR
# #                 EXISTS (
# #                     SELECT 1
# #                     FROM HIQ_VendorRFQReplies R
# #                     WHERE R.RFQ_CASE_ID        = L.RFQCASEID
# #                       AND R.VENDOR_ACCOUNT     = T.VENDACCOUNT
# #                       AND R.SUBMISSION_STATUS  = 1
# #                       AND R.DRAFTLINECOUNT     > 0
# #                 )
# #             )

# #             AND EXISTS (
# #                 SELECT 1
# #                 FROM PurchRFQCaseLine CL WITH (NOLOCK)
# #                 INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
# #                     ON  AVL.ITEMID            = CL.ITEMID
# #                     AND AVL.PDSAPPROVEDVENDOR = T.VENDACCOUNT
# #                     AND AVL.DATAAREAID        = L.DATAAREAID
# #                 WHERE CL.RFQCASEID = L.RFQCASEID
# #             )

# #             ORDER BY L.EXPIRYDATETIME DESC
# #         """, (vendor_account,))

# #         expired_rows = cur.fetchall()
# #         expired_cols = [c[0] for c in cur.description]

# #         for row in expired_rows:
# #             data = dict(zip(expired_cols, row))
# #             result.append({
# #                 "rfq_no":           data["RFQID"],
# #                 "case_id":          data["RFQCASEID"],
# #                 "created_date":     format_ist_date_only(data["ISSUE_DATE"]),
# #                 "expiry_date":      format_ist_date_only(data["EXPIRYDATETIME"]),
# #                 "delivery_date":    format_ist_date_only(data["DELIVERYDATE"]),
# #                 "mode_of_delivery": data["DELIVERY_MODE"]    or "-",
# #                 "delivery_term":    data["DELIVERY_TERM"]    or "-",
# #                 "payment_term":     data["PAYMENT_TERM"]     or "-",
# #                 "payment_mode":     data["PAYMENT_MODE"]     or "-",
# #                 "expired_status":   data["EXPIRED_STATUS"],   # Not Opened / Drafted
# #                 "status":           "Expired"
# #             })

# #     return {
# #         "status": "success",
# #         "count":  len(result),
# #         "data":   result
# #     }
# # async def get_rfq_history(vendor_account: str):
# #     return await run_in_threadpool(get_rfq_history_sync, vendor_account)    

# # def fetch_vendor_profile_sync(vendor_account: str):

# #     profile = {
# #         "email": None,
# #         "phone": None,
# #         "address": None,
# #         "name":None,
# #         "city":None
# #     }

# #     with get_connection() as conn:
# #         cursor = conn.cursor()

# #         # 🔹 Fetch Email + Phone
# #         electronic_query = """
# #             SELECT TYPE, LOCATOR
# #             FROM HIQ_vendorELECTRONICADDRESSVIEW WITH (NOLOCK)
# #             WHERE ACCOUNTNUM = ?
# #             AND ISPRIMARY1 = 1
# #         """

# #         cursor.execute(electronic_query, vendor_account)
# #         electronic_rows = cursor.fetchall()

# #         for row in electronic_rows:
# #             type = row.TYPE
# #             locator = row.LOCATOR
 
# #             if type == 2:
# #                 profile["email"] = locator
# #             elif type == 1:
# #                 profile["phone"] = locator

# #         # 🔹 Fetch Address
# #         address_query = """
# #             SELECT TOP 1 ADDRESS,NAME,CITY
# #             FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)
# #             WHERE ACCOUNTNUM = ?
# #             AND ISPRIMARY = 1
# #         """

# #         cursor.execute(address_query, vendor_account)
# #         address_row = cursor.fetchone()

# #         if address_row:
# #             profile["address"] = address_row.ADDRESS
# #             profile["name"]=address_row.NAME
# #             profile["city"] = address_row.CITY

# #         cursor.close()

# #     return profile 
# # async def fetch_vendor_profile(vendor_account: str):
# #     return await run_in_threadpool(fetch_vendor_profile_sync, vendor_account)

# # def _get_approved_items(vendor_account: str) -> List[str]:
# #     """
# #     Fetch approved item IDs for a vendor from D365.
# #     PDSAPPROVEDVENDORLIST lives in AxDb — must use get_d365_connection().
# #     """
# #     try:
# #         with get_connection() as conn:
# #             cur = conn.cursor()
# #             cur.execute("""
# #                 SELECT ITEMID
# #                 FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)
# #                 WHERE PDSAPPROVEDVENDOR = ?
# #                   AND DATAAREAID        = 'hi-q'
# #                   AND VALIDFROM        <= GETUTCDATE()
# #                   AND VALIDTO          >= GETUTCDATE()
# #             """, (vendor_account,))
# #             return [str(r[0]).strip() for r in cur.fetchall()]
# #     except Exception as e:
# #         print(f"[APPROVED VENDOR] D365 fetch error for {vendor_account}: {e}")
# #         return []


# # def fetch_completed_rfq_detail_sync(rfq_id: str, vendor_account: str) -> Dict[str, Any]:

# #     # Step 1 — get approved items from D365
# #     approved_items = _get_approved_items(vendor_account)

# #     with get_connection() as conn:
# #         cur = conn.cursor()

# #         # Step 2 — Header
# #         cur.execute("""
# #             SELECT TOP 1
# #                 RT.RFQID,
# #                 RT.CURRENCYCODE,
# #                 RT.DELIVERYDATE         AS REPLY_DELIVERY_DATE,
# #                 RT.DLVMODE              AS REPLY_DELIVERY_MODE,
# #                 RT.DLVTERM              AS REPLY_DELIVERY_TERM,
# #                 RT.PAYMENT              AS REPLY_PAYMENT_TERM,
# #                 RT.VENDREF,
# #                 RT.TOTALSCORE,
# #                 RT.RANK,
# #                 RT.VALIDFROM,
# #                 RT.VALIDTO,
# #                 RT.VALIDITYDATESTART,
# #                 RT.VALIDITYDATEEND,
# #                 RT.REPLYPROGRESSSTATUS,
# #                 RT.HIQ_COMMENTS         AS REMARKS,
# #                 R.RFQ_CASE_ID,
# #                 L.EXPIRYDATETIME        AS CLOSING_DATE,
# #                 L.CREATEDDATETIME       AS ISSUE_DATE,
# #                 L.DELIVERYDATE          AS EXPECTED_DELIVERY_DATE,
# #                 L.NAME                  AS DOCUMENT_TITLE,
# #                 PM.NAME                 AS PAYMENT_MODE,
# #                 PT.DESCRIPTION          AS PAYMENT_TERM,
# #                 DM.TXT                  AS DELIVERY_MODE,
# #                 DT.TXT                  AS DELIVERY_TERM,
# #                 T.HIQ_TERMSANDCONDITIONS
                
# #             FROM PURCHRFQREPLYTABLE RT WITH (NOLOCK)

# #             INNER JOIN HIQ_VENDORRFQREPLIES R WITH (NOLOCK)
# #                 ON  R.RFQ_ID         = RT.RFQID
# #                 AND R.VENDOR_ACCOUNT = ?

# #             LEFT JOIN PurchRFQCaseTable L WITH (NOLOCK)
# #                 ON  L.RFQCASEID  = R.RFQ_CASE_ID
# #                 AND L.DATAAREAID = 'hi-q'
# #             LEFT JOIN PurchRFQTable T WITH (NOLOCK)   -- ✅ ADD THIS JOIN
# #                 ON  T.RFQCASEID   = L.RFQCASEID
# #                 AND T.VENDACCOUNT = R.VENDOR_ACCOUNT
# #                 AND T.DATAAREAID  = 'hi-q'

# #             LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN DLVMODE DM WITH (NOLOCK)
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT WITH (NOLOCK)
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE RT.RFQID      = ?
# #               AND RT.DATAAREAID = 'hi-q'
# #         """, (vendor_account, rfq_id))

# #         row = cur.fetchone()
# #         if not row:
# #             return {"success": False, "message": "RFQ not found"}

# #         cols   = [c[0] for c in cur.description]
# #         header = dict(zip(cols, row))

# #         # Step 3 — Lines filtered by approved items
# #         lines = []
# #         if approved_items:
# #             placeholders = ",".join(["?" for _ in approved_items])
# #             cur.execute(f"""
# #                 SELECT
# #                     RL.LINENUM,
# #                     RL.NAME                     AS ITEM_NAME,
# #                     RL.PURCHQTY                 AS QUANTITY,
# #                     RL.PURCHUNIT                AS UOM,
# #                     RL.PURCHPRICE               AS UNIT_PRICE,
# #                     RL.LINEAMOUNT               AS NET_AMOUNT,
# #                     RL.LINEDISC                 AS LINE_DISC,
# #                     RL.LINEPERCENT              AS LINE_PERCENT,
# #                     RL.DELIVERYDATE             AS DELIVERY_DATE,
# #                     RL.LEADTIME,
# #                     RL.HIQ_COMMENTS             AS VENDOR_COMMENTS,
# #                     RL.VALIDFROM                AS LINE_VALID_FROM,
# #                     RL.VALIDTO                  AS LINE_VALID_TO,
# #                     RL.EXTERNALITEMID,
# #                     RL.MAXIMUMRETAILPRICE_IN    AS MRP,
# #                     PL.HIQ_TARGETPRICE          AS TARGETPRICE,
# #                     PL.HIQ_COMMENTS             AS COMMENTS,
# #                     PL.ITEMID,
# #                     PL.STATUS,
# #                     PL.CURRENCYCODE,
# #                     PL.PURCHID,
# #                     PL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
# #                     RL.DELIVERYDATE AS VENDORREPLY_DELIVERY_DATE,
# #                     CASE PL.STATUS
# #                         WHEN 3 THEN 'Rejected'
# #                         WHEN 4 THEN 'Accepted'
# #                         WHEN 5 THEN 'Caceled'
# #                         WHEN 6 THEN 'Declined'
# #                         ELSE        'Under Review'
# #                     END                         AS HIQ_DECISION

# #                 FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)

# #                 INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
# #                     ON  PL.RECID      = RL.RFQLINERECID
# #                     AND PL.DATAAREAID = 'hi-q'

# #                 WHERE RL.RFQID      = ?
# #                   AND RL.DATAAREAID = 'hi-q'
# #                   AND PL.STATUS     >= 3
# #                   AND PL.ITEMID     IN ({placeholders})

# #                 ORDER BY RL.LINENUM
# #             """, [rfq_id] + approved_items)

# #             line_rows = cur.fetchall()
# #             line_cols = [c[0] for c in cur.description]
# #             lines     = [dict(zip(line_cols, r)) for r in line_rows]

# #     return {
# #         "success": True,
# #         "data": {
# #             "rfq_id":                   rfq_id,
# #             "rfq_case_id":              header["RFQ_CASE_ID"],
# #             "delivery_date":            format_ist_date_only(header["EXPECTED_DELIVERY_DATE"]),
# #             "payment_term":             header["PAYMENT_TERM"]          or "-",
# #             "delivery_term":            header["DELIVERY_TERM"]         or "-",
# #             "delivery_mode":            header["DELIVERY_MODE"]         or "-",
# #             "payment_mode":             header["PAYMENT_MODE"]          or "-",
# #             "issue_date":               format_ist_date_only(header["ISSUE_DATE"]),
# #             "closing_date":             format_ist_date_only(header["CLOSING_DATE"]),
# #             "document_title":           header["DOCUMENT_TITLE"],
# #             "currency":                 header["CURRENCYCODE"]          or "INR",
# #             "reply_delivery_date":      format_ist_date_only(header["REPLY_DELIVERY_DATE"]),
# #             "reply_delivery_mode":      header["REPLY_DELIVERY_MODE"]   or "-",
# #             "reply_delivery_term":      header["REPLY_DELIVERY_TERM"]   or "-",
# #             "reply_payment_term":       header["REPLY_PAYMENT_TERM"]    or "-",
# #             "vendor_ref":               header["VENDREF"]               or "-",
# #             "valid_from":               format_ist_date_only(header["VALIDFROM"]),
# #             "valid_to":                 format_ist_date_only(header["VALIDTO"]),
# #             "validity_date_start":      format_ist_date_only(header["VALIDITYDATESTART"]),
# #             "validity_date_end":        format_ist_date_only(header["VALIDITYDATEEND"]),
# #             "total_score":              header["TOTALSCORE"]            or 0,
# #             "rank":                     header["RANK"]                  or 0,
# #             "reply_progress_status":    header["REPLYPROGRESSSTATUS"]   or 0,
# #             "remarks":                  header["REMARKS"]               or " ",
# #             "termsandconditions":     header["HIQ_TERMSANDCONDITIONS"],
# #             "line_items": [
# #                 {
# #                     "line_num":         int(float(line["LINENUM"])),
# #                     "item_id":          line["ITEMID"]               or "-",
# #                     "item_name":        line["ITEM_NAME"]            or "-",
# #                     "external_item_id": line["EXTERNALITEMID"]       or "-",
# #                     "quantity":         float(line["QUANTITY"]       or 0),
# #                     "uom":              line["UOM"]                  or "-",
# #                     "unit_price":       float(line["UNIT_PRICE"]     or 0),
# #                     "net_amount":       float(line["NET_AMOUNT"]     or 0),
# #                     "line_disc":        float(line["LINE_DISC"]      or 0),
# #                     "line_percent":     float(line["LINE_PERCENT"]   or 0),
# #                     "mrp":              float(line["MRP"]            or 0),
# #                     "delivery_date":    format_ist_date_only(line["DELIVERY_DATE"]),
# #                     "lead_time":        line["LEADTIME"]             or 0,
# #                     "vendor_comments":  line["VENDOR_COMMENTS"]      or " ",
# #                     "currency":         line["CURRENCYCODE"]       or "INR",
# #                     "line_valid_from":  format_ist_date_only(line["LINE_VALID_FROM"]),
# #                     "line_valid_to":    format_ist_date_only(line["LINE_VALID_TO"]),
# #                     "hiq_decision":     line["HIQ_DECISION"],
# #                     "target_price":     round(float(line['TARGETPRICE'] or 0), 2),
# #                     # "target_price":     f"{round(float(line['TARGETPRICE'] or 0), 2)} {line['CURRENCYCODE']}",
# #                     "comments":         line["COMMENTS"] or " ",
# #                     "purchid":line["PURCHID"],
# #                     "rfq_delivery_date": format_ist_date_only(line.get("LINE_DELIVERY_DATE")),
# #                     "vendor_delivery_date": format_ist_date_only(line.get("VENDORREPLY_DELIVERY_DATE"))
# #                 }
# #                 for line in lines
# #             ]
# #         }
# #     }

# # def fetch_rfq_detail_sync(rfq_id: str, vendor_account: str):

# #     header_query = """
# #         SELECT
# #             L.RFQCASEID,
# #             T.RFQID,
# #             L.NAME              AS DOCUMENT_TITLE,
# #             L.EXPIRYDATETIME    AS CLOSING_DATE,
# #             L.CREATEDDATETIME   AS ISSUE_DATE,
# #             L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
# #             PT.DESCRIPTION      AS PAYMENT_TERM,
# #             PM.NAME             AS METHOD_OF_PAYMENT,
# #             DM.TXT              AS MODE_OF_DELIVERY,
# #             DT.TXT              AS DELIVERY_TERM,
# #             T.HIQ_TERMSANDCONDITIONS,
# #             T.CURRENCYCODE

# #         FROM PurchRFQCaseTable L WITH (NOLOCK)

# #         INNER JOIN PurchRFQTable T WITH (NOLOCK)
# #             ON  T.RFQCASEID   = L.RFQCASEID
# #             AND T.VENDACCOUNT = ?

# #         LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# #             ON L.PAYMENT = PT.PAYMTERMID

# #         LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# #             ON L.PAYMMODE = PM.PAYMMODE

# #         LEFT JOIN DLVMODE DM WITH (NOLOCK)
# #             ON L.DLVMODE = DM.CODE

# #         LEFT JOIN DLVTERM DT WITH (NOLOCK)
# #             ON L.DLVTERM = DT.CODE

# #         WHERE T.RFQID = ?
# #     """

# #     lines_query = """
# #         SELECT
# #             RL.LINENUM,
# #             RL.ITEMID           AS MATERIAL_CODE,
# #             IT.NAMEALIAS        AS MATERIAL_DESCRIPTION,
# #             RL.QTYORDERED       AS QUANTITY,
# #             RL.PURCHUNIT        AS UOM,
# #             RL.HIQ_TARGETPRICE  AS TARGETPRICE,
# #             RL.HIQ_COMMENTS     AS COMMENTS,
# #             RL.CURRENCYCODE,
# #             RL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
# #             RPL.DELIVERYDATE    AS VENDORREPLY_DELIVERY_DATE   

# #         FROM PurchRFQLine RL WITH (NOLOCK)
# #         LEFT JOIN PURCHRFQREPLYLINE RPL WITH (NOLOCK)
# #             ON  RPL.RFQLINERECID = RL.RECID
# #             AND RPL.DATAAREAID   = 'hi-q'

# #         LEFT JOIN INVENTTABLE IT WITH (NOLOCK)
# #             ON IT.ITEMID = RL.ITEMID

# #         INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
# #             ON  AVL.ITEMID            = RL.ITEMID
# #             AND AVL.PDSAPPROVEDVENDOR = ?
# #             AND AVL.VALIDFROM        <= GETUTCDATE()
# #             AND AVL.VALIDTO          >= GETUTCDATE()

# #         WHERE RL.RFQID = ?
# #         ORDER BY RL.LINENUM
# #     """

# #     draft_query = """
# #         SELECT TOP 1 PAYLOAD_JSON
# #         FROM HIQ_VENDORRFQREPLIES WITH (NOLOCK)
# #         WHERE RFQ_ID         = ?
# #           AND VENDOR_ACCOUNT = ?
# #           --AND SUBMISSION_STATUS = 0
# #         ORDER BY ID DESC
# #     """

# #     with get_connection() as conn:
# #         cursor = conn.cursor()

# #         cursor.execute(header_query, (vendor_account, rfq_id))
# #         row = cursor.fetchone()
# #         if not row:
# #             return {"success": False, "message": "RFQ not found"}

# #         cols   = [c[0] for c in cursor.description]
# #         header = dict(zip(cols, row))

# #         cursor.execute(lines_query, (vendor_account, rfq_id))
# #         line_rows = cursor.fetchall()
# #         line_cols = [c[0] for c in cursor.description]
# #         lines     = [dict(zip(line_cols, r)) for r in line_rows]

# #         cursor.execute(draft_query, (rfq_id, vendor_account))
# #         draft_row = cursor.fetchone()

# #     saved_price_map = {}
# #     saved_header    = {}

# #     if draft_row and draft_row[0]:
# #         try:
# #             payload = json.loads(draft_row[0])

# #             saved_header = {
# #                 "modeOfDelivery":      payload.get("modeOfDelivery", ""),
# #                 "DeliveryTerms":       payload.get("DeliveryTerms", ""),
# #                 "methodOfPayment":     payload.get("methodOfPayment", ""),
# #                 "termsOfPayment":      payload.get("termsOfPayment", ""),
# #                 "replyDeliveryDate":   payload.get("replyDeliveryDate", ""),
# #                 "replyDeliveryTerms":  payload.get("replyDeliveryTerms", ""),   
# #                 "replyModeOfDelivery": payload.get("replyModeOfDelivery", ""),  
# #                 "vendorComments":      payload.get("vendorComments", ""),
# #             }

# #             for item in payload.get("Item", []):
# #                 item_number = item.get("itemNumber")
# #                 if item_number:
# #                     val = item.get("unitPrice")
# #                     saved_price_map[item_number] = {
# #                         "unit_price":float(val) if val not in [None, ""] else "",
# #                         "net_amount": float(item.get("netAmount")) if item.get("netAmount") not in [None, ""] else "", 
# #                         "vendor_comments": item.get("vendorComments", ""),
# #                         "line_status": item.get("lineStatus", False),
    
# #                     }
# #         except Exception:
# #             pass

# #     line_items = []
# #     for item in lines:
# #         material_code = item["MATERIAL_CODE"]
# #         saved         = saved_price_map.get(material_code, {})
# #         if saved.get("line_status", False):
# #             continue
# #         line_items.append({
# #             "line_num":                int(item["LINENUM"]),
# #             "item_name": item["MATERIAL_DESCRIPTION"],
# #             "item_id":        material_code,
# #             "quantity":             item["QUANTITY"],
# #             "uom":                  item["UOM"],
# #             "target_price":     round(float(item['TARGETPRICE'] or 0), 2),
# #             # "target_price":    f"{round(float(item['TARGETPRICE'] or 0), 2)} {item['CURRENCYCODE']}",
# #             "currency":         item["CURRENCYCODE"],
# #             "comments":             item["COMMENTS"],
# #             "unit_price": saved.get("unit_price", ""),
# #             "net_amount": saved.get("net_amount", ""),
# #             # "unit_price":           saved.get("unit_price", None),
# #             "vendor_comments":              saved.get("vendor_comments", ""),
# #             "rfq_delivery_date": format_ist_date_only(item.get("LINE_DELIVERY_DATE")),
# #             "vendor_delivery_date": format_ist_date_only(item.get("VENDORREPLY_DELIVERY_DATE")),
# #             "hiq_decision":"Expired",
# #         })

# #     return {
# #         "success": True,
# #         "has_draft": bool(saved_price_map),
# #         "data": {
# #             "rfq_case_id":            header["RFQCASEID"],
# #             "rfq_id":                 header["RFQID"],
# #             "document_title":         header["DOCUMENT_TITLE"],
# #             "issue_date":             format_ist_date_only(header["ISSUE_DATE"]),
# #             "closing_date":           format_ist_date_only(header["CLOSING_DATE"]),
# #             "time_remaining":         calculate_days_left(header["CLOSING_DATE"]),
# #             "delivery_date": format_ist_date_only(header["EXPECTED_DELIVERY_DATE"]),
# #             "payment_term":           header["PAYMENT_TERM"]      or "-",
# #             "payment_mode":      header["METHOD_OF_PAYMENT"] or "-",
# #             "delivery_term":          header["DELIVERY_TERM"]      or "-",
# #             "delivery_mode":       header["MODE_OF_DELIVERY"]   or "-",
# #             "termsandconditions":     header["HIQ_TERMSANDCONDITIONS"],
# #             "currency":header["CURRENCYCODE"],

# #             # ── Saved draft fields (all 8) ─────────────────────
# #             "saved_mode_of_delivery":       saved_header.get("modeOfDelivery", ""),
# #             "saved_delivery_terms":         saved_header.get("DeliveryTerms", ""),
# #             "saved_method_of_payment":      saved_header.get("methodOfPayment", ""),
# #             "saved_terms_of_payment":       saved_header.get("termsOfPayment", ""),
# #             "reply_delivery_date":    saved_header.get("replyDeliveryDate", ""),
# #             "reply_delivery_mode": saved_header.get("replyModeOfDelivery", ""),   
# #             "reply_delivery_term": saved_header.get("replyDeliveryTerms", ""),
# #             "saved_vendor_comments":        saved_header.get("vendorComments", ""),

# #             "line_items": line_items
# #         }
# #     }

# # def get_rfq_history_detail_sync(rfq_id: str, vendor_account: str, status: str):

# #     # =========================
# #     # VALIDATION
# #     # =========================
# #     if not rfq_id or not vendor_account:
# #         return {
# #             "success": False,
# #             "message": "rfq_id and vendor_account required"
# #         }

# #     # =========================
# #     # ROUTING BASED ON STATUS
# #     # =========================
# #     status = (status or "").lower()

# #     if status == "completed":
# #         result = fetch_completed_rfq_detail_sync(rfq_id, vendor_account)

# #     elif status == "expired":
# #         result = fetch_rfq_detail_sync(rfq_id, vendor_account)

# #     else:
# #         return {
# #             "success": False,
# #             "message": "Invalid status. Use Completed or Expired"
# #         }

# #     # =========================
# #     # SAFETY CHECK
# #     # =========================
# #     if not result.get("success"):
# #         return result

# #     # =========================
# #     # FETCH VENDOR PROFILE ✅
# #     # =========================
# #     vendor_profile = fetch_vendor_profile_sync(vendor_account)

# #     # =========================
# #     # FINAL RESPONSE ✅
# #     # =========================
# #     return {
# #         "success": True,
# #         "type": status.capitalize(),
# #         "data": {
# #             **result.get("data"),

# #             # 🔹 ADD VENDOR INFO HERE
# #             "vendor_information": {
# #                 "vendor_account": vendor_account,
# #                 "vendor_name": vendor_profile.get("name") or "-",
# #                 "email": vendor_profile.get("email") or "-",
# #                 "phone": vendor_profile.get("phone") or "-",
# #                 "address": vendor_profile.get("address") or "-",
# #                 "city":vendor_profile.get("city") or "-"  
# #             }
# #         }
# #     }
# # async def get_rfq_history_detail(rfq_id: str, vendor_account: str, status: str):
# #     return await run_in_threadpool(get_rfq_history_detail_sync, rfq_id,vendor_account,status)
#  import json

# from typing import (
#     List,
#     Dict,
#     Any
# )

# from fastapi.concurrency import (
#     run_in_threadpool
# )

# from app.db.base import (
#     get_connection,
#     get_d365_connection
# )

# from app.core.config import settings

# from app.utils.date_utils import (
#     format_ist_date_only
# )

# from app.utils.remainingdate import (
#     calculate_days_left
# )


# SCHEMA = settings.DB_SCHEMA

# RFQ_REPLIES_TABLE = (
#     f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
# )


# # ============================================================
# # HELPERS
# # ============================================================
# def normalize(val):
#     return str(val or "").strip().upper()


# # ============================================================
# # VENDOR PROFILE
# # ============================================================
# def fetch_vendor_profile_sync(
#     vendor_account: str
# ):

#     profile = {
#         "email":   None,
#         "phone":   None,
#         "address": None,
#         "name":    None,
#         "city":    None
#     }

#     with get_d365_connection() as conn:

#         cursor = conn.cursor()

#         # ====================================================
#         # EMAIL + PHONE
#         # ====================================================
#         cursor.execute("""
#             SELECT
#                 TYPE,
#                 LOCATOR

#             FROM HIQ_vendorELECTRONICADDRESSVIEW
#             WITH (NOLOCK)

#             WHERE ACCOUNTNUM = ?
#               AND ISPRIMARY1 = 1
#         """, vendor_account)

#         for row in cursor.fetchall():

#             if row.TYPE == 2:
#                 profile["email"] = row.LOCATOR

#             elif row.TYPE == 1:
#                 profile["phone"] = row.LOCATOR

#         # ====================================================
#         # ADDRESS
#         # ====================================================
#         cursor.execute("""
#             SELECT TOP 1
#                 ADDRESS,
#                 NAME,
#                 CITY

#             FROM HIQ_vendorPostalADDRESSVIEW
#             WITH (NOLOCK)

#             WHERE ACCOUNTNUM = ?
#               AND ISPRIMARY = 1
#         """, vendor_account)

#         row = cursor.fetchone()

#         if row:
#             profile["address"] = row.ADDRESS
#             profile["name"]    = row.NAME
#             profile["city"]    = row.CITY

#     return profile


# async def fetch_vendor_profile(
#     vendor_account: str
# ):
#     return await run_in_threadpool(
#         fetch_vendor_profile_sync,
#         vendor_account
#     )


# # ============================================================
# # APPROVED ITEMS
# # ============================================================
# def _get_approved_items(
#     vendor_account: str
# ) -> List[str]:

#     try:

#         with get_d365_connection() as conn:

#             cur = conn.cursor()

#             cur.execute("""
#                 SELECT ITEMID

#                 FROM PDSAPPROVEDVENDORLIST
#                 WITH (NOLOCK)

#                 WHERE PDSAPPROVEDVENDOR = ?
#                   AND DATAAREAID        = 'hi-q'
#                   AND VALIDFROM        <= GETUTCDATE()
#                   AND VALIDTO          >= GETUTCDATE()
#             """, (vendor_account,))

#             return [
#                 str(r[0])
#                 for r in cur.fetchall()
#             ]

#     except Exception as e:

#         print(
#             f"[APPROVED VENDOR] "
#             f"D365 fetch error "
#             f"for {vendor_account}: {e}"
#         )

#         return []


# # ============================================================
# # RFQ HISTORY
# # ============================================================
# def get_rfq_history_sync(
#     vendor_account: str
# ):

#     result = []

#     # ========================================================
#     # STEP 1 — PORTAL REPLIES MAP
#     # Read vendor's saved/submitted replies from portal DB
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT
#                 RFQCASEID,
#                 RFQID,
#                 SUBMISSIONSTATUS,
#                 DRAFTLINECOUNT,
#                 SENDTOD365AT

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE VENDORACCOUNT = ?
#         """, (vendor_account,))

#         portal_rows = cur.fetchall()

#     portal_map = {}

#     for row in portal_rows:

#         rfq_id = normalize(row[1])

#         portal_map[rfq_id] = {
#             "case_id":           row[0],
#             "submission_status": row[2],
#             "draft_line_count":  row[3],
#             "submitted_on":      row[4]
#         }

#     # ========================================================
#     # STEP 2 — D365 RFQs FOR HISTORY
#     #
#     # Only show in history if:
#     #   A) Expired    — EXPIRYDATETIME DATE < today DATE
#     #   B) Completed  — D365 lines STATUS >= 3
#     #                   (accepted/rejected by HiQ)
#     #
#     # FIX: CAST to DATE to ignore time part
#     # FIX: Alias DM.TXT and DT.TXT separately
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         cur.execute("""
#             SELECT
#                 L.RFQCASEID,
#                 T.RFQID,
#                 L.CREATEDDATETIME,
#                 L.EXPIRYDATETIME,
#                 L.DELIVERYDATE,
#                 PM.NAME         AS PAYMENT_MODE,
#                 PT.DESCRIPTION  AS PAYMENT_TERM,
#                 DM.TXT          AS MODE_OF_DELIVERY,
#                 DT.TXT          AS DELIVERY_TERM

#             FROM PurchRFQCaseTable L
#             WITH (NOLOCK)

#             INNER JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON  T.RFQCASEID  = L.RFQCASEID
#                 AND T.VENDACCOUNT = ?

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             WHERE (

#                 -- ============================================
#                 -- A) EXPIRED
#                 -- Compare DATE only — ignore time
#                 -- So RFQ expiring today stays in active tab
#                 -- and moves to history only from tomorrow
#                 -- ============================================
#                 --CAST(L.EXPIRYDATETIME AS DATE)
#                   CAST(DATEADD(MINUTE, 330, L.EXPIRYDATETIME) As Date)  
#                     < CAST(GETUTCDATE() AS DATE)

#                 OR

#                 -- ============================================
#                 -- B) COMPLETED
#                 -- Vendor submitted + HiQ accepted/rejected
#                 -- lines (PurchRFQLine STATUS >= 3)
#                 -- ============================================
#                 EXISTS (
#                     SELECT 1
#                     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
#                     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
#                         ON  PL.RECID      = RL.RFQLINERECID
#                         AND PL.DATAAREAID = 'hi-q'
#                     WHERE RL.RFQID      = T.RFQID
#                       AND RL.DATAAREAID = 'hi-q'
#                       AND PL.STATUS     >= 3
#                 )
#             )

#             ORDER BY L.EXPIRYDATETIME DESC
#         """, (vendor_account,))

#         rows = cur.fetchall()

#         cols = [c[0] for c in cur.description]

#     # ========================================================
#     # STEP 3 — BUILD RESULT WITH STATUS LOGIC
#     # ========================================================
#     for row in rows:

#         data   = dict(zip(cols, row))
#         rfq_id = normalize(data["RFQID"])
#         portal = portal_map.get(rfq_id)

#         # ====================================================
#         # CHECK — D365 lines accepted/rejected
#         # ====================================================
#         with get_d365_connection() as conn:

#             cur = conn.cursor()

#             cur.execute("""
#                 SELECT TOP 1 1

#                 FROM PURCHRFQREPLYLINE RL
#                 WITH (NOLOCK)

#                 INNER JOIN PURCHRFQLINE PL
#                 WITH (NOLOCK)

#                     ON  PL.RECID      = RL.RFQLINERECID
#                     AND PL.DATAAREAID = 'hi-q'

#                 WHERE RL.RFQID      = ?
#                   AND RL.DATAAREAID = 'hi-q'
#                   AND PL.STATUS     >= 3
#             """, (rfq_id,))

#             completed = cur.fetchone()

#         # ====================================================
#         # DETERMINE STATUS
#         #
#         # Priority:
#         #   1. Completed  — D365 has decided lines
#         #   2. Drafted    — vendor saved/submitted draft
#         #   3. Not Opened — vendor never touched it
#         # ====================================================
#         if completed:

#             # ================================================
#             # COMPLETED — HiQ accepted or rejected lines
#             # ================================================
#             status         = "Completed"
#             expired_status = "-"

#         elif portal and (
#             portal["submission_status"] == 1
#             or int(portal["draft_line_count"] or 0) > 0
#         ):

#             # ================================================
#             # DRAFTED — vendor saved draft or submitted
#             # but HiQ has not yet decided
#             # ================================================
#             status         = "Expired"
#             expired_status = "Drafted"

#         else:

#             # ================================================
#             # NOT OPENED — vendor never opened/filled it
#             # (not in portal replies table at all, or
#             #  no draft lines saved)
#             # ================================================
#             status         = "Expired"
#             expired_status = "Not Opened"

#         result.append({

#             "rfq_no":   data["RFQID"],
#             "case_id":  data["RFQCASEID"],

#             "created_date":  format_ist_date_only(
#                                  data["CREATEDDATETIME"]
#                              ),
#             "expiry_date":   format_ist_date_only(
#                                  data["EXPIRYDATETIME"]
#                              ),
#             "delivery_date": format_ist_date_only(
#                                  data["DELIVERYDATE"]
#                              ),

#             # FIX: use correct aliased column names
#             "mode_of_delivery": data["MODE_OF_DELIVERY"] or "-",
#             "delivery_term":    data["DELIVERY_TERM"]    or "-",
#             "payment_term":     data["PAYMENT_TERM"]     or "-",
#             "payment_mode":     data["PAYMENT_MODE"]     or "-",

#             "expired_status": expired_status,
#             "status":         status
#         })

#     return {
#         "status": "success",
#         "count":  len(result),
#         "data":   result
#     }


# async def get_rfq_history(
#     vendor_account: str
# ):
#     return await run_in_threadpool(
#         get_rfq_history_sync,
#         vendor_account
#     )


# # ============================================================
# # EXPIRED RFQ DETAIL
# # ============================================================
# def fetch_rfq_detail_sync(
#     rfq_id: str,
#     vendor_account: str
# ) -> Dict[str, Any]:

#     # ========================================================
#     # APPROVED ITEMS
#     # ========================================================
#     approved_items = _get_approved_items(vendor_account)

#     # ========================================================
#     # D365 HEADER + LINE DATA
#     # ========================================================
#     with get_d365_connection() as conn:

#         cursor = conn.cursor()

#         # ====================================================
#         # HEADER
#         # ====================================================
#         cursor.execute("""
#             SELECT TOP 1

#                 L.RFQCASEID,
#                 T.RFQID,
#                 L.NAME              AS DOCUMENT_TITLE,
#                 L.EXPIRYDATETIME    AS CLOSING_DATE,
#                 L.CREATEDDATETIME   AS ISSUE_DATE,
#                 L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
#                 PT.DESCRIPTION      AS PAYMENT_TERM,
#                 PM.NAME             AS METHOD_OF_PAYMENT,
#                 DM.TXT              AS MODE_OF_DELIVERY,
#                 DT.TXT              AS DELIVERY_TERM,
#                 T.HIQ_TERMSANDCONDITIONS,
#                 T.CURRENCYCODE

#             FROM PurchRFQCaseTable L
#             WITH (NOLOCK)

#             INNER JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON  T.RFQCASEID  = L.RFQCASEID
#                 AND T.VENDACCOUNT = ?

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             WHERE T.RFQID = ?
#         """, (vendor_account, rfq_id))

#         row = cursor.fetchone()

#         if not row:
#             return {
#                 "success": False,
#                 "message": "RFQ not found"
#             }

#         cols   = [c[0] for c in cursor.description]
#         header = dict(zip(cols, row))

#         # ====================================================
#         # LINE ITEMS
#         # ====================================================
#         lines = []

#         if approved_items:

#             placeholders = ",".join(
#                 ["?" for _ in approved_items]
#             )

#             cursor.execute(f"""
#                 SELECT

#                     RL.LINENUM,
#                     RL.ITEMID           AS MATERIAL_CODE,
#                     IT.NAMEALIAS        AS MATERIAL_DESCRIPTION,
#                     RL.QTYORDERED       AS QUANTITY,
#                     RL.PURCHUNIT        AS UOM,
#                     RL.HIQ_TARGETPRICE  AS TARGETPRICE,
#                     RL.HIQ_COMMENTS     AS COMMENTS,
#                     RL.CURRENCYCODE,
#                     RL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
#                     RPL.DELIVERYDATE    AS VENDORREPLY_DELIVERY_DATE

#                 FROM PurchRFQLine RL
#                 WITH (NOLOCK)

#                 LEFT JOIN PURCHRFQREPLYLINE RPL
#                 WITH (NOLOCK)

#                     ON  RPL.RFQLINERECID = RL.RECID
#                     AND RPL.DATAAREAID   = 'hi-q'

#                 LEFT JOIN INVENTTABLE IT
#                 WITH (NOLOCK)

#                     ON IT.ITEMID = RL.ITEMID

#                 INNER JOIN PDSAPPROVEDVENDORLIST AVL
#                 WITH (NOLOCK)

#                     ON  AVL.ITEMID            = RL.ITEMID
#                     AND AVL.PDSAPPROVEDVENDOR = ?
#                     AND AVL.VALIDFROM        <= GETUTCDATE()
#                     AND AVL.VALIDTO          >= GETUTCDATE()

#                 WHERE RL.RFQID    = ?
#                   AND RL.ITEMID IN ({placeholders})

#                 ORDER BY RL.LINENUM
#             """, ([vendor_account, rfq_id] + approved_items))

#             line_rows = cursor.fetchall()
#             line_cols = [c[0] for c in cursor.description]
#             lines     = [dict(zip(line_cols, r)) for r in line_rows]

#     # ========================================================
#     # PORTAL DRAFT DATA
#     # ========================================================
#     saved_price_map = {}
#     saved_header    = {}

#     with get_connection() as conn:

#         cursor = conn.cursor()

#         cursor.execute(f"""
#             SELECT TOP 1
#                 PAYLOADJSON

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE RFQID         = ?
#               AND VENDORACCOUNT  = ?

#             ORDER BY ID DESC
#         """, (rfq_id, vendor_account))

#         draft_row = cursor.fetchone()

#     # ========================================================
#     # DRAFT PARSE
#     # ========================================================
#     if draft_row and draft_row[0]:

#         try:

#             payload = json.loads(draft_row[0])

#             saved_header = {
#                 "modeOfDelivery":      payload.get("modeOfDelivery",      ""),
#                 "DeliveryTerms":       payload.get("DeliveryTerms",        ""),
#                 "methodOfPayment":     payload.get("methodOfPayment",      ""),
#                 "termsOfPayment":      payload.get("termsOfPayment",       ""),
#                 "replyDeliveryDate":   payload.get("replyDeliveryDate",    ""),
#                 "replyDeliveryTerms":  payload.get("replyDeliveryTerms",   ""),
#                 "replyModeOfDelivery": payload.get("replyModeOfDelivery",  ""),
#                 "vendorComments":      payload.get("vendorComments",       "")
#             }

#             for item in payload.get("Item", []):

#                 item_number = item.get("itemNumber")

#                 if item_number:

#                     saved_price_map[item_number] = {
#                         "unit_price":      float(item.get("unitPrice")   or 0),
#                         "net_amount":      float(item.get("netAmount")   or 0),
#                         "vendor_comments": item.get("vendorComments",   ""),
#                         "line_status":     item.get("lineStatus",       False)
#                     }

#         except Exception as e:
#             print(f"[DRAFT PARSE ERROR] {e}")

#     # ========================================================
#     # BUILD LINE ITEMS
#     # ========================================================
#     line_items = []

#     for item in lines:

#         material_code = item["MATERIAL_CODE"]
#         saved         = saved_price_map.get(material_code, {})

#         if saved.get("line_status", False):
#             continue

#         line_items.append({
#             "line_num":           int(item["LINENUM"]),
#             "item_name":          item["MATERIAL_DESCRIPTION"],
#             "item_id":            material_code,
#             "quantity":           item["QUANTITY"],
#             "uom":                item["UOM"],
#             "target_price":       round(float(item["TARGETPRICE"] or 0), 2),
#             "currency":           item["CURRENCYCODE"],
#             "comments":           item["COMMENTS"],
#             "unit_price":         saved.get("unit_price",      ""),
#             "net_amount":         saved.get("net_amount",      ""),
#             "vendor_comments":    saved.get("vendor_comments", ""),
#             "rfq_delivery_date":  format_ist_date_only(
#                                       item.get("LINE_DELIVERY_DATE")
#                                   ),
#             "vendor_delivery_date": format_ist_date_only(
#                                         item.get("VENDORREPLY_DELIVERY_DATE")
#                                     ),
#             "hiq_decision": "Expired"
#         })

#     # ========================================================
#     # FINAL RESPONSE
#     # ========================================================
#     return {

#         "success":   True,
#         "has_draft": bool(saved_price_map),

#         "data": {

#             "rfq_case_id":    header["RFQCASEID"],
#             "rfq_id":         header["RFQID"],
#             "document_title": header["DOCUMENT_TITLE"],

#             "issue_date":   format_ist_date_only(header["ISSUE_DATE"]),
#             "closing_date": format_ist_date_only(header["CLOSING_DATE"]),
#             "time_remaining": calculate_days_left(header["CLOSING_DATE"]),
#             "delivery_date": format_ist_date_only(
#                                  header["EXPECTED_DELIVERY_DATE"]
#                              ),

#             "payment_term":  header["PAYMENT_TERM"]     or "-",
#             "payment_mode":  header["METHOD_OF_PAYMENT"] or "-",
#             "delivery_term": header["DELIVERY_TERM"]     or "-",
#             "delivery_mode": header["MODE_OF_DELIVERY"]  or "-",

#             "termsandconditions": header["HIQ_TERMSANDCONDITIONS"],
#             "currency":           header["CURRENCYCODE"],

#             "saved_mode_of_delivery":  saved_header.get("modeOfDelivery",      ""),
#             "saved_delivery_terms":    saved_header.get("DeliveryTerms",        ""),
#             "saved_method_of_payment": saved_header.get("methodOfPayment",      ""),
#             "saved_terms_of_payment":  saved_header.get("termsOfPayment",       ""),
#             "reply_delivery_date":     saved_header.get("replyDeliveryDate",    ""),
#             "reply_delivery_mode":     saved_header.get("replyModeOfDelivery",  ""),
#             "reply_delivery_term":     saved_header.get("replyDeliveryTerms",   ""),
#             "saved_vendor_comments":   saved_header.get("vendorComments",       ""),

#             "line_items": line_items
#         }
#     }


# # ============================================================
# # COMPLETED RFQ DETAIL
# # ============================================================
# def fetch_completed_rfq_detail_sync(
#     rfq_id: str,
#     vendor_account: str
# ) -> Dict[str, Any]:

#     approved_items = _get_approved_items(vendor_account)

#     # ========================================================
#     # PORTAL DATA — get case id
#     # ========================================================
#     with get_connection() as conn:

#         cur = conn.cursor()

#         cur.execute(f"""
#             SELECT TOP 1
#                 RFQCASEID

#             FROM {RFQ_REPLIES_TABLE}
#             WITH (NOLOCK)

#             WHERE RFQID        = ?
#               AND VENDORACCOUNT = ?
#         """, (rfq_id, vendor_account))

#         portal_row = cur.fetchone()

#     if not portal_row:
#         return {
#             "success": False,
#             "message": "RFQ not found"
#         }

#     rfq_case_id = portal_row[0]

#     # ========================================================
#     # D365 HEADER
#     # ========================================================
#     with get_d365_connection() as conn:

#         cur = conn.cursor()

#         cur.execute("""
#             SELECT TOP 1

#                 RT.RFQID,
#                 RT.CURRENCYCODE,
#                 RT.DELIVERYDATE,
#                 RT.DLVMODE,
#                 RT.DLVTERM,
#                 RT.PAYMENT,
#                 RT.VENDREF,
#                 RT.TOTALSCORE,
#                 RT.RANK,
#                 RT.VALIDFROM,
#                 RT.VALIDTO,
#                 RT.VALIDITYDATESTART,
#                 RT.VALIDITYDATEEND,
#                 RT.REPLYPROGRESSSTATUS,
#                 RT.HIQ_COMMENTS,

#                 L.EXPIRYDATETIME,
#                 L.CREATEDDATETIME,
#                 L.DELIVERYDATE,
#                 L.NAME,

#                 PM.NAME         AS PAYMENT_MODE,
#                 PT.DESCRIPTION  AS PAYMENT_TERM,
#                 DM.TXT          AS MODE_OF_DELIVERY,
#                 DT.TXT          AS DELIVERY_TERM,

#                 T.HIQ_TERMSANDCONDITIONS

#             FROM PURCHRFQREPLYTABLE RT
#             WITH (NOLOCK)

#             LEFT JOIN PurchRFQCaseTable L
#             WITH (NOLOCK)

#                 ON L.RFQCASEID = ?

#             LEFT JOIN PurchRFQTable T
#             WITH (NOLOCK)

#                 ON  T.RFQCASEID  = L.RFQCASEID
#                 AND T.VENDACCOUNT = ?

#             LEFT JOIN VENDPAYMMODETABLE PM
#                 ON L.PAYMMODE = PM.PAYMMODE

#             LEFT JOIN PAYMTERM PT
#                 ON L.PAYMENT = PT.PAYMTERMID

#             LEFT JOIN DLVMODE DM
#                 ON L.DLVMODE = DM.CODE

#             LEFT JOIN DLVTERM DT
#                 ON L.DLVTERM = DT.CODE

#             WHERE RT.RFQID      = ?
#               AND RT.DATAAREAID = 'hi-q'
#         """, (rfq_case_id, vendor_account, rfq_id))

#         row = cur.fetchone()

#         if not row:
#             return {
#                 "success": False,
#                 "message": "RFQ not found"
#             }

#         cols   = [c[0] for c in cur.description]
#         header = dict(zip(cols, row))

#         # ====================================================
#         # LINE ITEMS — only accepted/rejected (STATUS >= 3)
#         # ====================================================
#         lines = []

#         if approved_items:

#             placeholders = ",".join(
#                 ["?" for _ in approved_items]
#             )

#             cur.execute(f"""
#                 SELECT

#                     RL.LINENUM,
#                     RL.NAME,
#                     RL.PURCHQTY,
#                     RL.PURCHUNIT,
#                     RL.PURCHPRICE,
#                     RL.LINEAMOUNT,
#                     RL.LINEDISC,
#                     RL.LINEPERCENT,
#                     RL.DELIVERYDATE,
#                     RL.LEADTIME,
#                     RL.HIQ_COMMENTS,
#                     RL.VALIDFROM,
#                     RL.VALIDTO,
#                     RL.EXTERNALITEMID,
#                     RL.MAXIMUMRETAILPRICE_IN,
#                     PL.HIQ_TARGETPRICE,
#                     PL.HIQ_COMMENTS,
#                     PL.ITEMID,
#                     PL.STATUS,
#                     PL.CURRENCYCODE,
#                     PL.PURCHID,
#                     PL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
#                     RL.DELIVERYDATE     AS VENDORREPLY_DELIVERY_DATE

#                 FROM PURCHRFQREPLYLINE RL
#                 WITH (NOLOCK)

#                 INNER JOIN PURCHRFQLINE PL
#                 WITH (NOLOCK)

#                     ON  PL.RECID      = RL.RFQLINERECID
#                     AND PL.DATAAREAID = 'hi-q'

#                 WHERE RL.RFQID      = ?
#                   AND RL.DATAAREAID = 'hi-q'
#                   AND PL.STATUS     >= 3
#                   AND PL.ITEMID     IN ({placeholders})

#                 ORDER BY RL.LINENUM
#             """, [rfq_id] + approved_items)

#             line_rows = cur.fetchall()
#             line_cols = [c[0] for c in cur.description]
#             lines     = [dict(zip(line_cols, r)) for r in line_rows]

#     return {

#         "success": True,

#         "data": {

#             "rfq_id":         rfq_id,
#             "rfq_case_id":    rfq_case_id,
#             "document_title": header["NAME"],

#             "issue_date":   format_ist_date_only(header["CREATEDDATETIME"]),
#             "closing_date": format_ist_date_only(header["EXPIRYDATETIME"]),
#             "delivery_date": format_ist_date_only(header["DELIVERYDATE"]),

#             "currency":           header["CURRENCYCODE"],
#             "termsandconditions": header["HIQ_TERMSANDCONDITIONS"],

#             "line_items": [
#                 {
#                     "line_num":   int(float(line["LINENUM"])),
#                     "item_id":    line["ITEMID"],
#                     "item_name":  line["NAME"],
#                     "quantity":   float(line["PURCHQTY"]   or 0),
#                     "uom":        line["PURCHUNIT"],
#                     "unit_price": float(line["PURCHPRICE"] or 0),
#                     "net_amount": float(line["LINEAMOUNT"] or 0),
#                     "hiq_decision": (
#                         "Accepted"
#                         if line["STATUS"] == 4
#                         else "Rejected"
#                     )
#                 }
#                 for line in lines
#             ]
#         }
#     }


# # ============================================================
# # RFQ HISTORY DETAIL
# # ============================================================
# def get_rfq_history_detail_sync(
#     rfq_id: str,
#     vendor_account: str,
#     status: str
# ):

#     status = (status or "").lower()

#     if status == "completed":

#         result = fetch_completed_rfq_detail_sync(
#             rfq_id,
#             vendor_account
#         )

#     elif status == "expired":

#         result = fetch_rfq_detail_sync(
#             rfq_id,
#             vendor_account
#         )

#     else:

#         return {
#             "success": False,
#             "message": "Invalid status. Use Completed or Expired."
#         }

#     if not result.get("success"):
#         return result

#     vendor_profile = fetch_vendor_profile_sync(vendor_account)

#     return {

#         "success": True,

#         "type": status.capitalize(),

#         "data": {

#             **result.get("data"),

#             "vendor_information": {
#                 "vendor_account": vendor_account,
#                 "vendor_name":    vendor_profile.get("name")    or "-",
#                 "email":          vendor_profile.get("email")   or "-",
#                 "phone":          vendor_profile.get("phone")   or "-",
#                 "address":        vendor_profile.get("address") or "-",
#                 "city":           vendor_profile.get("city")    or "-"
#             }
#         }
#     }


# async def get_rfq_history_detail(
#     rfq_id: str,
#     vendor_account: str,
#     status: str
# ):
#     return await run_in_threadpool(
#         get_rfq_history_detail_sync,
#         rfq_id,
#         vendor_account,
#         status
#     )

# # import json

# # from typing import (
# #     List,
# #     Dict,
# #     Any
# # )

# # from fastapi.concurrency import (
# #     run_in_threadpool
# # )

# # from app.db.base import (
# #     get_connection,
# #     get_d365_connection
# # )

# # from app.core.config import settings

# # from app.utils.date_utils import (
# #     format_ist_date_only
# # )

# # from app.utils.remainingdate import (
# #     calculate_days_left
# # )


# # SCHEMA = settings.DB_SCHEMA

# # RFQ_REPLIES_TABLE = (
# #     f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
# # )


# # # ============================================================
# # # HELPERS
# # # ============================================================
# # def normalize(val):
# #     return str(val or "").strip().upper()


# # # ============================================================
# # # VENDOR PROFILE
# # # ============================================================
# # def fetch_vendor_profile_sync(
# #     vendor_account: str
# # ):

# #     profile = {

# #         "email": None,

# #         "phone": None,

# #         "address": None,

# #         "name": None,

# #         "city": None
# #     }

# #     with get_d365_connection() as conn:

# #         cursor = conn.cursor()

# #         # ====================================================
# #         # EMAIL + PHONE
# #         # ====================================================
# #         cursor.execute("""
# #             SELECT
# #                 TYPE,
# #                 LOCATOR

# #             FROM HIQ_vendorELECTRONICADDRESSVIEW
# #             WITH (NOLOCK)

# #             WHERE ACCOUNTNUM = ?
# #               AND ISPRIMARY1 = 1
# #         """, vendor_account)

# #         for row in cursor.fetchall():

# #             if row.TYPE == 2:
# #                 profile["email"] = row.LOCATOR

# #             elif row.TYPE == 1:
# #                 profile["phone"] = row.LOCATOR

# #         # ====================================================
# #         # ADDRESS
# #         # ====================================================
# #         cursor.execute("""
# #             SELECT TOP 1
# #                 ADDRESS,
# #                 NAME,
# #                 CITY

# #             FROM HIQ_vendorPostalADDRESSVIEW
# #             WITH (NOLOCK)

# #             WHERE ACCOUNTNUM = ?
# #               AND ISPRIMARY = 1
# #         """, vendor_account)

# #         row = cursor.fetchone()

# #         if row:

# #             profile["address"] = row.ADDRESS

# #             profile["name"] = row.NAME

# #             profile["city"] = row.CITY

# #     return profile


# # async def fetch_vendor_profile(
# #     vendor_account: str
# # ):
# #     return await run_in_threadpool(
# #         fetch_vendor_profile_sync,
# #         vendor_account
# #     )


# # # ============================================================
# # # APPROVED ITEMS
# # # ============================================================
# # def _get_approved_items(
# #     vendor_account: str
# # ) -> List[str]:

# #     try:

# #         with get_d365_connection() as conn:

# #             cur = conn.cursor()

# #             cur.execute("""
# #                 SELECT ITEMID

# #                 FROM PDSAPPROVEDVENDORLIST
# #                 WITH (NOLOCK)

# #                 WHERE
# #                     PDSAPPROVEDVENDOR = ?

# #                   AND DATAAREAID = 'hi-q'

# #                   AND VALIDFROM
# #                         <= GETUTCDATE()

# #                   AND VALIDTO
# #                         >= GETUTCDATE()
# #             """, (vendor_account,))

# #             return [
# #                 str(r[0]).strip()
# #                 for r in cur.fetchall()
# #             ]

# #     except Exception as e:

# #         print(
# #             f"[APPROVED VENDOR] "
# #             f"D365 fetch error "
# #             f"for {vendor_account}: {e}"
# #         )

# #         return []


# # # ============================================================
# # # RFQ HISTORY
# # # ============================================================
# # def get_rfq_history_sync(
# #     vendor_account: str
# # ):

# #     result = []

# #     # ========================================================
# #     # STEP 1 - PORTAL RFQS
# #     # ========================================================
# #     with get_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute(f"""
# #             SELECT
# #                 RFQCASEID,
# #                 RFQID,
# #                 SUBMISSIONSTATUS,
# #                 DRAFTLINECOUNT,
# #                 SENDTOD365AT

# #             FROM {RFQ_REPLIES_TABLE}
# #             WITH (NOLOCK)

# #             WHERE VENDORACCOUNT = ?
# #         """, (vendor_account,))

# #         portal_rows = cur.fetchall()

# #     portal_map = {}

# #     for row in portal_rows:

# #         rfq_id = normalize(row[1])

# #         portal_map[rfq_id] = {

# #             "case_id":
# #                 row[0],

# #             "submission_status":
# #                 row[2],

# #             "draft_line_count":
# #                 row[3],

# #             "submitted_on":
# #                 row[4]
# #         }

# #     # ========================================================
# #     # STEP 2 - D365 RFQS
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute("""
# #             SELECT
# #                 L.RFQCASEID,
# #                 T.RFQID,
# #                 L.CREATEDDATETIME,
# #                 L.EXPIRYDATETIME,
# #                 L.DELIVERYDATE,
# #                 PM.NAME,
# #                 PT.DESCRIPTION,
# #                 DM.TXT,
# #                 DT.TXT

# #             FROM PurchRFQCaseTable L
# #             WITH (NOLOCK)

# #             INNER JOIN PurchRFQTable T
# #             WITH (NOLOCK)

# #                 ON T.RFQCASEID = L.RFQCASEID
# #                AND T.VENDACCOUNT = ?

# #             LEFT JOIN VENDPAYMMODETABLE PM
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN PAYMTERM PT
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN DLVMODE DM
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT
# #                 ON L.DLVTERM = DT.CODE

# #             ORDER BY L.EXPIRYDATETIME DESC
# #         """, (vendor_account,))

# #         rows = cur.fetchall()

# #         cols = [
# #             c[0]
# #             for c in cur.description
# #         ]

# #     for row in rows:

# #         data = dict(zip(cols, row))

# #         rfq_id = normalize(data["RFQID"])

# #         portal = portal_map.get(rfq_id)

# #         # ====================================================
# #         # STATUS
# #         # ====================================================
# #         status = "Expired"

# #         expired_status = "Not Opened"

# #         if portal:

# #             if (
# #                 portal["submission_status"] == 1
# #             ):

# #                 # ============================================
# #                 # CHECK COMPLETED
# #                 # ============================================
# #                 with get_d365_connection() as conn:

# #                     cur = conn.cursor()

# #                     cur.execute("""
# #                         SELECT TOP 1 1

# #                         FROM PURCHRFQREPLYLINE RL
# #                         WITH (NOLOCK)

# #                         INNER JOIN PURCHRFQLINE PL
# #                         WITH (NOLOCK)

# #                             ON PL.RECID
# #                                = RL.RFQLINERECID

# #                            AND PL.DATAAREAID = 'hi-q'

# #                         WHERE RL.RFQID = ?
# #                           AND RL.DATAAREAID = 'hi-q'
# #                           AND PL.STATUS >= 3
# #                     """, (rfq_id,))

# #                     completed = cur.fetchone()

# #                 if completed:

# #                     status = "Completed"

# #                 else:

# #                     expired_status = "Drafted"

# #         result.append({

# #             "rfq_no":
# #                 data["RFQID"],

# #             "case_id":
# #                 data["RFQCASEID"],

# #             "created_date":
# #                 format_ist_date_only(
# #                     data["CREATEDDATETIME"]
# #                 ),

# #             "expiry_date":
# #                 format_ist_date_only(
# #                     data["EXPIRYDATETIME"]
# #                 ),

# #             "delivery_date":
# #                 format_ist_date_only(
# #                     data["DELIVERYDATE"]
# #                 ),

# #             "mode_of_delivery":
# #                 data["TXT"] or "-",

# #             "delivery_term":
# #                 data["TXT"] or "-",

# #             "payment_term":
# #                 data["DESCRIPTION"] or "-",

# #             "payment_mode":
# #                 data["NAME"] or "-",

# #             "expired_status":
# #                 expired_status,

# #             "status":
# #                 status
# #         })

# #     return {

# #         "status": "success",

# #         "count": len(result),

# #         "data": result
# #     }


# # async def get_rfq_history(
# #     vendor_account: str
# # ):
# #     return await run_in_threadpool(
# #         get_rfq_history_sync,
# #         vendor_account
# #     )

# # # ============================================================
# # # EXPIRED RFQ DETAIL
# # # ============================================================
# # def fetch_rfq_detail_sync(
# #     rfq_id: str,
# #     vendor_account: str
# # ) -> Dict[str, Any]:

# #     # ========================================================
# #     # APPROVED ITEMS
# #     # ========================================================
# #     approved_items = _get_approved_items(
# #         vendor_account
# #     )

# #     # ========================================================
# #     # D365 HEADER + LINE DATA
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cursor = conn.cursor()

# #         # ====================================================
# #         # HEADER
# #         # ====================================================
# #         cursor.execute("""
# #             SELECT TOP 1

# #                 L.RFQCASEID,

# #                 T.RFQID,

# #                 L.NAME AS DOCUMENT_TITLE,

# #                 L.EXPIRYDATETIME AS CLOSING_DATE,

# #                 L.CREATEDDATETIME AS ISSUE_DATE,

# #                 L.DELIVERYDATE AS EXPECTED_DELIVERY_DATE,

# #                 PT.DESCRIPTION AS PAYMENT_TERM,

# #                 PM.NAME AS METHOD_OF_PAYMENT,

# #                 DM.TXT AS MODE_OF_DELIVERY,

# #                 DT.TXT AS DELIVERY_TERM,

# #                 T.HIQ_TERMSANDCONDITIONS,

# #                 T.CURRENCYCODE

# #             FROM PurchRFQCaseTable L
# #             WITH (NOLOCK)

# #             INNER JOIN PurchRFQTable T
# #             WITH (NOLOCK)

# #                 ON T.RFQCASEID = L.RFQCASEID
# #                AND T.VENDACCOUNT = ?

# #             LEFT JOIN PAYMTERM PT
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN VENDPAYMMODETABLE PM
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN DLVMODE DM
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE T.RFQID = ?
# #         """, (
# #             vendor_account,
# #             rfq_id
# #         ))

# #         row = cursor.fetchone()

# #         if not row:

# #             return {
# #                 "success": False,
# #                 "message": "RFQ not found"
# #             }

# #         cols = [
# #             c[0]
# #             for c in cursor.description
# #         ]

# #         header = dict(zip(cols, row))

# #         # ====================================================
# #         # LINE ITEMS
# #         # ====================================================
# #         lines = []

# #         if approved_items:

# #             placeholders = ",".join(
# #                 ["?" for _ in approved_items]
# #             )

# #             cursor.execute(f"""
# #                 SELECT

# #                     RL.LINENUM,

# #                     RL.ITEMID AS MATERIAL_CODE,

# #                     IT.NAMEALIAS AS MATERIAL_DESCRIPTION,

# #                     RL.QTYORDERED AS QUANTITY,

# #                     RL.PURCHUNIT AS UOM,

# #                     RL.HIQ_TARGETPRICE AS TARGETPRICE,

# #                     RL.HIQ_COMMENTS AS COMMENTS,

# #                     RL.CURRENCYCODE,

# #                     RL.DELIVERYDATE AS LINE_DELIVERY_DATE,

# #                     RPL.DELIVERYDATE
# #                         AS VENDORREPLY_DELIVERY_DATE

# #                 FROM PurchRFQLine RL
# #                 WITH (NOLOCK)

# #                 LEFT JOIN PURCHRFQREPLYLINE RPL
# #                 WITH (NOLOCK)

# #                     ON RPL.RFQLINERECID = RL.RECID
# #                    AND RPL.DATAAREAID = 'hi-q'

# #                 LEFT JOIN INVENTTABLE IT
# #                 WITH (NOLOCK)

# #                     ON IT.ITEMID = RL.ITEMID

# #                 INNER JOIN PDSAPPROVEDVENDORLIST AVL
# #                 WITH (NOLOCK)

# #                     ON AVL.ITEMID = RL.ITEMID
# #                    AND AVL.PDSAPPROVEDVENDOR = ?
# #                    AND AVL.VALIDFROM <= GETUTCDATE()
# #                    AND AVL.VALIDTO >= GETUTCDATE()

# #                 WHERE RL.RFQID = ?
# #                   AND RL.ITEMID IN ({placeholders})

# #                 ORDER BY RL.LINENUM
# #             """, (
# #                 [vendor_account, rfq_id]
# #                 + approved_items
# #             ))

# #             line_rows = cursor.fetchall()

# #             line_cols = [
# #                 c[0]
# #                 for c in cursor.description
# #             ]

# #             lines = [
# #                 dict(zip(line_cols, r))
# #                 for r in line_rows
# #             ]

# #     # ========================================================
# #     # PORTAL DRAFT DATA
# #     # ========================================================
# #     saved_price_map = {}

# #     saved_header = {}

# #     with get_connection() as conn:

# #         cursor = conn.cursor()

# #         cursor.execute(f"""
# #             SELECT TOP 1
# #                 PAYLOADJSON

# #             FROM {RFQ_REPLIES_TABLE}
# #             WITH (NOLOCK)

# #             WHERE RFQID = ?
# #               AND VENDORACCOUNT = ?

# #             ORDER BY ID DESC
# #         """, (
# #             rfq_id,
# #             vendor_account
# #         ))

# #         draft_row = cursor.fetchone()

# #     # ========================================================
# #     # DRAFT PARSE
# #     # ========================================================
# #     if draft_row and draft_row[0]:

# #         try:

# #             payload = json.loads(
# #                 draft_row[0]
# #             )

# #             saved_header = {

# #                 "modeOfDelivery":
# #                     payload.get(
# #                         "modeOfDelivery", ""
# #                     ),

# #                 "DeliveryTerms":
# #                     payload.get(
# #                         "DeliveryTerms", ""
# #                     ),

# #                 "methodOfPayment":
# #                     payload.get(
# #                         "methodOfPayment", ""
# #                     ),

# #                 "termsOfPayment":
# #                     payload.get(
# #                         "termsOfPayment", ""
# #                     ),

# #                 "replyDeliveryDate":
# #                     payload.get(
# #                         "replyDeliveryDate", ""
# #                     ),

# #                 "replyDeliveryTerms":
# #                     payload.get(
# #                         "replyDeliveryTerms", ""
# #                     ),

# #                 "replyModeOfDelivery":
# #                     payload.get(
# #                         "replyModeOfDelivery", ""
# #                     ),

# #                 "vendorComments":
# #                     payload.get(
# #                         "vendorComments", ""
# #                     )
# #             }

# #             for item in payload.get("Item", []):

# #                 item_number = item.get(
# #                     "itemNumber"
# #                 )

# #                 if item_number:

# #                     saved_price_map[
# #                         item_number
# #                     ] = {

# #                         "unit_price":
# #                             float(
# #                                 item.get(
# #                                     "unitPrice"
# #                                 ) or 0
# #                             ),

# #                         "net_amount":
# #                             float(
# #                                 item.get(
# #                                     "netAmount"
# #                                 ) or 0
# #                             ),

# #                         "vendor_comments":
# #                             item.get(
# #                                 "vendorComments", ""
# #                             ),

# #                         "line_status":
# #                             item.get(
# #                                 "lineStatus",
# #                                 False
# #                             )
# #                     }

# #         except Exception as e:

# #             print(
# #                 f"[DRAFT PARSE ERROR] {e}"
# #             )

# #     # ========================================================
# #     # BUILD LINE ITEMS
# #     # ========================================================
# #     line_items = []

# #     for item in lines:

# #         material_code = item[
# #             "MATERIAL_CODE"
# #         ]

# #         saved = saved_price_map.get(
# #             material_code,
# #             {}
# #         )

# #         if saved.get(
# #             "line_status",
# #             False
# #         ):
# #             continue

# #         line_items.append({

# #             "line_num":
# #                 int(item["LINENUM"]),

# #             "item_name":
# #                 item[
# #                     "MATERIAL_DESCRIPTION"
# #                 ],

# #             "item_id":
# #                 material_code,

# #             "quantity":
# #                 item["QUANTITY"],

# #             "uom":
# #                 item["UOM"],

# #             "target_price":
# #                 round(
# #                     float(
# #                         item[
# #                             "TARGETPRICE"
# #                         ] or 0
# #                     ),
# #                     2
# #                 ),

# #             "currency":
# #                 item["CURRENCYCODE"],

# #             "comments":
# #                 item["COMMENTS"],

# #             "unit_price":
# #                 saved.get(
# #                     "unit_price", ""
# #                 ),

# #             "net_amount":
# #                 saved.get(
# #                     "net_amount", ""
# #                 ),

# #             "vendor_comments":
# #                 saved.get(
# #                     "vendor_comments", ""
# #                 ),

# #             "rfq_delivery_date":
# #                 format_ist_date_only(
# #                     item.get(
# #                         "LINE_DELIVERY_DATE"
# #                     )
# #                 ),

# #             "vendor_delivery_date":
# #                 format_ist_date_only(
# #                     item.get(
# #                         "VENDORREPLY_DELIVERY_DATE"
# #                     )
# #                 ),

# #             "hiq_decision":
# #                 "Expired"
# #         })

# #     # ========================================================
# #     # FINAL RESPONSE
# #     # ========================================================
# #     return {

# #         "success": True,

# #         "has_draft":
# #             bool(saved_price_map),

# #         "data": {

# #             "rfq_case_id":
# #                 header["RFQCASEID"],

# #             "rfq_id":
# #                 header["RFQID"],

# #             "document_title":
# #                 header["DOCUMENT_TITLE"],

# #             "issue_date":
# #                 format_ist_date_only(
# #                     header["ISSUE_DATE"]
# #                 ),

# #             "closing_date":
# #                 format_ist_date_only(
# #                     header["CLOSING_DATE"]
# #                 ),

# #             "time_remaining":
# #                 calculate_days_left(
# #                     header["CLOSING_DATE"]
# #                 ),

# #             "delivery_date":
# #                 format_ist_date_only(
# #                     header[
# #                         "EXPECTED_DELIVERY_DATE"
# #                     ]
# #                 ),

# #             "payment_term":
# #                 header["PAYMENT_TERM"]
# #                 or "-",

# #             "payment_mode":
# #                 header[
# #                     "METHOD_OF_PAYMENT"
# #                 ] or "-",

# #             "delivery_term":
# #                 header["DELIVERY_TERM"]
# #                 or "-",

# #             "delivery_mode":
# #                 header[
# #                     "MODE_OF_DELIVERY"
# #                 ] or "-",

# #             "termsandconditions":
# #                 header[
# #                     "HIQ_TERMSANDCONDITIONS"
# #                 ],

# #             "currency":
# #                 header["CURRENCYCODE"],

# #             "saved_mode_of_delivery":
# #                 saved_header.get(
# #                     "modeOfDelivery", ""
# #                 ),

# #             "saved_delivery_terms":
# #                 saved_header.get(
# #                     "DeliveryTerms", ""
# #                 ),

# #             "saved_method_of_payment":
# #                 saved_header.get(
# #                     "methodOfPayment", ""
# #                 ),

# #             "saved_terms_of_payment":
# #                 saved_header.get(
# #                     "termsOfPayment", ""
# #                 ),

# #             "reply_delivery_date":
# #                 saved_header.get(
# #                     "replyDeliveryDate", ""
# #                 ),

# #             "reply_delivery_mode":
# #                 saved_header.get(
# #                     "replyModeOfDelivery", ""
# #                 ),

# #             "reply_delivery_term":
# #                 saved_header.get(
# #                     "replyDeliveryTerms", ""
# #                 ),

# #             "saved_vendor_comments":
# #                 saved_header.get(
# #                     "vendorComments", ""
# #                 ),

# #             "line_items":
# #                 line_items
# #         }
# #     }
# # # ============================================================
# # # COMPLETED RFQ DETAIL
# # # ============================================================
# # def fetch_completed_rfq_detail_sync(
# #     rfq_id: str,
# #     vendor_account: str
# # ) -> Dict[str, Any]:

# #     approved_items = _get_approved_items(
# #         vendor_account
# #     )

# #     # ========================================================
# #     # PORTAL DATA
# #     # ========================================================
# #     with get_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute(f"""
# #             SELECT TOP 1
# #                 RFQCASEID

# #             FROM {RFQ_REPLIES_TABLE}
# #             WITH (NOLOCK)

# #             WHERE RFQID = ?
# #               AND VENDORACCOUNT = ?
# #         """, (
# #             rfq_id,
# #             vendor_account
# #         ))

# #         portal_row = cur.fetchone()

# #     if not portal_row:

# #         return {
# #             "success": False,
# #             "message": "RFQ not found"
# #         }

# #     rfq_case_id = portal_row[0]

# #     # ========================================================
# #     # D365 HEADER
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute("""
# #             SELECT TOP 1

# #                 RT.RFQID,

# #                 RT.CURRENCYCODE,

# #                 RT.DELIVERYDATE,

# #                 RT.DLVMODE,

# #                 RT.DLVTERM,

# #                 RT.PAYMENT,

# #                 RT.VENDREF,

# #                 RT.TOTALSCORE,

# #                 RT.RANK,

# #                 RT.VALIDFROM,

# #                 RT.VALIDTO,

# #                 RT.VALIDITYDATESTART,

# #                 RT.VALIDITYDATEEND,

# #                 RT.REPLYPROGRESSSTATUS,

# #                 RT.HIQ_COMMENTS,

# #                 L.EXPIRYDATETIME,

# #                 L.CREATEDDATETIME,

# #                 L.DELIVERYDATE,

# #                 L.NAME,

# #                 PM.NAME,

# #                 PT.DESCRIPTION,

# #                 DM.TXT,

# #                 DT.TXT,

# #                 T.HIQ_TERMSANDCONDITIONS

# #             FROM PURCHRFQREPLYTABLE RT
# #             WITH (NOLOCK)

# #             LEFT JOIN PurchRFQCaseTable L
# #             WITH (NOLOCK)

# #                 ON L.RFQCASEID = ?

# #             LEFT JOIN PurchRFQTable T
# #             WITH (NOLOCK)

# #                 ON T.RFQCASEID = L.RFQCASEID
# #                AND T.VENDACCOUNT = ?

# #             LEFT JOIN VENDPAYMMODETABLE PM
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN PAYMTERM PT
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN DLVMODE DM
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE RT.RFQID = ?
# #               AND RT.DATAAREAID = 'hi-q'
# #         """, (
# #             rfq_case_id,
# #             vendor_account,
# #             rfq_id
# #         ))

# #         row = cur.fetchone()

# #         if not row:

# #             return {
# #                 "success": False,
# #                 "message": "RFQ not found"
# #             }

# #         cols = [
# #             c[0]
# #             for c in cur.description
# #         ]

# #         header = dict(zip(cols, row))

# #         lines = []

# #         if approved_items:

# #             placeholders = ",".join(
# #                 ["?" for _ in approved_items]
# #             )

# #             cur.execute(f"""
# #                 SELECT

# #                     RL.LINENUM,

# #                     RL.NAME,

# #                     RL.PURCHQTY,

# #                     RL.PURCHUNIT,

# #                     RL.PURCHPRICE,

# #                     RL.LINEAMOUNT,

# #                     RL.LINEDISC,

# #                     RL.LINEPERCENT,

# #                     RL.DELIVERYDATE,

# #                     RL.LEADTIME,

# #                     RL.HIQ_COMMENTS,

# #                     RL.VALIDFROM,

# #                     RL.VALIDTO,

# #                     RL.EXTERNALITEMID,

# #                     RL.MAXIMUMRETAILPRICE_IN,

# #                     PL.HIQ_TARGETPRICE,

# #                     PL.HIQ_COMMENTS,

# #                     PL.ITEMID,

# #                     PL.STATUS,

# #                     PL.CURRENCYCODE,

# #                     PL.PURCHID,

# #                     PL.DELIVERYDATE,

# #                     RL.DELIVERYDATE

# #                 FROM PURCHRFQREPLYLINE RL
# #                 WITH (NOLOCK)

# #                 INNER JOIN PURCHRFQLINE PL
# #                 WITH (NOLOCK)

# #                     ON PL.RECID
# #                        = RL.RFQLINERECID

# #                    AND PL.DATAAREAID = 'hi-q'

# #                 WHERE RL.RFQID = ?
# #                   AND RL.DATAAREAID = 'hi-q'
# #                   AND PL.STATUS >= 3
# #                   AND PL.ITEMID IN ({placeholders})

# #                 ORDER BY RL.LINENUM
# #             """, [rfq_id] + approved_items)

# #             line_rows = cur.fetchall()

# #             line_cols = [
# #                 c[0]
# #                 for c in cur.description
# #             ]

# #             lines = [
# #                 dict(zip(line_cols, r))
# #                 for r in line_rows
# #             ]

# #     return {

# #         "success": True,

# #         "data": {

# #             "rfq_id":
# #                 rfq_id,

# #             "rfq_case_id":
# #                 rfq_case_id,

# #             "document_title":
# #                 header["NAME"],

# #             "issue_date":
# #                 format_ist_date_only(
# #                     header["CREATEDDATETIME"]
# #                 ),

# #             "closing_date":
# #                 format_ist_date_only(
# #                     header["EXPIRYDATETIME"]
# #                 ),

# #             "delivery_date":
# #                 format_ist_date_only(
# #                     header["DELIVERYDATE"]
# #                 ),

# #             "currency":
# #                 header["CURRENCYCODE"],

# #             "termsandconditions":
# #                 header["HIQ_TERMSANDCONDITIONS"],

# #             "line_items": [

# #                 {

# #                     "line_num":
# #                         int(float(line["LINENUM"])),

# #                     "item_id":
# #                         line["ITEMID"],

# #                     "item_name":
# #                         line["NAME"],

# #                     "quantity":
# #                         float(line["PURCHQTY"] or 0),

# #                     "uom":
# #                         line["PURCHUNIT"],

# #                     "unit_price":
# #                         float(line["PURCHPRICE"] or 0),

# #                     "net_amount":
# #                         float(line["LINEAMOUNT"] or 0),

# #                     "hiq_decision":
# #                         "Accepted"
# #                         if line["STATUS"] == 4
# #                         else "Rejected"

# #                 }

# #                 for line in lines
# #             ]
# #         }
# #     }


# # # ============================================================
# # # RFQ HISTORY DETAIL
# # # ============================================================
# # def get_rfq_history_detail_sync(
# #     rfq_id: str,
# #     vendor_account: str,
# #     status: str
# # ):

# #     status = (
# #         status or ""
# #     ).lower()

# #     if status == "completed":

# #         result = fetch_completed_rfq_detail_sync(
# #             rfq_id,
# #             vendor_account
# #         )

# #     elif status == "expired":

# #         result = fetch_rfq_detail_sync(
# #             rfq_id,
# #             vendor_account
# #         )

# #     else:

# #         return {

# #             "success": False,

# #             "message":
# #                 "Invalid status"
# #         }

# #     if not result.get("success"):
# #         return result

# #     vendor_profile = fetch_vendor_profile_sync(
# #         vendor_account
# #     )

# #     return {

# #         "success": True,

# #         "type":
# #             status.capitalize(),

# #         "data": {

# #             **result.get("data"),

# #             "vendor_information": {

# #                 "vendor_account":
# #                     vendor_account,

# #                 "vendor_name":
# #                     vendor_profile.get("name") or "-",

# #                 "email":
# #                     vendor_profile.get("email") or "-",

# #                 "phone":
# #                     vendor_profile.get("phone") or "-",

# #                 "address":
# #                     vendor_profile.get("address") or "-",

# #                 "city":
# #                     vendor_profile.get("city") or "-"
# #             }
# #         }
# #     }

# # async def get_rfq_history_detail(
# #     rfq_id: str,
# #     vendor_account: str,
# #     status: str
# # ):
# #     return await run_in_threadpool(
# #         get_rfq_history_detail_sync,
# #         rfq_id,
# #         vendor_account,
# #         status
# #     )





# # import json

# # from typing import (
# #     List,
# #     Dict,
# #     Any
# # )

# # from fastapi.concurrency import (
# #     run_in_threadpool
# # )

# # from app.db.base import (
# #     get_connection,
# #     get_d365_connection
# # )

# # from app.core.config import settings

# # from app.utils.date_utils import (
# #     format_ist_date_only
# # )

# # from app.utils.remainingdate import (
# #     calculate_days_left
# # )


# # SCHEMA = settings.DB_SCHEMA

# # RFQ_REPLIES_TABLE = (
# #     f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
# # )


# # # ============================================================
# # # HELPERS
# # # ============================================================
# # def normalize(val):
# #     return str(val or "").strip().upper()


# # # ============================================================
# # # VENDOR PROFILE
# # # ============================================================
# # def fetch_vendor_profile_sync(
# #     vendor_account: str
# # ):

# #     profile = {
# #         "email":   None,
# #         "phone":   None,
# #         "address": None,
# #         "name":    None,
# #         "city":    None
# #     }

# #     with get_d365_connection() as conn:

# #         cursor = conn.cursor()

# #         # ====================================================
# #         # EMAIL + PHONE
# #         # ====================================================
# #         cursor.execute("""
# #             SELECT
# #                 TYPE,
# #                 LOCATOR

# #             FROM HIQ_vendorELECTRONICADDRESSVIEW
# #             WITH (NOLOCK)

# #             WHERE ACCOUNTNUM = ?
# #               AND ISPRIMARY1 = 1
# #         """, vendor_account)

# #         for row in cursor.fetchall():

# #             if row.TYPE == 2:
# #                 profile["email"] = row.LOCATOR

# #             elif row.TYPE == 1:
# #                 profile["phone"] = row.LOCATOR

# #         # ====================================================
# #         # ADDRESS
# #         # ====================================================
# #         cursor.execute("""
# #             SELECT TOP 1
# #                 ADDRESS,
# #                 NAME,
# #                 CITY

# #             FROM HIQ_vendorPostalADDRESSVIEW
# #             WITH (NOLOCK)

# #             WHERE ACCOUNTNUM = ?
# #               AND ISPRIMARY = 1
# #         """, vendor_account)

# #         row = cursor.fetchone()

# #         if row:
# #             profile["address"] = row.ADDRESS
# #             profile["name"]    = row.NAME
# #             profile["city"]    = row.CITY

# #     return profile


# # async def fetch_vendor_profile(
# #     vendor_account: str
# # ):
# #     return await run_in_threadpool(
# #         fetch_vendor_profile_sync,
# #         vendor_account
# #     )


# # # ============================================================
# # # APPROVED ITEMS
# # # ============================================================
# # def _get_approved_items(
# #     vendor_account: str
# # ) -> List[str]:

# #     try:

# #         with get_d365_connection() as conn:

# #             cur = conn.cursor()

# #             cur.execute("""
# #                 SELECT ITEMID

# #                 FROM PDSAPPROVEDVENDORLIST
# #                 WITH (NOLOCK)

# #                 WHERE PDSAPPROVEDVENDOR = ?
# #                   AND DATAAREAID        = 'hi-q'
# #                   AND VALIDFROM        <= GETUTCDATE()
# #                   AND VALIDTO          >= GETUTCDATE()
# #             """, (vendor_account,))

# #             return [
# #                 str(r[0]).strip()
# #                 for r in cur.fetchall()
# #             ]

# #     except Exception as e:

# #         print(
# #             f"[APPROVED VENDOR] "
# #             f"D365 fetch error "
# #             f"for {vendor_account}: {e}"
# #         )

# #         return []


# # # ============================================================
# # # RFQ HISTORY
# # # ============================================================
# # def get_rfq_history_sync(
# #     vendor_account: str
# # ):

# #     result = []

# #     # ========================================================
# #     # STEP 1 — PORTAL REPLIES MAP
# #     # Read vendor's saved/submitted replies from portal DB
# #     # ========================================================
# #     with get_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute(f"""
# #             SELECT
# #                 RFQCASEID,
# #                 RFQID,
# #                 SUBMISSIONSTATUS,
# #                 DRAFTLINECOUNT,
# #                 SENDTOD365AT

# #             FROM {RFQ_REPLIES_TABLE}
# #             WITH (NOLOCK)

# #             WHERE VENDORACCOUNT = ?
# #         """, (vendor_account,))

# #         portal_rows = cur.fetchall()

# #     portal_map = {}

# #     for row in portal_rows:

# #         rfq_id = normalize(row[1])

# #         portal_map[rfq_id] = {
# #             "case_id":           row[0],
# #             "submission_status": row[2],
# #             "draft_line_count":  row[3],
# #             "submitted_on":      row[4]
# #         }

# #     # ========================================================
# #     # STEP 2 — D365 RFQs FOR HISTORY
# #     #
# #     # Only show in history if:
# #     #   A) Expired    — EXPIRYDATETIME DATE < today DATE
# #     #   B) Completed  — D365 lines STATUS >= 3
# #     #                   (accepted/rejected by HiQ)
# #     #
# #     # FIX: CAST to DATE to ignore time part
# #     # FIX: Alias DM.TXT and DT.TXT separately
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute("""
# #             SELECT
# #                 L.RFQCASEID,
# #                 T.RFQID,
# #                 L.CREATEDDATETIME,
# #                 L.EXPIRYDATETIME,
# #                 L.DELIVERYDATE,
# #                 PM.NAME         AS PAYMENT_MODE,
# #                 PT.DESCRIPTION  AS PAYMENT_TERM,
# #                 DM.TXT          AS MODE_OF_DELIVERY,
# #                 DT.TXT          AS DELIVERY_TERM

# #             FROM PurchRFQCaseTable L
# #             WITH (NOLOCK)

# #             INNER JOIN PurchRFQTable T
# #             WITH (NOLOCK)

# #                 ON  T.RFQCASEID  = L.RFQCASEID
# #                 AND T.VENDACCOUNT = ?

# #             LEFT JOIN VENDPAYMMODETABLE PM
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN PAYMTERM PT
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN DLVMODE DM
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE (

# #                 -- ============================================
# #                 -- A) EXPIRED
# #                 -- Compare DATE only — ignore time
# #                 -- So RFQ expiring today stays in active tab
# #                 -- and moves to history only from tomorrow
# #                 -- ============================================
# #                 --CAST(L.EXPIRYDATETIME AS DATE)
# #                 CAST(DATEADD(MINUTE, 330, L.EXPIRYDATETIME) As Date)  
# #                     < CAST(GETUTCDATE() AS DATE)

# #                 OR

# #                 -- ============================================
# #                 -- B) COMPLETED
# #                 -- Vendor submitted + HiQ accepted/rejected
# #                 -- lines (PurchRFQLine STATUS >= 3)
# #                 -- ============================================
# #                 EXISTS (
# #                     SELECT 1
# #                     FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)
# #                     INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
# #                         ON  PL.RECID      = RL.RFQLINERECID
# #                         AND PL.DATAAREAID = 'hi-q'
# #                     WHERE RL.RFQID      = T.RFQID
# #                       AND RL.DATAAREAID = 'hi-q'
# #                       AND PL.STATUS     >= 3
# #                 )
# #             )

# #             ORDER BY L.EXPIRYDATETIME DESC
# #         """, (vendor_account,))

# #         rows = cur.fetchall()

# #         cols = [c[0] for c in cur.description]

# #     # ========================================================
# #     # STEP 3 — BUILD RESULT WITH STATUS LOGIC
# #     # ========================================================
# #     for row in rows:

# #         data   = dict(zip(cols, row))
# #         rfq_id = normalize(data["RFQID"])
# #         portal = portal_map.get(rfq_id)

# #         # ====================================================
# #         # CHECK — D365 lines accepted/rejected
# #         # ====================================================
# #         with get_d365_connection() as conn:

# #             cur = conn.cursor()

# #             cur.execute("""
# #                 SELECT TOP 1 1

# #                 FROM PURCHRFQREPLYLINE RL
# #                 WITH (NOLOCK)

# #                 INNER JOIN PURCHRFQLINE PL
# #                 WITH (NOLOCK)

# #                     ON  PL.RECID      = RL.RFQLINERECID
# #                     AND PL.DATAAREAID = 'hi-q'

# #                 WHERE RL.RFQID      = ?
# #                   AND RL.DATAAREAID = 'hi-q'
# #                   AND PL.STATUS     >= 3
# #             """, (rfq_id,))

# #             completed = cur.fetchone()

# #         # ====================================================
# #         # DETERMINE STATUS
# #         #
# #         # Priority:
# #         #   1. Completed  — D365 has decided lines
# #         #   2. Drafted    — vendor saved/submitted draft
# #         #   3. Not Opened — vendor never touched it
# #         # ====================================================
# #         if completed:

# #             # ================================================
# #             # COMPLETED — HiQ accepted or rejected lines
# #             # ================================================
# #             status         = "Completed"
# #             expired_status = "-"

# #         elif portal and (
# #             portal["submission_status"] == 1
# #             or int(portal["draft_line_count"] or 0) > 0
# #         ):

# #             # ================================================
# #             # DRAFTED — vendor saved draft or submitted
# #             # but HiQ has not yet decided
# #             # ================================================
# #             status         = "Expired"
# #             expired_status = "Drafted"

# #         else:

# #             # ================================================
# #             # NOT OPENED — vendor never opened/filled it
# #             # (not in portal replies table at all, or
# #             #  no draft lines saved)
# #             # ================================================
# #             status         = "Expired"
# #             expired_status = "Not Opened"

# #         result.append({

# #             "rfq_no":   data["RFQID"],
# #             "case_id":  data["RFQCASEID"],

# #             "created_date":  format_ist_date_only(
# #                                  data["CREATEDDATETIME"]
# #                              ),
# #             "expiry_date":   format_ist_date_only(
# #                                  data["EXPIRYDATETIME"]
# #                              ),
# #             "delivery_date": format_ist_date_only(
# #                                  data["DELIVERYDATE"]
# #                              ),

# #             # FIX: use correct aliased column names
# #             "mode_of_delivery": data["MODE_OF_DELIVERY"] or "-",
# #             "delivery_term":    data["DELIVERY_TERM"]    or "-",
# #             "payment_term":     data["PAYMENT_TERM"]     or "-",
# #             "payment_mode":     data["PAYMENT_MODE"]     or "-",

# #             "expired_status": expired_status,
# #             "status":         status
# #         })

# #     return {
# #         "status": "success",
# #         "count":  len(result),
# #         "data":   result
# #     }


# # async def get_rfq_history(
# #     vendor_account: str
# # ):
# #     return await run_in_threadpool(
# #         get_rfq_history_sync,
# #         vendor_account
# #     )


# # # ============================================================
# # # EXPIRED RFQ DETAIL
# # # ============================================================
# # def fetch_rfq_detail_sync(
# #     rfq_id: str,
# #     vendor_account: str
# # ) -> Dict[str, Any]:

# #     # ========================================================
# #     # APPROVED ITEMS
# #     # ========================================================
# #     approved_items = _get_approved_items(vendor_account)

# #     # ========================================================
# #     # D365 HEADER + LINE DATA
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cursor = conn.cursor()

# #         # ====================================================
# #         # HEADER
# #         # ====================================================
# #         cursor.execute("""
# #             SELECT TOP 1

# #                 L.RFQCASEID,
# #                 T.RFQID,
# #                 L.NAME              AS DOCUMENT_TITLE,
# #                 L.EXPIRYDATETIME    AS CLOSING_DATE,
# #                 L.CREATEDDATETIME   AS ISSUE_DATE,
# #                 L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
# #                 PT.DESCRIPTION      AS PAYMENT_TERM,
# #                 PM.NAME             AS METHOD_OF_PAYMENT,
# #                 DM.TXT              AS MODE_OF_DELIVERY,
# #                 DT.TXT              AS DELIVERY_TERM,
# #                 T.HIQ_TERMSANDCONDITIONS,
# #                 T.CURRENCYCODE

# #             FROM PurchRFQCaseTable L
# #             WITH (NOLOCK)

# #             INNER JOIN PurchRFQTable T
# #             WITH (NOLOCK)

# #                 ON  T.RFQCASEID  = L.RFQCASEID
# #                 AND T.VENDACCOUNT = ?

# #             LEFT JOIN PAYMTERM PT
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN VENDPAYMMODETABLE PM
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN DLVMODE DM
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE T.RFQID = ?
# #         """, (vendor_account, rfq_id))

# #         row = cursor.fetchone()

# #         if not row:
# #             return {
# #                 "success": False,
# #                 "message": "RFQ not found"
# #             }

# #         cols   = [c[0] for c in cursor.description]
# #         header = dict(zip(cols, row))

# #         # ====================================================
# #         # LINE ITEMS
# #         # ====================================================
# #         lines = []

# #         if approved_items:

# #             placeholders = ",".join(
# #                 ["?" for _ in approved_items]
# #             )

# #             cursor.execute(f"""
# #                 SELECT

# #                     RL.LINENUM,
# #                     RL.ITEMID           AS MATERIAL_CODE,
# #                     IT.NAMEALIAS        AS MATERIAL_DESCRIPTION,
# #                     RL.QTYORDERED       AS QUANTITY,
# #                     RL.PURCHUNIT        AS UOM,
# #                     RL.HIQ_TARGETPRICE  AS TARGETPRICE,
# #                     RL.HIQ_COMMENTS     AS COMMENTS,
# #                     RL.CURRENCYCODE,
# #                     RL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
# #                     RPL.DELIVERYDATE    AS VENDORREPLY_DELIVERY_DATE

# #                 FROM PurchRFQLine RL
# #                 WITH (NOLOCK)

# #                 LEFT JOIN PURCHRFQREPLYLINE RPL
# #                 WITH (NOLOCK)

# #                     ON  RPL.RFQLINERECID = RL.RECID
# #                     AND RPL.DATAAREAID   = 'hi-q'

# #                 LEFT JOIN INVENTTABLE IT
# #                 WITH (NOLOCK)

# #                     ON IT.ITEMID = RL.ITEMID

# #                 INNER JOIN PDSAPPROVEDVENDORLIST AVL
# #                 WITH (NOLOCK)

# #                     ON  AVL.ITEMID            = RL.ITEMID
# #                     AND AVL.PDSAPPROVEDVENDOR = ?
# #                     AND AVL.VALIDFROM        <= GETUTCDATE()
# #                     AND AVL.VALIDTO          >= GETUTCDATE()

# #                 WHERE RL.RFQID    = ?
# #                   AND RL.ITEMID IN ({placeholders})

# #                 ORDER BY RL.LINENUM
# #             """, ([vendor_account, rfq_id] + approved_items))

# #             line_rows = cursor.fetchall()
# #             line_cols = [c[0] for c in cursor.description]
# #             lines     = [dict(zip(line_cols, r)) for r in line_rows]

# #     # ========================================================
# #     # PORTAL DRAFT DATA
# #     # ========================================================
# #     saved_price_map = {}
# #     saved_header    = {}

# #     with get_connection() as conn:

# #         cursor = conn.cursor()

# #         cursor.execute(f"""
# #             SELECT TOP 1
# #                 PAYLOADJSON

# #             FROM {RFQ_REPLIES_TABLE}
# #             WITH (NOLOCK)

# #             WHERE RFQID         = ?
# #               AND VENDORACCOUNT  = ?

# #             ORDER BY ID DESC
# #         """, (rfq_id, vendor_account))

# #         draft_row = cursor.fetchone()

# #     # ========================================================
# #     # DRAFT PARSE
# #     # ========================================================
# #     if draft_row and draft_row[0]:

# #         try:

# #             payload = json.loads(draft_row[0])

# #             saved_header = {
# #                 "modeOfDelivery":      payload.get("modeOfDelivery",      ""),
# #                 "DeliveryTerms":       payload.get("DeliveryTerms",        ""),
# #                 "methodOfPayment":     payload.get("methodOfPayment",      ""),
# #                 "termsOfPayment":      payload.get("termsOfPayment",       ""),
# #                 "replyDeliveryDate":   payload.get("replyDeliveryDate",    ""),
# #                 "replyDeliveryTerms":  payload.get("replyDeliveryTerms",   ""),
# #                 "replyModeOfDelivery": payload.get("replyModeOfDelivery",  ""),
# #                 "vendorComments":      payload.get("vendorComments",       "")
# #             }

# #             for item in payload.get("Item", []):

# #                 item_number = item.get("itemNumber")

# #                 if item_number:

# #                     saved_price_map[item_number] = {
# #                         "unit_price":      float(item.get("unitPrice")   or 0),
# #                         "net_amount":      float(item.get("netAmount")   or 0),
# #                         "vendor_comments": item.get("vendorComments",   ""),
# #                         "line_status":     item.get("lineStatus",       False)
# #                     }

# #         except Exception as e:
# #             print(f"[DRAFT PARSE ERROR] {e}")

# #     # ========================================================
# #     # BUILD LINE ITEMS
# #     # ========================================================
# #     line_items = []

# #     for item in lines:

# #         material_code = item["MATERIAL_CODE"]
# #         saved         = saved_price_map.get(material_code, {})

# #         if saved.get("line_status", False):
# #             continue

# #         line_items.append({
# #             "line_num":           int(item["LINENUM"]),
# #             "item_name":          item["MATERIAL_DESCRIPTION"],
# #             "item_id":            material_code,
# #             "quantity":           item["QUANTITY"],
# #             "uom":                item["UOM"],
# #             "target_price":       round(float(item["TARGETPRICE"] or 0), 2),
# #             "currency":           item["CURRENCYCODE"],
# #             "comments":           item["COMMENTS"],
# #             "unit_price":         saved.get("unit_price",      ""),
# #             "net_amount":         saved.get("net_amount",      ""),
# #             "vendor_comments":    saved.get("vendor_comments", ""),
# #             "rfq_delivery_date":  format_ist_date_only(
# #                                       item.get("LINE_DELIVERY_DATE")
# #                                   ),
# #             "vendor_delivery_date": format_ist_date_only(
# #                                         item.get("VENDORREPLY_DELIVERY_DATE")
# #                                     ),
# #             "hiq_decision": "Expired"
# #         })

# #     # ========================================================
# #     # FINAL RESPONSE
# #     # ========================================================
# #     return {

# #         "success":   True,
# #         "has_draft": bool(saved_price_map),

# #         "data": {

# #             "rfq_case_id":    header["RFQCASEID"],
# #             "rfq_id":         header["RFQID"],
# #             "document_title": header["DOCUMENT_TITLE"],

# #             "issue_date":   format_ist_date_only(header["ISSUE_DATE"]),
# #             "closing_date": format_ist_date_only(header["CLOSING_DATE"]),
# #             "time_remaining": calculate_days_left(header["CLOSING_DATE"]),
# #             "delivery_date": format_ist_date_only(
# #                                  header["EXPECTED_DELIVERY_DATE"]
# #                              ),

# #             "payment_term":  header["PAYMENT_TERM"]     or "-",
# #             "payment_mode":  header["METHOD_OF_PAYMENT"] or "-",
# #             "delivery_term": header["DELIVERY_TERM"]     or "-",
# #             "delivery_mode": header["MODE_OF_DELIVERY"]  or "-",

# #             "termsandconditions": header["HIQ_TERMSANDCONDITIONS"],
# #             "currency":           header["CURRENCYCODE"],

# #             "saved_mode_of_delivery":  saved_header.get("modeOfDelivery",      ""),
# #             "saved_delivery_terms":    saved_header.get("DeliveryTerms",        ""),
# #             "saved_method_of_payment": saved_header.get("methodOfPayment",      ""),
# #             "saved_terms_of_payment":  saved_header.get("termsOfPayment",       ""),
# #             "reply_delivery_date":     saved_header.get("replyDeliveryDate",    ""),
# #             "reply_delivery_mode":     saved_header.get("replyModeOfDelivery",  ""),
# #             "reply_delivery_term":     saved_header.get("replyDeliveryTerms",   ""),
# #             "saved_vendor_comments":   saved_header.get("vendorComments",       ""),

# #             "line_items": line_items
# #         }
# #     }


# # # ============================================================
# # # COMPLETED RFQ DETAIL
# # # ============================================================
# # def fetch_completed_rfq_detail_sync(
# #     rfq_id: str,
# #     vendor_account: str
# # ) -> Dict[str, Any]:

# #     approved_items = _get_approved_items(vendor_account)

# #     # ========================================================
# #     # PORTAL DATA — get case id
# #     # ========================================================
# #     with get_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute(f"""
# #             SELECT TOP 1
# #                 RFQCASEID

# #             FROM {RFQ_REPLIES_TABLE}
# #             WITH (NOLOCK)

# #             WHERE RFQID        = ?
# #               AND VENDORACCOUNT = ?
# #         """, (rfq_id, vendor_account))

# #         portal_row = cur.fetchone()

# #     if not portal_row:
# #         return {
# #             "success": False,
# #             "message": "RFQ not found"
# #         }

# #     rfq_case_id = portal_row[0]

# #     # ========================================================
# #     # D365 HEADER
# #     # ========================================================
# #     with get_d365_connection() as conn:

# #         cur = conn.cursor()

# #         cur.execute("""
# #             SELECT TOP 1

# #                 RT.RFQID,
# #                 RT.CURRENCYCODE,
# #                 RT.DELIVERYDATE,
# #                 RT.DLVMODE,
# #                 RT.DLVTERM,
# #                 RT.PAYMENT,
# #                 RT.VENDREF,
# #                 RT.TOTALSCORE,
# #                 RT.RANK,
# #                 RT.VALIDFROM,
# #                 RT.VALIDTO,
# #                 RT.VALIDITYDATESTART,
# #                 RT.VALIDITYDATEEND,
# #                 RT.REPLYPROGRESSSTATUS,
# #                 RT.HIQ_COMMENTS,

# #                 L.EXPIRYDATETIME,
# #                 L.CREATEDDATETIME,
# #                 L.DELIVERYDATE,
# #                 L.NAME,

# #                 PM.NAME         AS PAYMENT_MODE,
# #                 PT.DESCRIPTION  AS PAYMENT_TERM,
# #                 DM.TXT          AS MODE_OF_DELIVERY,
# #                 DT.TXT          AS DELIVERY_TERM,

# #                 T.HIQ_TERMSANDCONDITIONS

# #             FROM PURCHRFQREPLYTABLE RT
# #             WITH (NOLOCK)

# #             LEFT JOIN PurchRFQCaseTable L
# #             WITH (NOLOCK)

# #                 ON L.RFQCASEID = ?

# #             LEFT JOIN PurchRFQTable T
# #             WITH (NOLOCK)

# #                 ON  T.RFQCASEID  = L.RFQCASEID
# #                 AND T.VENDACCOUNT = ?

# #             LEFT JOIN VENDPAYMMODETABLE PM
# #                 ON L.PAYMMODE = PM.PAYMMODE

# #             LEFT JOIN PAYMTERM PT
# #                 ON L.PAYMENT = PT.PAYMTERMID

# #             LEFT JOIN DLVMODE DM
# #                 ON L.DLVMODE = DM.CODE

# #             LEFT JOIN DLVTERM DT
# #                 ON L.DLVTERM = DT.CODE

# #             WHERE RT.RFQID      = ?
# #               AND RT.DATAAREAID = 'hi-q'
# #         """, (rfq_case_id, vendor_account, rfq_id))

# #         row = cur.fetchone()

# #         if not row:
# #             return {
# #                 "success": False,
# #                 "message": "RFQ not found"
# #             }

# #         cols   = [c[0] for c in cur.description]
# #         header = dict(zip(cols, row))

# #         # ====================================================
# #         # LINE ITEMS — only accepted/rejected (STATUS >= 3)
# #         # ====================================================
# #         lines = []

# #         if approved_items:

# #             placeholders = ",".join(
# #                 ["?" for _ in approved_items]
# #             )

# #             cur.execute(f"""
# #                 SELECT

# #                     RL.LINENUM,
# #                     RL.NAME,
# #                     RL.PURCHQTY,
# #                     RL.PURCHUNIT,
# #                     RL.PURCHPRICE,
# #                     RL.LINEAMOUNT,
# #                     RL.LINEDISC,
# #                     RL.LINEPERCENT,
# #                     RL.DELIVERYDATE,
# #                     RL.LEADTIME,
# #                     RL.HIQ_COMMENTS,
# #                     RL.VALIDFROM,
# #                     RL.VALIDTO,
# #                     RL.EXTERNALITEMID,
# #                     RL.MAXIMUMRETAILPRICE_IN,
# #                     PL.HIQ_TARGETPRICE,
# #                     PL.HIQ_COMMENTS,
# #                     PL.ITEMID,
# #                     PL.STATUS,
# #                     PL.CURRENCYCODE,
# #                     PL.PURCHID,
# #                     PL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
# #                     RL.DELIVERYDATE     AS VENDORREPLY_DELIVERY_DATE

# #                 FROM PURCHRFQREPLYLINE RL
# #                 WITH (NOLOCK)

# #                 INNER JOIN PURCHRFQLINE PL
# #                 WITH (NOLOCK)

# #                     ON  PL.RECID      = RL.RFQLINERECID
# #                     AND PL.DATAAREAID = 'hi-q'

# #                 WHERE RL.RFQID      = ?
# #                   AND RL.DATAAREAID = 'hi-q'
# #                   AND PL.STATUS     >= 3
# #                   AND PL.ITEMID     IN ({placeholders})

# #                 ORDER BY RL.LINENUM
# #             """, [rfq_id] + approved_items)

# #             line_rows = cur.fetchall()
# #             line_cols = [c[0] for c in cur.description]
# #             lines     = [dict(zip(line_cols, r)) for r in line_rows]

# #     return {

# #         "success": True,

# #         "data": {

# #             "rfq_id":         rfq_id,
# #             "rfq_case_id":    rfq_case_id,
# #             "document_title": header["NAME"],

# #             "issue_date":   format_ist_date_only(header["CREATEDDATETIME"]),
# #             "closing_date": format_ist_date_only(header["EXPIRYDATETIME"]),
# #             "delivery_date": format_ist_date_only(header["DELIVERYDATE"]),

# #             "currency":           header["CURRENCYCODE"],
# #             "termsandconditions": header["HIQ_TERMSANDCONDITIONS"],

# #             "line_items": [
# #                 {
# #                     "line_num":   int(float(line["LINENUM"])),
# #                     "item_id":    line["ITEMID"],
# #                     "item_name":  line["NAME"],
# #                     "quantity":   float(line["PURCHQTY"]   or 0),
# #                     "uom":        line["PURCHUNIT"],
# #                     "unit_price": float(line["PURCHPRICE"] or 0),
# #                     "net_amount": float(line["LINEAMOUNT"] or 0),
# #                     "hiq_decision": (
# #                         "Accepted"
# #                         if line["STATUS"] == 4
# #                         else "Rejected"
# #                     )
# #                 }
# #                 for line in lines
# #             ]
# #         }
# #     }


# # # ============================================================
# # # RFQ HISTORY DETAIL
# # # ============================================================
# # def get_rfq_history_detail_sync(
# #     rfq_id: str,
# #     vendor_account: str,
# #     status: str
# # ):

# #     status = (status or "").lower()

# #     if status == "completed":

# #         result = fetch_completed_rfq_detail_sync(
# #             rfq_id,
# #             vendor_account
# #         )

# #     elif status == "expired":

# #         result = fetch_rfq_detail_sync(
# #             rfq_id,
# #             vendor_account
# #         )

# #     else:

# #         return {
# #             "success": False,
# #             "message": "Invalid status. Use Completed or Expired."
# #         }

# #     if not result.get("success"):
# #         return result

# #     vendor_profile = fetch_vendor_profile_sync(vendor_account)

# #     return {

# #         "success": True,

# #         "type": status.capitalize(),

# #         "data": {

# #             **result.get("data"),

# #             "vendor_information": {
# #                 "vendor_account": vendor_account,
# #                 "vendor_name":    vendor_profile.get("name")    or "-",
# #                 "email":          vendor_profile.get("email")   or "-",
# #                 "phone":          vendor_profile.get("phone")   or "-",
# #                 "address":        vendor_profile.get("address") or "-",
# #                 "city":           vendor_profile.get("city")    or "-"
# #             }
# #         }
# #     }


# # async def get_rfq_history_detail(
# #     rfq_id: str,
# #     vendor_account: str,
# #     status: str
# # ):
# #     return await run_in_threadpool(
# #         get_rfq_history_detail_sync,
# #         rfq_id,
# #         vendor_account,
# #         status
# #     )

# # # import json

# # # from typing import (
# # #     List,
# # #     Dict,
# # #     Any
# # # )

# # # from fastapi.concurrency import (
# # #     run_in_threadpool
# # # )

# # # from app.db.base import (
# # #     get_connection,
# # #     get_d365_connection
# # # )

# # # from app.core.config import settings

# # # from app.utils.date_utils import (
# # #     format_ist_date_only
# # # )

# # # from app.utils.remainingdate import (
# # #     calculate_days_left
# # # )


# # # SCHEMA = settings.DB_SCHEMA

# # # RFQ_REPLIES_TABLE = (
# # #     f"{SCHEMA}.HIQ_VENDORRFQREPLIES"
# # # )


# # # # ============================================================
# # # # HELPERS
# # # # ============================================================
# # # def normalize(val):
# # #     return str(val or "").strip().upper()


# # # # ============================================================
# # # # VENDOR PROFILE
# # # # ============================================================
# # # def fetch_vendor_profile_sync(
# # #     vendor_account: str
# # # ):

# # #     profile = {

# # #         "email": None,

# # #         "phone": None,

# # #         "address": None,

# # #         "name": None,

# # #         "city": None
# # #     }

# # #     with get_d365_connection() as conn:

# # #         cursor = conn.cursor()

# # #         # ====================================================
# # #         # EMAIL + PHONE
# # #         # ====================================================
# # #         cursor.execute("""
# # #             SELECT
# # #                 TYPE,
# # #                 LOCATOR

# # #             FROM HIQ_vendorELECTRONICADDRESSVIEW
# # #             WITH (NOLOCK)

# # #             WHERE ACCOUNTNUM = ?
# # #               AND ISPRIMARY1 = 1
# # #         """, vendor_account)

# # #         for row in cursor.fetchall():

# # #             if row.TYPE == 2:
# # #                 profile["email"] = row.LOCATOR

# # #             elif row.TYPE == 1:
# # #                 profile["phone"] = row.LOCATOR

# # #         # ====================================================
# # #         # ADDRESS
# # #         # ====================================================
# # #         cursor.execute("""
# # #             SELECT TOP 1
# # #                 ADDRESS,
# # #                 NAME,
# # #                 CITY

# # #             FROM HIQ_vendorPostalADDRESSVIEW
# # #             WITH (NOLOCK)

# # #             WHERE ACCOUNTNUM = ?
# # #               AND ISPRIMARY = 1
# # #         """, vendor_account)

# # #         row = cursor.fetchone()

# # #         if row:

# # #             profile["address"] = row.ADDRESS

# # #             profile["name"] = row.NAME

# # #             profile["city"] = row.CITY

# # #     return profile


# # # async def fetch_vendor_profile(
# # #     vendor_account: str
# # # ):
# # #     return await run_in_threadpool(
# # #         fetch_vendor_profile_sync,
# # #         vendor_account
# # #     )


# # # # ============================================================
# # # # APPROVED ITEMS
# # # # ============================================================
# # # def _get_approved_items(
# # #     vendor_account: str
# # # ) -> List[str]:

# # #     try:

# # #         with get_d365_connection() as conn:

# # #             cur = conn.cursor()

# # #             cur.execute("""
# # #                 SELECT ITEMID

# # #                 FROM PDSAPPROVEDVENDORLIST
# # #                 WITH (NOLOCK)

# # #                 WHERE
# # #                     PDSAPPROVEDVENDOR = ?

# # #                   AND DATAAREAID = 'hi-q'

# # #                   AND VALIDFROM
# # #                         <= GETUTCDATE()

# # #                   AND VALIDTO
# # #                         >= GETUTCDATE()
# # #             """, (vendor_account,))

# # #             return [
# # #                 str(r[0]).strip()
# # #                 for r in cur.fetchall()
# # #             ]

# # #     except Exception as e:

# # #         print(
# # #             f"[APPROVED VENDOR] "
# # #             f"D365 fetch error "
# # #             f"for {vendor_account}: {e}"
# # #         )

# # #         return []


# # # # ============================================================
# # # # RFQ HISTORY
# # # # ============================================================
# # # def get_rfq_history_sync(
# # #     vendor_account: str
# # # ):

# # #     result = []

# # #     # ========================================================
# # #     # STEP 1 - PORTAL RFQS
# # #     # ========================================================
# # #     with get_connection() as conn:

# # #         cur = conn.cursor()

# # #         cur.execute(f"""
# # #             SELECT
# # #                 RFQCASEID,
# # #                 RFQID,
# # #                 SUBMISSIONSTATUS,
# # #                 DRAFTLINECOUNT,
# # #                 SENDTOD365AT

# # #             FROM {RFQ_REPLIES_TABLE}
# # #             WITH (NOLOCK)

# # #             WHERE VENDORACCOUNT = ?
# # #         """, (vendor_account,))

# # #         portal_rows = cur.fetchall()

# # #     portal_map = {}

# # #     for row in portal_rows:

# # #         rfq_id = normalize(row[1])

# # #         portal_map[rfq_id] = {

# # #             "case_id":
# # #                 row[0],

# # #             "submission_status":
# # #                 row[2],

# # #             "draft_line_count":
# # #                 row[3],

# # #             "submitted_on":
# # #                 row[4]
# # #         }

# # #     # ========================================================
# # #     # STEP 2 - D365 RFQS
# # #     # ========================================================
# # #     with get_d365_connection() as conn:

# # #         cur = conn.cursor()

# # #         cur.execute("""
# # #             SELECT
# # #                 L.RFQCASEID,
# # #                 T.RFQID,
# # #                 L.CREATEDDATETIME,
# # #                 L.EXPIRYDATETIME,
# # #                 L.DELIVERYDATE,
# # #                 PM.NAME,
# # #                 PT.DESCRIPTION,
# # #                 DM.TXT,
# # #                 DT.TXT

# # #             FROM PurchRFQCaseTable L
# # #             WITH (NOLOCK)

# # #             INNER JOIN PurchRFQTable T
# # #             WITH (NOLOCK)

# # #                 ON T.RFQCASEID = L.RFQCASEID
# # #                AND T.VENDACCOUNT = ?

# # #             LEFT JOIN VENDPAYMMODETABLE PM
# # #                 ON L.PAYMMODE = PM.PAYMMODE

# # #             LEFT JOIN PAYMTERM PT
# # #                 ON L.PAYMENT = PT.PAYMTERMID

# # #             LEFT JOIN DLVMODE DM
# # #                 ON L.DLVMODE = DM.CODE

# # #             LEFT JOIN DLVTERM DT
# # #                 ON L.DLVTERM = DT.CODE

# # #             ORDER BY L.EXPIRYDATETIME DESC
# # #         """, (vendor_account,))

# # #         rows = cur.fetchall()

# # #         cols = [
# # #             c[0]
# # #             for c in cur.description
# # #         ]

# # #     for row in rows:

# # #         data = dict(zip(cols, row))

# # #         rfq_id = normalize(data["RFQID"])

# # #         portal = portal_map.get(rfq_id)

# # #         # ====================================================
# # #         # STATUS
# # #         # ====================================================
# # #         status = "Expired"

# # #         expired_status = "Not Opened"

# # #         if portal:

# # #             if (
# # #                 portal["submission_status"] == 1
# # #             ):

# # #                 # ============================================
# # #                 # CHECK COMPLETED
# # #                 # ============================================
# # #                 with get_d365_connection() as conn:

# # #                     cur = conn.cursor()

# # #                     cur.execute("""
# # #                         SELECT TOP 1 1

# # #                         FROM PURCHRFQREPLYLINE RL
# # #                         WITH (NOLOCK)

# # #                         INNER JOIN PURCHRFQLINE PL
# # #                         WITH (NOLOCK)

# # #                             ON PL.RECID
# # #                                = RL.RFQLINERECID

# # #                            AND PL.DATAAREAID = 'hi-q'

# # #                         WHERE RL.RFQID = ?
# # #                           AND RL.DATAAREAID = 'hi-q'
# # #                           AND PL.STATUS >= 3
# # #                     """, (rfq_id,))

# # #                     completed = cur.fetchone()

# # #                 if completed:

# # #                     status = "Completed"

# # #                 else:

# # #                     expired_status = "Drafted"

# # #         result.append({

# # #             "rfq_no":
# # #                 data["RFQID"],

# # #             "case_id":
# # #                 data["RFQCASEID"],

# # #             "created_date":
# # #                 format_ist_date_only(
# # #                     data["CREATEDDATETIME"]
# # #                 ),

# # #             "expiry_date":
# # #                 format_ist_date_only(
# # #                     data["EXPIRYDATETIME"]
# # #                 ),

# # #             "delivery_date":
# # #                 format_ist_date_only(
# # #                     data["DELIVERYDATE"]
# # #                 ),

# # #             "mode_of_delivery":
# # #                 data["TXT"] or "-",

# # #             "delivery_term":
# # #                 data["TXT"] or "-",

# # #             "payment_term":
# # #                 data["DESCRIPTION"] or "-",

# # #             "payment_mode":
# # #                 data["NAME"] or "-",

# # #             "expired_status":
# # #                 expired_status,

# # #             "status":
# # #                 status
# # #         })

# # #     return {

# # #         "status": "success",

# # #         "count": len(result),

# # #         "data": result
# # #     }


# # # async def get_rfq_history(
# # #     vendor_account: str
# # # ):
# # #     return await run_in_threadpool(
# # #         get_rfq_history_sync,
# # #         vendor_account
# # #     )

# # # # ============================================================
# # # # EXPIRED RFQ DETAIL
# # # # ============================================================
# # # def fetch_rfq_detail_sync(
# # #     rfq_id: str,
# # #     vendor_account: str
# # # ) -> Dict[str, Any]:

# # #     # ========================================================
# # #     # APPROVED ITEMS
# # #     # ========================================================
# # #     approved_items = _get_approved_items(
# # #         vendor_account
# # #     )

# # #     # ========================================================
# # #     # D365 HEADER + LINE DATA
# # #     # ========================================================
# # #     with get_d365_connection() as conn:

# # #         cursor = conn.cursor()

# # #         # ====================================================
# # #         # HEADER
# # #         # ====================================================
# # #         cursor.execute("""
# # #             SELECT TOP 1

# # #                 L.RFQCASEID,

# # #                 T.RFQID,

# # #                 L.NAME AS DOCUMENT_TITLE,

# # #                 L.EXPIRYDATETIME AS CLOSING_DATE,

# # #                 L.CREATEDDATETIME AS ISSUE_DATE,

# # #                 L.DELIVERYDATE AS EXPECTED_DELIVERY_DATE,

# # #                 PT.DESCRIPTION AS PAYMENT_TERM,

# # #                 PM.NAME AS METHOD_OF_PAYMENT,

# # #                 DM.TXT AS MODE_OF_DELIVERY,

# # #                 DT.TXT AS DELIVERY_TERM,

# # #                 T.HIQ_TERMSANDCONDITIONS,

# # #                 T.CURRENCYCODE

# # #             FROM PurchRFQCaseTable L
# # #             WITH (NOLOCK)

# # #             INNER JOIN PurchRFQTable T
# # #             WITH (NOLOCK)

# # #                 ON T.RFQCASEID = L.RFQCASEID
# # #                AND T.VENDACCOUNT = ?

# # #             LEFT JOIN PAYMTERM PT
# # #                 ON L.PAYMENT = PT.PAYMTERMID

# # #             LEFT JOIN VENDPAYMMODETABLE PM
# # #                 ON L.PAYMMODE = PM.PAYMMODE

# # #             LEFT JOIN DLVMODE DM
# # #                 ON L.DLVMODE = DM.CODE

# # #             LEFT JOIN DLVTERM DT
# # #                 ON L.DLVTERM = DT.CODE

# # #             WHERE T.RFQID = ?
# # #         """, (
# # #             vendor_account,
# # #             rfq_id
# # #         ))

# # #         row = cursor.fetchone()

# # #         if not row:

# # #             return {
# # #                 "success": False,
# # #                 "message": "RFQ not found"
# # #             }

# # #         cols = [
# # #             c[0]
# # #             for c in cursor.description
# # #         ]

# # #         header = dict(zip(cols, row))

# # #         # ====================================================
# # #         # LINE ITEMS
# # #         # ====================================================
# # #         lines = []

# # #         if approved_items:

# # #             placeholders = ",".join(
# # #                 ["?" for _ in approved_items]
# # #             )

# # #             cursor.execute(f"""
# # #                 SELECT

# # #                     RL.LINENUM,

# # #                     RL.ITEMID AS MATERIAL_CODE,

# # #                     IT.NAMEALIAS AS MATERIAL_DESCRIPTION,

# # #                     RL.QTYORDERED AS QUANTITY,

# # #                     RL.PURCHUNIT AS UOM,

# # #                     RL.HIQ_TARGETPRICE AS TARGETPRICE,

# # #                     RL.HIQ_COMMENTS AS COMMENTS,

# # #                     RL.CURRENCYCODE,

# # #                     RL.DELIVERYDATE AS LINE_DELIVERY_DATE,

# # #                     RPL.DELIVERYDATE
# # #                         AS VENDORREPLY_DELIVERY_DATE

# # #                 FROM PurchRFQLine RL
# # #                 WITH (NOLOCK)

# # #                 LEFT JOIN PURCHRFQREPLYLINE RPL
# # #                 WITH (NOLOCK)

# # #                     ON RPL.RFQLINERECID = RL.RECID
# # #                    AND RPL.DATAAREAID = 'hi-q'

# # #                 LEFT JOIN INVENTTABLE IT
# # #                 WITH (NOLOCK)

# # #                     ON IT.ITEMID = RL.ITEMID

# # #                 INNER JOIN PDSAPPROVEDVENDORLIST AVL
# # #                 WITH (NOLOCK)

# # #                     ON AVL.ITEMID = RL.ITEMID
# # #                    AND AVL.PDSAPPROVEDVENDOR = ?
# # #                    AND AVL.VALIDFROM <= GETUTCDATE()
# # #                    AND AVL.VALIDTO >= GETUTCDATE()

# # #                 WHERE RL.RFQID = ?
# # #                   AND RL.ITEMID IN ({placeholders})

# # #                 ORDER BY RL.LINENUM
# # #             """, (
# # #                 [vendor_account, rfq_id]
# # #                 + approved_items
# # #             ))

# # #             line_rows = cursor.fetchall()

# # #             line_cols = [
# # #                 c[0]
# # #                 for c in cursor.description
# # #             ]

# # #             lines = [
# # #                 dict(zip(line_cols, r))
# # #                 for r in line_rows
# # #             ]

# # #     # ========================================================
# # #     # PORTAL DRAFT DATA
# # #     # ========================================================
# # #     saved_price_map = {}

# # #     saved_header = {}

# # #     with get_connection() as conn:

# # #         cursor = conn.cursor()

# # #         cursor.execute(f"""
# # #             SELECT TOP 1
# # #                 PAYLOADJSON

# # #             FROM {RFQ_REPLIES_TABLE}
# # #             WITH (NOLOCK)

# # #             WHERE RFQID = ?
# # #               AND VENDORACCOUNT = ?

# # #             ORDER BY ID DESC
# # #         """, (
# # #             rfq_id,
# # #             vendor_account
# # #         ))

# # #         draft_row = cursor.fetchone()

# # #     # ========================================================
# # #     # DRAFT PARSE
# # #     # ========================================================
# # #     if draft_row and draft_row[0]:

# # #         try:

# # #             payload = json.loads(
# # #                 draft_row[0]
# # #             )

# # #             saved_header = {

# # #                 "modeOfDelivery":
# # #                     payload.get(
# # #                         "modeOfDelivery", ""
# # #                     ),

# # #                 "DeliveryTerms":
# # #                     payload.get(
# # #                         "DeliveryTerms", ""
# # #                     ),

# # #                 "methodOfPayment":
# # #                     payload.get(
# # #                         "methodOfPayment", ""
# # #                     ),

# # #                 "termsOfPayment":
# # #                     payload.get(
# # #                         "termsOfPayment", ""
# # #                     ),

# # #                 "replyDeliveryDate":
# # #                     payload.get(
# # #                         "replyDeliveryDate", ""
# # #                     ),

# # #                 "replyDeliveryTerms":
# # #                     payload.get(
# # #                         "replyDeliveryTerms", ""
# # #                     ),

# # #                 "replyModeOfDelivery":
# # #                     payload.get(
# # #                         "replyModeOfDelivery", ""
# # #                     ),

# # #                 "vendorComments":
# # #                     payload.get(
# # #                         "vendorComments", ""
# # #                     )
# # #             }

# # #             for item in payload.get("Item", []):

# # #                 item_number = item.get(
# # #                     "itemNumber"
# # #                 )

# # #                 if item_number:

# # #                     saved_price_map[
# # #                         item_number
# # #                     ] = {

# # #                         "unit_price":
# # #                             float(
# # #                                 item.get(
# # #                                     "unitPrice"
# # #                                 ) or 0
# # #                             ),

# # #                         "net_amount":
# # #                             float(
# # #                                 item.get(
# # #                                     "netAmount"
# # #                                 ) or 0
# # #                             ),

# # #                         "vendor_comments":
# # #                             item.get(
# # #                                 "vendorComments", ""
# # #                             ),

# # #                         "line_status":
# # #                             item.get(
# # #                                 "lineStatus",
# # #                                 False
# # #                             )
# # #                     }

# # #         except Exception as e:

# # #             print(
# # #                 f"[DRAFT PARSE ERROR] {e}"
# # #             )

# # #     # ========================================================
# # #     # BUILD LINE ITEMS
# # #     # ========================================================
# # #     line_items = []

# # #     for item in lines:

# # #         material_code = item[
# # #             "MATERIAL_CODE"
# # #         ]

# # #         saved = saved_price_map.get(
# # #             material_code,
# # #             {}
# # #         )

# # #         if saved.get(
# # #             "line_status",
# # #             False
# # #         ):
# # #             continue

# # #         line_items.append({

# # #             "line_num":
# # #                 int(item["LINENUM"]),

# # #             "item_name":
# # #                 item[
# # #                     "MATERIAL_DESCRIPTION"
# # #                 ],

# # #             "item_id":
# # #                 material_code,

# # #             "quantity":
# # #                 item["QUANTITY"],

# # #             "uom":
# # #                 item["UOM"],

# # #             "target_price":
# # #                 round(
# # #                     float(
# # #                         item[
# # #                             "TARGETPRICE"
# # #                         ] or 0
# # #                     ),
# # #                     2
# # #                 ),

# # #             "currency":
# # #                 item["CURRENCYCODE"],

# # #             "comments":
# # #                 item["COMMENTS"],

# # #             "unit_price":
# # #                 saved.get(
# # #                     "unit_price", ""
# # #                 ),

# # #             "net_amount":
# # #                 saved.get(
# # #                     "net_amount", ""
# # #                 ),

# # #             "vendor_comments":
# # #                 saved.get(
# # #                     "vendor_comments", ""
# # #                 ),

# # #             "rfq_delivery_date":
# # #                 format_ist_date_only(
# # #                     item.get(
# # #                         "LINE_DELIVERY_DATE"
# # #                     )
# # #                 ),

# # #             "vendor_delivery_date":
# # #                 format_ist_date_only(
# # #                     item.get(
# # #                         "VENDORREPLY_DELIVERY_DATE"
# # #                     )
# # #                 ),

# # #             "hiq_decision":
# # #                 "Expired"
# # #         })

# # #     # ========================================================
# # #     # FINAL RESPONSE
# # #     # ========================================================
# # #     return {

# # #         "success": True,

# # #         "has_draft":
# # #             bool(saved_price_map),

# # #         "data": {

# # #             "rfq_case_id":
# # #                 header["RFQCASEID"],

# # #             "rfq_id":
# # #                 header["RFQID"],

# # #             "document_title":
# # #                 header["DOCUMENT_TITLE"],

# # #             "issue_date":
# # #                 format_ist_date_only(
# # #                     header["ISSUE_DATE"]
# # #                 ),

# # #             "closing_date":
# # #                 format_ist_date_only(
# # #                     header["CLOSING_DATE"]
# # #                 ),

# # #             "time_remaining":
# # #                 calculate_days_left(
# # #                     header["CLOSING_DATE"]
# # #                 ),

# # #             "delivery_date":
# # #                 format_ist_date_only(
# # #                     header[
# # #                         "EXPECTED_DELIVERY_DATE"
# # #                     ]
# # #                 ),

# # #             "payment_term":
# # #                 header["PAYMENT_TERM"]
# # #                 or "-",

# # #             "payment_mode":
# # #                 header[
# # #                     "METHOD_OF_PAYMENT"
# # #                 ] or "-",

# # #             "delivery_term":
# # #                 header["DELIVERY_TERM"]
# # #                 or "-",

# # #             "delivery_mode":
# # #                 header[
# # #                     "MODE_OF_DELIVERY"
# # #                 ] or "-",

# # #             "termsandconditions":
# # #                 header[
# # #                     "HIQ_TERMSANDCONDITIONS"
# # #                 ],

# # #             "currency":
# # #                 header["CURRENCYCODE"],

# # #             "saved_mode_of_delivery":
# # #                 saved_header.get(
# # #                     "modeOfDelivery", ""
# # #                 ),

# # #             "saved_delivery_terms":
# # #                 saved_header.get(
# # #                     "DeliveryTerms", ""
# # #                 ),

# # #             "saved_method_of_payment":
# # #                 saved_header.get(
# # #                     "methodOfPayment", ""
# # #                 ),

# # #             "saved_terms_of_payment":
# # #                 saved_header.get(
# # #                     "termsOfPayment", ""
# # #                 ),

# # #             "reply_delivery_date":
# # #                 saved_header.get(
# # #                     "replyDeliveryDate", ""
# # #                 ),

# # #             "reply_delivery_mode":
# # #                 saved_header.get(
# # #                     "replyModeOfDelivery", ""
# # #                 ),

# # #             "reply_delivery_term":
# # #                 saved_header.get(
# # #                     "replyDeliveryTerms", ""
# # #                 ),

# # #             "saved_vendor_comments":
# # #                 saved_header.get(
# # #                     "vendorComments", ""
# # #                 ),

# # #             "line_items":
# # #                 line_items
# # #         }
# # #     }
# # # # ============================================================
# # # # COMPLETED RFQ DETAIL
# # # # ============================================================
# # # def fetch_completed_rfq_detail_sync(
# # #     rfq_id: str,
# # #     vendor_account: str
# # # ) -> Dict[str, Any]:

# # #     approved_items = _get_approved_items(
# # #         vendor_account
# # #     )

# # #     # ========================================================
# # #     # PORTAL DATA
# # #     # ========================================================
# # #     with get_connection() as conn:

# # #         cur = conn.cursor()

# # #         cur.execute(f"""
# # #             SELECT TOP 1
# # #                 RFQCASEID

# # #             FROM {RFQ_REPLIES_TABLE}
# # #             WITH (NOLOCK)

# # #             WHERE RFQID = ?
# # #               AND VENDORACCOUNT = ?
# # #         """, (
# # #             rfq_id,
# # #             vendor_account
# # #         ))

# # #         portal_row = cur.fetchone()

# # #     if not portal_row:

# # #         return {
# # #             "success": False,
# # #             "message": "RFQ not found"
# # #         }

# # #     rfq_case_id = portal_row[0]

# # #     # ========================================================
# # #     # D365 HEADER
# # #     # ========================================================
# # #     with get_d365_connection() as conn:

# # #         cur = conn.cursor()

# # #         cur.execute("""
# # #             SELECT TOP 1

# # #                 RT.RFQID,

# # #                 RT.CURRENCYCODE,

# # #                 RT.DELIVERYDATE,

# # #                 RT.DLVMODE,

# # #                 RT.DLVTERM,

# # #                 RT.PAYMENT,

# # #                 RT.VENDREF,

# # #                 RT.TOTALSCORE,

# # #                 RT.RANK,

# # #                 RT.VALIDFROM,

# # #                 RT.VALIDTO,

# # #                 RT.VALIDITYDATESTART,

# # #                 RT.VALIDITYDATEEND,

# # #                 RT.REPLYPROGRESSSTATUS,

# # #                 RT.HIQ_COMMENTS,

# # #                 L.EXPIRYDATETIME,

# # #                 L.CREATEDDATETIME,

# # #                 L.DELIVERYDATE,

# # #                 L.NAME,

# # #                 PM.NAME,

# # #                 PT.DESCRIPTION,

# # #                 DM.TXT,

# # #                 DT.TXT,

# # #                 T.HIQ_TERMSANDCONDITIONS

# # #             FROM PURCHRFQREPLYTABLE RT
# # #             WITH (NOLOCK)

# # #             LEFT JOIN PurchRFQCaseTable L
# # #             WITH (NOLOCK)

# # #                 ON L.RFQCASEID = ?

# # #             LEFT JOIN PurchRFQTable T
# # #             WITH (NOLOCK)

# # #                 ON T.RFQCASEID = L.RFQCASEID
# # #                AND T.VENDACCOUNT = ?

# # #             LEFT JOIN VENDPAYMMODETABLE PM
# # #                 ON L.PAYMMODE = PM.PAYMMODE

# # #             LEFT JOIN PAYMTERM PT
# # #                 ON L.PAYMENT = PT.PAYMTERMID

# # #             LEFT JOIN DLVMODE DM
# # #                 ON L.DLVMODE = DM.CODE

# # #             LEFT JOIN DLVTERM DT
# # #                 ON L.DLVTERM = DT.CODE

# # #             WHERE RT.RFQID = ?
# # #               AND RT.DATAAREAID = 'hi-q'
# # #         """, (
# # #             rfq_case_id,
# # #             vendor_account,
# # #             rfq_id
# # #         ))

# # #         row = cur.fetchone()

# # #         if not row:

# # #             return {
# # #                 "success": False,
# # #                 "message": "RFQ not found"
# # #             }

# # #         cols = [
# # #             c[0]
# # #             for c in cur.description
# # #         ]

# # #         header = dict(zip(cols, row))

# # #         lines = []

# # #         if approved_items:

# # #             placeholders = ",".join(
# # #                 ["?" for _ in approved_items]
# # #             )

# # #             cur.execute(f"""
# # #                 SELECT

# # #                     RL.LINENUM,

# # #                     RL.NAME,

# # #                     RL.PURCHQTY,

# # #                     RL.PURCHUNIT,

# # #                     RL.PURCHPRICE,

# # #                     RL.LINEAMOUNT,

# # #                     RL.LINEDISC,

# # #                     RL.LINEPERCENT,

# # #                     RL.DELIVERYDATE,

# # #                     RL.LEADTIME,

# # #                     RL.HIQ_COMMENTS,

# # #                     RL.VALIDFROM,

# # #                     RL.VALIDTO,

# # #                     RL.EXTERNALITEMID,

# # #                     RL.MAXIMUMRETAILPRICE_IN,

# # #                     PL.HIQ_TARGETPRICE,

# # #                     PL.HIQ_COMMENTS,

# # #                     PL.ITEMID,

# # #                     PL.STATUS,

# # #                     PL.CURRENCYCODE,

# # #                     PL.PURCHID,

# # #                     PL.DELIVERYDATE,

# # #                     RL.DELIVERYDATE

# # #                 FROM PURCHRFQREPLYLINE RL
# # #                 WITH (NOLOCK)

# # #                 INNER JOIN PURCHRFQLINE PL
# # #                 WITH (NOLOCK)

# # #                     ON PL.RECID
# # #                        = RL.RFQLINERECID

# # #                    AND PL.DATAAREAID = 'hi-q'

# # #                 WHERE RL.RFQID = ?
# # #                   AND RL.DATAAREAID = 'hi-q'
# # #                   AND PL.STATUS >= 3
# # #                   AND PL.ITEMID IN ({placeholders})

# # #                 ORDER BY RL.LINENUM
# # #             """, [rfq_id] + approved_items)

# # #             line_rows = cur.fetchall()

# # #             line_cols = [
# # #                 c[0]
# # #                 for c in cur.description
# # #             ]

# # #             lines = [
# # #                 dict(zip(line_cols, r))
# # #                 for r in line_rows
# # #             ]

# # #     return {

# # #         "success": True,

# # #         "data": {

# # #             "rfq_id":
# # #                 rfq_id,

# # #             "rfq_case_id":
# # #                 rfq_case_id,

# # #             "document_title":
# # #                 header["NAME"],

# # #             "issue_date":
# # #                 format_ist_date_only(
# # #                     header["CREATEDDATETIME"]
# # #                 ),

# # #             "closing_date":
# # #                 format_ist_date_only(
# # #                     header["EXPIRYDATETIME"]
# # #                 ),

# # #             "delivery_date":
# # #                 format_ist_date_only(
# # #                     header["DELIVERYDATE"]
# # #                 ),

# # #             "currency":
# # #                 header["CURRENCYCODE"],

# # #             "termsandconditions":
# # #                 header["HIQ_TERMSANDCONDITIONS"],

# # #             "line_items": [

# # #                 {

# # #                     "line_num":
# # #                         int(float(line["LINENUM"])),

# # #                     "item_id":
# # #                         line["ITEMID"],

# # #                     "item_name":
# # #                         line["NAME"],

# # #                     "quantity":
# # #                         float(line["PURCHQTY"] or 0),

# # #                     "uom":
# # #                         line["PURCHUNIT"],

# # #                     "unit_price":
# # #                         float(line["PURCHPRICE"] or 0),

# # #                     "net_amount":
# # #                         float(line["LINEAMOUNT"] or 0),

# # #                     "hiq_decision":
# # #                         "Accepted"
# # #                         if line["STATUS"] == 4
# # #                         else "Rejected"

# # #                 }

# # #                 for line in lines
# # #             ]
# # #         }
# # #     }


# # # # ============================================================
# # # # RFQ HISTORY DETAIL
# # # # ============================================================
# # # def get_rfq_history_detail_sync(
# # #     rfq_id: str,
# # #     vendor_account: str,
# # #     status: str
# # # ):

# # #     status = (
# # #         status or ""
# # #     ).lower()

# # #     if status == "completed":

# # #         result = fetch_completed_rfq_detail_sync(
# # #             rfq_id,
# # #             vendor_account
# # #         )

# # #     elif status == "expired":

# # #         result = fetch_rfq_detail_sync(
# # #             rfq_id,
# # #             vendor_account
# # #         )

# # #     else:

# # #         return {

# # #             "success": False,

# # #             "message":
# # #                 "Invalid status"
# # #         }

# # #     if not result.get("success"):
# # #         return result

# # #     vendor_profile = fetch_vendor_profile_sync(
# # #         vendor_account
# # #     )

# # #     return {

# # #         "success": True,

# # #         "type":
# # #             status.capitalize(),

# # #         "data": {

# # #             **result.get("data"),

# # #             "vendor_information": {

# # #                 "vendor_account":
# # #                     vendor_account,

# # #                 "vendor_name":
# # #                     vendor_profile.get("name") or "-",

# # #                 "email":
# # #                     vendor_profile.get("email") or "-",

# # #                 "phone":
# # #                     vendor_profile.get("phone") or "-",

# # #                 "address":
# # #                     vendor_profile.get("address") or "-",

# # #                 "city":
# # #                     vendor_profile.get("city") or "-"
# # #             }
# # #         }
# # #     }

# # # async def get_rfq_history_detail(
# # #     rfq_id: str,
# # #     vendor_account: str,
# # #     status: str
# # # ):
# # #     return await run_in_threadpool(
# # #         get_rfq_history_detail_sync,
# # #         rfq_id,
# # #         vendor_account,
# # #         status
# # #     )



# # # # from app.db.base import get_connection
# # # # from app.utils.date_utils import format_ist_date_only
# # # # import json
# # # # from app.utils.remainingdate import calculate_days_left
# # # # from typing import List, Dict, Any
# # # # from app.db.base import get_connection
# # # # from app.utils.date_utils import format_ist_date_only
# # # # from typing import List, Dict, Any
# # # # from fastapi.concurrency import run_in_threadpool
# # # # def get_rfq_history_sync(vendor_account: str):

# # # #     result = []

# # # #     with get_connection() as conn:
# # # #         cur = conn.cursor()

# # # #         # =========================
# # # #         # COMPLETED RFQs
# # # #         # Taken from fetch_completed_rfqs
# # # #         # =========================
# # # #         cur.execute("""
# # # #             SELECT DISTINCT
# # # #                 R.RFQ_CASE_ID,
# # # #                 R.RFQ_ID,
# # # #                 L.CREATEDDATETIME           AS ISSUE_DATE,
# # # #                 L.EXPIRYDATETIME            AS CLOSING_DATE,
# # # #                 L.DELIVERYDATE              AS EXPECTED_DELIVERY_DATE,
# # # #                 MAX(R.SEND_TO_D365_AT)      AS SUBMITTED_ON,
# # # #                 PM.NAME                     AS PAYMENT_MODE,
# # # #                 PT.DESCRIPTION              AS PAYMENT_TERM,
# # # #                 DM.TXT                      AS DELIVERY_MODE,
# # # #                 DT.TXT                      AS DELIVERY_TERM

# # # #             FROM HIQ_VENDORRFQREPLIES R WITH (NOLOCK)

# # # #             LEFT JOIN PurchRFQCaseTable L WITH (NOLOCK)
# # # #                 ON L.RFQCASEID = R.RFQ_CASE_ID

# # # #             LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# # # #                 ON L.PAYMMODE = PM.PAYMMODE

# # # #             LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# # # #                 ON L.PAYMENT = PT.PAYMTERMID

# # # #             LEFT JOIN DLVMODE DM WITH (NOLOCK)
# # # #                 ON L.DLVMODE = DM.CODE

# # # #             LEFT JOIN DLVTERM DT WITH (NOLOCK)
# # # #                 ON L.DLVTERM = DT.CODE

# # # #             WHERE R.VENDOR_ACCOUNT    = ?
# # # #               AND R.SUBMISSION_STATUS = 1

# # # #               AND EXISTS (
# # # #                   SELECT 1
# # # #                   FROM PURCHRFQREPLYLINE RL2 WITH (NOLOCK)
# # # #                   INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
# # # #                       ON  PL.RECID      = RL2.RFQLINERECID
# # # #                       AND PL.DATAAREAID = 'hi-q'
# # # #                   WHERE RL2.RFQID      = R.RFQ_ID
# # # #                     AND RL2.DATAAREAID = 'hi-q'
# # # #                     AND PL.STATUS      >= 3
# # # #               )

# # # #             GROUP BY
# # # #                 R.RFQ_CASE_ID,
# # # #                 R.RFQ_ID,
# # # #                 L.CREATEDDATETIME,
# # # #                 L.EXPIRYDATETIME,
# # # #                 L.DELIVERYDATE,
# # # #                 PM.NAME,
# # # #                 PT.DESCRIPTION,
# # # #                 DM.TXT,
# # # #                 DT.TXT

# # # #             ORDER BY MAX(R.SEND_TO_D365_AT) DESC
# # # #         """, (vendor_account,))

# # # #         completed_rows = cur.fetchall()
# # # #         completed_cols = [c[0] for c in cur.description]

# # # #         for row in completed_rows:
# # # #             data = dict(zip(completed_cols, row))
# # # #             result.append({
# # # #                 "rfq_no":           data["RFQ_ID"],
# # # #                 "case_id":          data["RFQ_CASE_ID"],
# # # #                 "created_date":     format_ist_date_only(data["ISSUE_DATE"]),
# # # #                 "expiry_date":      format_ist_date_only(data["CLOSING_DATE"]),
# # # #                 "delivery_date":    format_ist_date_only(data["EXPECTED_DELIVERY_DATE"]),
# # # #                 "mode_of_delivery": data["DELIVERY_MODE"]  or "-",
# # # #                 "delivery_term":    data["DELIVERY_TERM"]  or "-",
# # # #                 "payment_term":     data["PAYMENT_TERM"]   or "-",
# # # #                 "payment_mode":     data["PAYMENT_MODE"]   or "-",
# # # #                 "status":           "Completed"
# # # #             })

# # # #         # =========================
# # # #         # EXPIRED RFQs
# # # #         # Taken from fetch_vendor_expired_rfqs
# # # #         # =========================
# # # #         cur.execute("""
# # # #             SELECT
# # # #                 L.RFQCASEID,
# # # #                 T.RFQID,
# # # #                 L.CREATEDDATETIME   AS ISSUE_DATE,
# # # #                 L.EXPIRYDATETIME,
# # # #                 L.DELIVERYDATE,
# # # #                 PT.DESCRIPTION      AS PAYMENT_TERM,
# # # #                 PM.NAME             AS PAYMENT_MODE,
# # # #                 DM.TXT              AS DELIVERY_MODE,
# # # #                 DT.TXT              AS DELIVERY_TERM,
# # # #                 CASE
# # # #                     WHEN NOT EXISTS (
# # # #                         SELECT 1
# # # #                         FROM HIQ_VendorRFQReplies R
# # # #                         WHERE R.RFQ_CASE_ID    = L.RFQCASEID
# # # #                           AND R.VENDOR_ACCOUNT = T.VENDACCOUNT
# # # #                     ) THEN 'Not Opened'

# # # #                     WHEN EXISTS (
# # # #                         SELECT 1
# # # #                         FROM HIQ_VendorRFQReplies R
# # # #                         WHERE R.RFQ_CASE_ID        = L.RFQCASEID
# # # #                           AND R.VENDOR_ACCOUNT     = T.VENDACCOUNT
# # # #                           AND R.SUBMISSION_STATUS  = 1
# # # #                           AND R.DRAFTLINECOUNT     > 0
# # # #                     ) THEN 'Drafted'

# # # #                     ELSE 'Not Opened'
# # # #                 END AS EXPIRED_STATUS

# # # #             FROM PurchRFQCaseTable L WITH (NOLOCK)

# # # #             INNER JOIN PurchRFQTable T WITH (NOLOCK)
# # # #                 ON  T.RFQCASEID   = L.RFQCASEID
# # # #                 AND T.VENDACCOUNT = ?

# # # #             LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# # # #                 ON L.PAYMENT = PT.PAYMTERMID

# # # #             LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# # # #                 ON L.PAYMMODE = PM.PAYMMODE

# # # #             LEFT JOIN DLVMODE DM WITH (NOLOCK)
# # # #                 ON L.DLVMODE = DM.CODE

# # # #             LEFT JOIN DLVTERM DT WITH (NOLOCK)
# # # #                 ON L.DLVTERM = DT.CODE

# # # #             WHERE L.EXPIRYDATETIME < GETUTCDATE()

# # # #             AND (
# # # #                 NOT EXISTS (
# # # #                     SELECT 1
# # # #                     FROM HIQ_VendorRFQReplies R WITH (NOLOCK)
# # # #                     WHERE R.RFQ_CASE_ID    = L.RFQCASEID
# # # #                       AND R.VENDOR_ACCOUNT = T.VENDACCOUNT
# # # #                 )
# # # #                 OR
# # # #                 EXISTS (
# # # #                     SELECT 1
# # # #                     FROM HIQ_VendorRFQReplies R
# # # #                     WHERE R.RFQ_CASE_ID        = L.RFQCASEID
# # # #                       AND R.VENDOR_ACCOUNT     = T.VENDACCOUNT
# # # #                       AND R.SUBMISSION_STATUS  = 1
# # # #                       AND R.DRAFTLINECOUNT     > 0
# # # #                 )
# # # #             )

# # # #             AND EXISTS (
# # # #                 SELECT 1
# # # #                 FROM PurchRFQCaseLine CL WITH (NOLOCK)
# # # #                 INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
# # # #                     ON  AVL.ITEMID            = CL.ITEMID
# # # #                     AND AVL.PDSAPPROVEDVENDOR = T.VENDACCOUNT
# # # #                     AND AVL.DATAAREAID        = L.DATAAREAID
# # # #                 WHERE CL.RFQCASEID = L.RFQCASEID
# # # #             )

# # # #             ORDER BY L.EXPIRYDATETIME DESC
# # # #         """, (vendor_account,))

# # # #         expired_rows = cur.fetchall()
# # # #         expired_cols = [c[0] for c in cur.description]

# # # #         for row in expired_rows:
# # # #             data = dict(zip(expired_cols, row))
# # # #             result.append({
# # # #                 "rfq_no":           data["RFQID"],
# # # #                 "case_id":          data["RFQCASEID"],
# # # #                 "created_date":     format_ist_date_only(data["ISSUE_DATE"]),
# # # #                 "expiry_date":      format_ist_date_only(data["EXPIRYDATETIME"]),
# # # #                 "delivery_date":    format_ist_date_only(data["DELIVERYDATE"]),
# # # #                 "mode_of_delivery": data["DELIVERY_MODE"]    or "-",
# # # #                 "delivery_term":    data["DELIVERY_TERM"]    or "-",
# # # #                 "payment_term":     data["PAYMENT_TERM"]     or "-",
# # # #                 "payment_mode":     data["PAYMENT_MODE"]     or "-",
# # # #                 "expired_status":   data["EXPIRED_STATUS"],   # Not Opened / Drafted
# # # #                 "status":           "Expired"
# # # #             })

# # # #     return {
# # # #         "status": "success",
# # # #         "count":  len(result),
# # # #         "data":   result
# # # #     }
# # # # async def get_rfq_history(vendor_account: str):
# # # #     return await run_in_threadpool(get_rfq_history_sync, vendor_account)    

# # # # def fetch_vendor_profile_sync(vendor_account: str):

# # # #     profile = {
# # # #         "email": None,
# # # #         "phone": None,
# # # #         "address": None,
# # # #         "name":None,
# # # #         "city":None
# # # #     }

# # # #     with get_connection() as conn:
# # # #         cursor = conn.cursor()

# # # #         # 🔹 Fetch Email + Phone
# # # #         electronic_query = """
# # # #             SELECT TYPE, LOCATOR
# # # #             FROM HIQ_vendorELECTRONICADDRESSVIEW WITH (NOLOCK)
# # # #             WHERE ACCOUNTNUM = ?
# # # #             AND ISPRIMARY1 = 1
# # # #         """

# # # #         cursor.execute(electronic_query, vendor_account)
# # # #         electronic_rows = cursor.fetchall()

# # # #         for row in electronic_rows:
# # # #             type = row.TYPE
# # # #             locator = row.LOCATOR
 
# # # #             if type == 2:
# # # #                 profile["email"] = locator
# # # #             elif type == 1:
# # # #                 profile["phone"] = locator

# # # #         # 🔹 Fetch Address
# # # #         address_query = """
# # # #             SELECT TOP 1 ADDRESS,NAME,CITY
# # # #             FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)
# # # #             WHERE ACCOUNTNUM = ?
# # # #             AND ISPRIMARY = 1
# # # #         """

# # # #         cursor.execute(address_query, vendor_account)
# # # #         address_row = cursor.fetchone()

# # # #         if address_row:
# # # #             profile["address"] = address_row.ADDRESS
# # # #             profile["name"]=address_row.NAME
# # # #             profile["city"] = address_row.CITY

# # # #         cursor.close()

# # # #     return profile 
# # # # async def fetch_vendor_profile(vendor_account: str):
# # # #     return await run_in_threadpool(fetch_vendor_profile_sync, vendor_account)

# # # # def _get_approved_items(vendor_account: str) -> List[str]:
# # # #     """
# # # #     Fetch approved item IDs for a vendor from D365.
# # # #     PDSAPPROVEDVENDORLIST lives in AxDb — must use get_d365_connection().
# # # #     """
# # # #     try:
# # # #         with get_connection() as conn:
# # # #             cur = conn.cursor()
# # # #             cur.execute("""
# # # #                 SELECT ITEMID
# # # #                 FROM PDSAPPROVEDVENDORLIST WITH (NOLOCK)
# # # #                 WHERE PDSAPPROVEDVENDOR = ?
# # # #                   AND DATAAREAID        = 'hi-q'
# # # #                   AND VALIDFROM        <= GETUTCDATE()
# # # #                   AND VALIDTO          >= GETUTCDATE()
# # # #             """, (vendor_account,))
# # # #             return [str(r[0]).strip() for r in cur.fetchall()]
# # # #     except Exception as e:
# # # #         print(f"[APPROVED VENDOR] D365 fetch error for {vendor_account}: {e}")
# # # #         return []


# # # # def fetch_completed_rfq_detail_sync(rfq_id: str, vendor_account: str) -> Dict[str, Any]:

# # # #     # Step 1 — get approved items from D365
# # # #     approved_items = _get_approved_items(vendor_account)

# # # #     with get_connection() as conn:
# # # #         cur = conn.cursor()

# # # #         # Step 2 — Header
# # # #         cur.execute("""
# # # #             SELECT TOP 1
# # # #                 RT.RFQID,
# # # #                 RT.CURRENCYCODE,
# # # #                 RT.DELIVERYDATE         AS REPLY_DELIVERY_DATE,
# # # #                 RT.DLVMODE              AS REPLY_DELIVERY_MODE,
# # # #                 RT.DLVTERM              AS REPLY_DELIVERY_TERM,
# # # #                 RT.PAYMENT              AS REPLY_PAYMENT_TERM,
# # # #                 RT.VENDREF,
# # # #                 RT.TOTALSCORE,
# # # #                 RT.RANK,
# # # #                 RT.VALIDFROM,
# # # #                 RT.VALIDTO,
# # # #                 RT.VALIDITYDATESTART,
# # # #                 RT.VALIDITYDATEEND,
# # # #                 RT.REPLYPROGRESSSTATUS,
# # # #                 RT.HIQ_COMMENTS         AS REMARKS,
# # # #                 R.RFQ_CASE_ID,
# # # #                 L.EXPIRYDATETIME        AS CLOSING_DATE,
# # # #                 L.CREATEDDATETIME       AS ISSUE_DATE,
# # # #                 L.DELIVERYDATE          AS EXPECTED_DELIVERY_DATE,
# # # #                 L.NAME                  AS DOCUMENT_TITLE,
# # # #                 PM.NAME                 AS PAYMENT_MODE,
# # # #                 PT.DESCRIPTION          AS PAYMENT_TERM,
# # # #                 DM.TXT                  AS DELIVERY_MODE,
# # # #                 DT.TXT                  AS DELIVERY_TERM,
# # # #                 T.HIQ_TERMSANDCONDITIONS
                
# # # #             FROM PURCHRFQREPLYTABLE RT WITH (NOLOCK)

# # # #             INNER JOIN HIQ_VENDORRFQREPLIES R WITH (NOLOCK)
# # # #                 ON  R.RFQ_ID         = RT.RFQID
# # # #                 AND R.VENDOR_ACCOUNT = ?

# # # #             LEFT JOIN PurchRFQCaseTable L WITH (NOLOCK)
# # # #                 ON  L.RFQCASEID  = R.RFQ_CASE_ID
# # # #                 AND L.DATAAREAID = 'hi-q'
# # # #             LEFT JOIN PurchRFQTable T WITH (NOLOCK)   -- ✅ ADD THIS JOIN
# # # #                 ON  T.RFQCASEID   = L.RFQCASEID
# # # #                 AND T.VENDACCOUNT = R.VENDOR_ACCOUNT
# # # #                 AND T.DATAAREAID  = 'hi-q'

# # # #             LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# # # #                 ON L.PAYMMODE = PM.PAYMMODE

# # # #             LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# # # #                 ON L.PAYMENT = PT.PAYMTERMID

# # # #             LEFT JOIN DLVMODE DM WITH (NOLOCK)
# # # #                 ON L.DLVMODE = DM.CODE

# # # #             LEFT JOIN DLVTERM DT WITH (NOLOCK)
# # # #                 ON L.DLVTERM = DT.CODE

# # # #             WHERE RT.RFQID      = ?
# # # #               AND RT.DATAAREAID = 'hi-q'
# # # #         """, (vendor_account, rfq_id))

# # # #         row = cur.fetchone()
# # # #         if not row:
# # # #             return {"success": False, "message": "RFQ not found"}

# # # #         cols   = [c[0] for c in cur.description]
# # # #         header = dict(zip(cols, row))

# # # #         # Step 3 — Lines filtered by approved items
# # # #         lines = []
# # # #         if approved_items:
# # # #             placeholders = ",".join(["?" for _ in approved_items])
# # # #             cur.execute(f"""
# # # #                 SELECT
# # # #                     RL.LINENUM,
# # # #                     RL.NAME                     AS ITEM_NAME,
# # # #                     RL.PURCHQTY                 AS QUANTITY,
# # # #                     RL.PURCHUNIT                AS UOM,
# # # #                     RL.PURCHPRICE               AS UNIT_PRICE,
# # # #                     RL.LINEAMOUNT               AS NET_AMOUNT,
# # # #                     RL.LINEDISC                 AS LINE_DISC,
# # # #                     RL.LINEPERCENT              AS LINE_PERCENT,
# # # #                     RL.DELIVERYDATE             AS DELIVERY_DATE,
# # # #                     RL.LEADTIME,
# # # #                     RL.HIQ_COMMENTS             AS VENDOR_COMMENTS,
# # # #                     RL.VALIDFROM                AS LINE_VALID_FROM,
# # # #                     RL.VALIDTO                  AS LINE_VALID_TO,
# # # #                     RL.EXTERNALITEMID,
# # # #                     RL.MAXIMUMRETAILPRICE_IN    AS MRP,
# # # #                     PL.HIQ_TARGETPRICE          AS TARGETPRICE,
# # # #                     PL.HIQ_COMMENTS             AS COMMENTS,
# # # #                     PL.ITEMID,
# # # #                     PL.STATUS,
# # # #                     PL.CURRENCYCODE,
# # # #                     PL.PURCHID,
# # # #                     PL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
# # # #                     RL.DELIVERYDATE AS VENDORREPLY_DELIVERY_DATE,
# # # #                     CASE PL.STATUS
# # # #                         WHEN 3 THEN 'Rejected'
# # # #                         WHEN 4 THEN 'Accepted'
# # # #                         WHEN 5 THEN 'Caceled'
# # # #                         WHEN 6 THEN 'Declined'
# # # #                         ELSE        'Under Review'
# # # #                     END                         AS HIQ_DECISION

# # # #                 FROM PURCHRFQREPLYLINE RL WITH (NOLOCK)

# # # #                 INNER JOIN PURCHRFQLINE PL WITH (NOLOCK)
# # # #                     ON  PL.RECID      = RL.RFQLINERECID
# # # #                     AND PL.DATAAREAID = 'hi-q'

# # # #                 WHERE RL.RFQID      = ?
# # # #                   AND RL.DATAAREAID = 'hi-q'
# # # #                   AND PL.STATUS     >= 3
# # # #                   AND PL.ITEMID     IN ({placeholders})

# # # #                 ORDER BY RL.LINENUM
# # # #             """, [rfq_id] + approved_items)

# # # #             line_rows = cur.fetchall()
# # # #             line_cols = [c[0] for c in cur.description]
# # # #             lines     = [dict(zip(line_cols, r)) for r in line_rows]

# # # #     return {
# # # #         "success": True,
# # # #         "data": {
# # # #             "rfq_id":                   rfq_id,
# # # #             "rfq_case_id":              header["RFQ_CASE_ID"],
# # # #             "delivery_date":            format_ist_date_only(header["EXPECTED_DELIVERY_DATE"]),
# # # #             "payment_term":             header["PAYMENT_TERM"]          or "-",
# # # #             "delivery_term":            header["DELIVERY_TERM"]         or "-",
# # # #             "delivery_mode":            header["DELIVERY_MODE"]         or "-",
# # # #             "payment_mode":             header["PAYMENT_MODE"]          or "-",
# # # #             "issue_date":               format_ist_date_only(header["ISSUE_DATE"]),
# # # #             "closing_date":             format_ist_date_only(header["CLOSING_DATE"]),
# # # #             "document_title":           header["DOCUMENT_TITLE"],
# # # #             "currency":                 header["CURRENCYCODE"]          or "INR",
# # # #             "reply_delivery_date":      format_ist_date_only(header["REPLY_DELIVERY_DATE"]),
# # # #             "reply_delivery_mode":      header["REPLY_DELIVERY_MODE"]   or "-",
# # # #             "reply_delivery_term":      header["REPLY_DELIVERY_TERM"]   or "-",
# # # #             "reply_payment_term":       header["REPLY_PAYMENT_TERM"]    or "-",
# # # #             "vendor_ref":               header["VENDREF"]               or "-",
# # # #             "valid_from":               format_ist_date_only(header["VALIDFROM"]),
# # # #             "valid_to":                 format_ist_date_only(header["VALIDTO"]),
# # # #             "validity_date_start":      format_ist_date_only(header["VALIDITYDATESTART"]),
# # # #             "validity_date_end":        format_ist_date_only(header["VALIDITYDATEEND"]),
# # # #             "total_score":              header["TOTALSCORE"]            or 0,
# # # #             "rank":                     header["RANK"]                  or 0,
# # # #             "reply_progress_status":    header["REPLYPROGRESSSTATUS"]   or 0,
# # # #             "remarks":                  header["REMARKS"]               or " ",
# # # #             "termsandconditions":     header["HIQ_TERMSANDCONDITIONS"],
# # # #             "line_items": [
# # # #                 {
# # # #                     "line_num":         int(float(line["LINENUM"])),
# # # #                     "item_id":          line["ITEMID"]               or "-",
# # # #                     "item_name":        line["ITEM_NAME"]            or "-",
# # # #                     "external_item_id": line["EXTERNALITEMID"]       or "-",
# # # #                     "quantity":         float(line["QUANTITY"]       or 0),
# # # #                     "uom":              line["UOM"]                  or "-",
# # # #                     "unit_price":       float(line["UNIT_PRICE"]     or 0),
# # # #                     "net_amount":       float(line["NET_AMOUNT"]     or 0),
# # # #                     "line_disc":        float(line["LINE_DISC"]      or 0),
# # # #                     "line_percent":     float(line["LINE_PERCENT"]   or 0),
# # # #                     "mrp":              float(line["MRP"]            or 0),
# # # #                     "delivery_date":    format_ist_date_only(line["DELIVERY_DATE"]),
# # # #                     "lead_time":        line["LEADTIME"]             or 0,
# # # #                     "vendor_comments":  line["VENDOR_COMMENTS"]      or " ",
# # # #                     "currency":         line["CURRENCYCODE"]       or "INR",
# # # #                     "line_valid_from":  format_ist_date_only(line["LINE_VALID_FROM"]),
# # # #                     "line_valid_to":    format_ist_date_only(line["LINE_VALID_TO"]),
# # # #                     "hiq_decision":     line["HIQ_DECISION"],
# # # #                     "target_price":     round(float(line['TARGETPRICE'] or 0), 2),
# # # #                     # "target_price":     f"{round(float(line['TARGETPRICE'] or 0), 2)} {line['CURRENCYCODE']}",
# # # #                     "comments":         line["COMMENTS"] or " ",
# # # #                     "purchid":line["PURCHID"],
# # # #                     "rfq_delivery_date": format_ist_date_only(line.get("LINE_DELIVERY_DATE")),
# # # #                     "vendor_delivery_date": format_ist_date_only(line.get("VENDORREPLY_DELIVERY_DATE"))
# # # #                 }
# # # #                 for line in lines
# # # #             ]
# # # #         }
# # # #     }

# # # # def fetch_rfq_detail_sync(rfq_id: str, vendor_account: str):

# # # #     header_query = """
# # # #         SELECT
# # # #             L.RFQCASEID,
# # # #             T.RFQID,
# # # #             L.NAME              AS DOCUMENT_TITLE,
# # # #             L.EXPIRYDATETIME    AS CLOSING_DATE,
# # # #             L.CREATEDDATETIME   AS ISSUE_DATE,
# # # #             L.DELIVERYDATE      AS EXPECTED_DELIVERY_DATE,
# # # #             PT.DESCRIPTION      AS PAYMENT_TERM,
# # # #             PM.NAME             AS METHOD_OF_PAYMENT,
# # # #             DM.TXT              AS MODE_OF_DELIVERY,
# # # #             DT.TXT              AS DELIVERY_TERM,
# # # #             T.HIQ_TERMSANDCONDITIONS,
# # # #             T.CURRENCYCODE

# # # #         FROM PurchRFQCaseTable L WITH (NOLOCK)

# # # #         INNER JOIN PurchRFQTable T WITH (NOLOCK)
# # # #             ON  T.RFQCASEID   = L.RFQCASEID
# # # #             AND T.VENDACCOUNT = ?

# # # #         LEFT JOIN PAYMTERM PT WITH (NOLOCK)
# # # #             ON L.PAYMENT = PT.PAYMTERMID

# # # #         LEFT JOIN VENDPAYMMODETABLE PM WITH (NOLOCK)
# # # #             ON L.PAYMMODE = PM.PAYMMODE

# # # #         LEFT JOIN DLVMODE DM WITH (NOLOCK)
# # # #             ON L.DLVMODE = DM.CODE

# # # #         LEFT JOIN DLVTERM DT WITH (NOLOCK)
# # # #             ON L.DLVTERM = DT.CODE

# # # #         WHERE T.RFQID = ?
# # # #     """

# # # #     lines_query = """
# # # #         SELECT
# # # #             RL.LINENUM,
# # # #             RL.ITEMID           AS MATERIAL_CODE,
# # # #             IT.NAMEALIAS        AS MATERIAL_DESCRIPTION,
# # # #             RL.QTYORDERED       AS QUANTITY,
# # # #             RL.PURCHUNIT        AS UOM,
# # # #             RL.HIQ_TARGETPRICE  AS TARGETPRICE,
# # # #             RL.HIQ_COMMENTS     AS COMMENTS,
# # # #             RL.CURRENCYCODE,
# # # #             RL.DELIVERYDATE     AS LINE_DELIVERY_DATE,
# # # #             RPL.DELIVERYDATE    AS VENDORREPLY_DELIVERY_DATE   

# # # #         FROM PurchRFQLine RL WITH (NOLOCK)
# # # #         LEFT JOIN PURCHRFQREPLYLINE RPL WITH (NOLOCK)
# # # #             ON  RPL.RFQLINERECID = RL.RECID
# # # #             AND RPL.DATAAREAID   = 'hi-q'

# # # #         LEFT JOIN INVENTTABLE IT WITH (NOLOCK)
# # # #             ON IT.ITEMID = RL.ITEMID

# # # #         INNER JOIN PDSAPPROVEDVENDORLIST AVL WITH (NOLOCK)
# # # #             ON  AVL.ITEMID            = RL.ITEMID
# # # #             AND AVL.PDSAPPROVEDVENDOR = ?
# # # #             AND AVL.VALIDFROM        <= GETUTCDATE()
# # # #             AND AVL.VALIDTO          >= GETUTCDATE()

# # # #         WHERE RL.RFQID = ?
# # # #         ORDER BY RL.LINENUM
# # # #     """

# # # #     draft_query = """
# # # #         SELECT TOP 1 PAYLOAD_JSON
# # # #         FROM HIQ_VENDORRFQREPLIES WITH (NOLOCK)
# # # #         WHERE RFQ_ID         = ?
# # # #           AND VENDOR_ACCOUNT = ?
# # # #           --AND SUBMISSION_STATUS = 0
# # # #         ORDER BY ID DESC
# # # #     """

# # # #     with get_connection() as conn:
# # # #         cursor = conn.cursor()

# # # #         cursor.execute(header_query, (vendor_account, rfq_id))
# # # #         row = cursor.fetchone()
# # # #         if not row:
# # # #             return {"success": False, "message": "RFQ not found"}

# # # #         cols   = [c[0] for c in cursor.description]
# # # #         header = dict(zip(cols, row))

# # # #         cursor.execute(lines_query, (vendor_account, rfq_id))
# # # #         line_rows = cursor.fetchall()
# # # #         line_cols = [c[0] for c in cursor.description]
# # # #         lines     = [dict(zip(line_cols, r)) for r in line_rows]

# # # #         cursor.execute(draft_query, (rfq_id, vendor_account))
# # # #         draft_row = cursor.fetchone()

# # # #     saved_price_map = {}
# # # #     saved_header    = {}

# # # #     if draft_row and draft_row[0]:
# # # #         try:
# # # #             payload = json.loads(draft_row[0])

# # # #             saved_header = {
# # # #                 "modeOfDelivery":      payload.get("modeOfDelivery", ""),
# # # #                 "DeliveryTerms":       payload.get("DeliveryTerms", ""),
# # # #                 "methodOfPayment":     payload.get("methodOfPayment", ""),
# # # #                 "termsOfPayment":      payload.get("termsOfPayment", ""),
# # # #                 "replyDeliveryDate":   payload.get("replyDeliveryDate", ""),
# # # #                 "replyDeliveryTerms":  payload.get("replyDeliveryTerms", ""),   
# # # #                 "replyModeOfDelivery": payload.get("replyModeOfDelivery", ""),  
# # # #                 "vendorComments":      payload.get("vendorComments", ""),
# # # #             }

# # # #             for item in payload.get("Item", []):
# # # #                 item_number = item.get("itemNumber")
# # # #                 if item_number:
# # # #                     val = item.get("unitPrice")
# # # #                     saved_price_map[item_number] = {
# # # #                         "unit_price":float(val) if val not in [None, ""] else "",
# # # #                         "net_amount": float(item.get("netAmount")) if item.get("netAmount") not in [None, ""] else "", 
# # # #                         "vendor_comments": item.get("vendorComments", ""),
# # # #                         "line_status": item.get("lineStatus", False),
    
# # # #                     }
# # # #         except Exception:
# # # #             pass

# # # #     line_items = []
# # # #     for item in lines:
# # # #         material_code = item["MATERIAL_CODE"]
# # # #         saved         = saved_price_map.get(material_code, {})
# # # #         if saved.get("line_status", False):
# # # #             continue
# # # #         line_items.append({
# # # #             "line_num":                int(item["LINENUM"]),
# # # #             "item_name": item["MATERIAL_DESCRIPTION"],
# # # #             "item_id":        material_code,
# # # #             "quantity":             item["QUANTITY"],
# # # #             "uom":                  item["UOM"],
# # # #             "target_price":     round(float(item['TARGETPRICE'] or 0), 2),
# # # #             # "target_price":    f"{round(float(item['TARGETPRICE'] or 0), 2)} {item['CURRENCYCODE']}",
# # # #             "currency":         item["CURRENCYCODE"],
# # # #             "comments":             item["COMMENTS"],
# # # #             "unit_price": saved.get("unit_price", ""),
# # # #             "net_amount": saved.get("net_amount", ""),
# # # #             # "unit_price":           saved.get("unit_price", None),
# # # #             "vendor_comments":              saved.get("vendor_comments", ""),
# # # #             "rfq_delivery_date": format_ist_date_only(item.get("LINE_DELIVERY_DATE")),
# # # #             "vendor_delivery_date": format_ist_date_only(item.get("VENDORREPLY_DELIVERY_DATE")),
# # # #             "hiq_decision":"Expired",
# # # #         })

# # # #     return {
# # # #         "success": True,
# # # #         "has_draft": bool(saved_price_map),
# # # #         "data": {
# # # #             "rfq_case_id":            header["RFQCASEID"],
# # # #             "rfq_id":                 header["RFQID"],
# # # #             "document_title":         header["DOCUMENT_TITLE"],
# # # #             "issue_date":             format_ist_date_only(header["ISSUE_DATE"]),
# # # #             "closing_date":           format_ist_date_only(header["CLOSING_DATE"]),
# # # #             "time_remaining":         calculate_days_left(header["CLOSING_DATE"]),
# # # #             "delivery_date": format_ist_date_only(header["EXPECTED_DELIVERY_DATE"]),
# # # #             "payment_term":           header["PAYMENT_TERM"]      or "-",
# # # #             "payment_mode":      header["METHOD_OF_PAYMENT"] or "-",
# # # #             "delivery_term":          header["DELIVERY_TERM"]      or "-",
# # # #             "delivery_mode":       header["MODE_OF_DELIVERY"]   or "-",
# # # #             "termsandconditions":     header["HIQ_TERMSANDCONDITIONS"],
# # # #             "currency":header["CURRENCYCODE"],

# # # #             # ── Saved draft fields (all 8) ─────────────────────
# # # #             "saved_mode_of_delivery":       saved_header.get("modeOfDelivery", ""),
# # # #             "saved_delivery_terms":         saved_header.get("DeliveryTerms", ""),
# # # #             "saved_method_of_payment":      saved_header.get("methodOfPayment", ""),
# # # #             "saved_terms_of_payment":       saved_header.get("termsOfPayment", ""),
# # # #             "reply_delivery_date":    saved_header.get("replyDeliveryDate", ""),
# # # #             "reply_delivery_mode": saved_header.get("replyModeOfDelivery", ""),   
# # # #             "reply_delivery_term": saved_header.get("replyDeliveryTerms", ""),
# # # #             "saved_vendor_comments":        saved_header.get("vendorComments", ""),

# # # #             "line_items": line_items
# # # #         }
# # # #     }

# # # # def get_rfq_history_detail_sync(rfq_id: str, vendor_account: str, status: str):

# # # #     # =========================
# # # #     # VALIDATION
# # # #     # =========================
# # # #     if not rfq_id or not vendor_account:
# # # #         return {
# # # #             "success": False,
# # # #             "message": "rfq_id and vendor_account required"
# # # #         }

# # # #     # =========================
# # # #     # ROUTING BASED ON STATUS
# # # #     # =========================
# # # #     status = (status or "").lower()

# # # #     if status == "completed":
# # # #         result = fetch_completed_rfq_detail_sync(rfq_id, vendor_account)

# # # #     elif status == "expired":
# # # #         result = fetch_rfq_detail_sync(rfq_id, vendor_account)

# # # #     else:
# # # #         return {
# # # #             "success": False,
# # # #             "message": "Invalid status. Use Completed or Expired"
# # # #         }

# # # #     # =========================
# # # #     # SAFETY CHECK
# # # #     # =========================
# # # #     if not result.get("success"):
# # # #         return result

# # # #     # =========================
# # # #     # FETCH VENDOR PROFILE ✅
# # # #     # =========================
# # # #     vendor_profile = fetch_vendor_profile_sync(vendor_account)

# # # #     # =========================
# # # #     # FINAL RESPONSE ✅
# # # #     # =========================
# # # #     return {
# # # #         "success": True,
# # # #         "type": status.capitalize(),
# # # #         "data": {
# # # #             **result.get("data"),

# # # #             # 🔹 ADD VENDOR INFO HERE
# # # #             "vendor_information": {
# # # #                 "vendor_account": vendor_account,
# # # #                 "vendor_name": vendor_profile.get("name") or "-",
# # # #                 "email": vendor_profile.get("email") or "-",
# # # #                 "phone": vendor_profile.get("phone") or "-",
# # # #                 "address": vendor_profile.get("address") or "-",
# # # #                 "city":vendor_profile.get("city") or "-"  
# # # #             }
# # # #         }
# # # #     }
# # # # async def get_rfq_history_detail(rfq_id: str, vendor_account: str, status: str):
# # # #     return await run_in_threadpool(get_rfq_history_detail_sync, rfq_id,vendor_account,status)