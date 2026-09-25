
from app.db.base import get_d365_connection
from app.utils.date_utils import format_ist_date_only
from fastapi.concurrency import run_in_threadpool
def map_po_status(purch_status):
    if purch_status in (0, 1):
        return "Open"
    elif purch_status == 2:
        return "Received"
    elif purch_status == 3:
        return "Invoiced"
    elif purch_status == 4:
        return "Cancelled"
    return "Unknown"
def get_bidding_po_list_sync():
    with get_d365_connection() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT 
    P.PURCHID,
    R.RFQID,
    VP.NAME,
    ISNULL(LT.TOTAL_AMOUNT, 0) AS TOTAL_AMOUNT,
    P.CREATEDDATETIME,
    P.PURCHSTATUS,
    P.CURRENCYCODE

FROM PURCHTABLE P

-- ✅ TOTAL
LEFT JOIN (
    SELECT 
        PURCHID,
        SUM(LINEAMOUNT) AS TOTAL_AMOUNT
    FROM PURCHLINE
    GROUP BY PURCHID
) LT 
    ON LT.PURCHID = P.PURCHID

-- ✅ FIX 1: ONLY ONE RFQ PER PO
OUTER APPLY (
    SELECT TOP 1 RFQID
    FROM PURCHRFQLINE R
    WHERE R.PURCHID = P.PURCHID
      AND R.RFQID IS NOT NULL
) R

-- ✅ FIX 2: ONLY ONE VENDOR ROW
OUTER APPLY (
    SELECT TOP 1 NAME
    FROM HIQ_VENDORPOSTALADDRESSVIEW VP
    WHERE VP.ACCOUNTNUM = P.ORDERACCOUNT
      AND VP.ISPRIMARY = 1
) VP

WHERE P.DATAAREAID = 'hi-q'

-- ✅ ONLY BIDDING POs
AND R.RFQID IS NOT NULL

ORDER BY P.CREATEDDATETIME DESC
        """)

        rows = cur.fetchall()

        return [{
            "po_number": r[0],
            "rfq_id": r[1],
            "vendor_name": r[2],
            "total_amount": float(r[3] or 0),
            "created_date": format_ist_date_only(r[4]),
            "status": map_po_status(r[5]),
            "currency": r[6] 
        } for r in rows]
async def get_bidding_po_list():
    return await run_in_threadpool(get_bidding_po_list_sync)
def fetch_vendor_profile_sync(vendor_account: str):

    profile = {
        "email": None,
        "phone": None,
        "address": None,
        "name":None
    }

    with get_d365_connection() as conn:
        cursor = conn.cursor()

        # 🔹 Fetch Email + Phone
        electronic_query = """
            SELECT TYPE, LOCATOR
            FROM HIQ_vendorELECTRONICADDRESSVIEW WITH (NOLOCK)
            WHERE ACCOUNTNUM = ?
            AND ISPRIMARY1 = 1
        """

        cursor.execute(electronic_query, vendor_account)
        electronic_rows = cursor.fetchall()

        for row in electronic_rows:
            type = row.TYPE
            locator = row.LOCATOR
 
            if type == 2:
                profile["email"] = locator
            elif type == 1:
                profile["phone"] = locator

        # 🔹 Fetch Address
        address_query = """
            SELECT TOP 1 ADDRESS,NAME,city
            FROM HIQ_vendorPostalADDRESSVIEW WITH (NOLOCK)
            WHERE ACCOUNTNUM = ?
            AND ISPRIMARY = 1
        """

        cursor.execute(address_query, vendor_account)
        address_row = cursor.fetchone()

        if address_row:
            profile["address"] = address_row.ADDRESS
            profile["name"]=address_row.NAME
            profile["city"]=address_row.city
        cursor.close()

    return profile 
async def fetch_vendor_profile(vendor_account: str):
    return await run_in_threadpool(fetch_vendor_profile_sync, vendor_account)
def get_bidding_po_details_sync(purch_id: str):

    with get_d365_connection() as conn:
        cur = conn.cursor()

        # =========================
        # HEADER (ONLY PO DATA)
        # =========================
        cur.execute("""
            SELECT TOP 1
                P.PURCHID,
                P.CREATEDDATETIME,
                P.PURCHSTATUS,
                P.ORDERACCOUNT,
                p.DLVTERM,
                p.DLVMODE,
                p.PAYMENT,
                p.PAYMMODE,
                p.DELIVERYDATE,
                P.CURRENCYCODE,   

                -- RFQ ID
                (
                    SELECT TOP 1 R.RFQID
                    FROM PURCHRFQLINE R
                    WHERE R.PURCHID = P.PURCHID
                ) AS RFQID,

                -- CONFIRMED DATE
                (
                    SELECT TOP 1 J.PURCHORDERDATE
                    FROM VENDPURCHORDERJOUR J
                    WHERE J.PURCHID = P.PURCHID
                    ORDER BY J.PURCHORDERDATE DESC
                ) AS CONFIRMEDDATE

            FROM PURCHTABLE P
            WHERE P.PURCHID = ?
        """, (purch_id,))

        header_row = cur.fetchone()

        if not header_row:
            return {}

        vendor_account = header_row[3]

        # =========================
        # USE YOUR FUNCTION HERE
        # =========================
        # profile = fetch_vendor_profile(vendor_account)
        profile = fetch_vendor_profile_sync(str(vendor_account).strip().upper()) or {}
      
        # =========================
        # LINE ITEMS
        # =========================
        cur.execute("""
SELECT
    L.LINENUMBER,
    L.ITEMID,
    L.NAME,
    PC.NAME AS PROCUREMENTCATEGORY,   -- ✅ NEW
    L.PURCHQTY,
    L.PURCHUNIT,
    L.PURCHPRICE,
    L.LINEAMOUNT,
    L.DELIVERYDATE

FROM PURCHLINE L

LEFT JOIN EcoResCategory PC
    ON PC.RECID = L.PROCUREMENTCATEGORY

WHERE L.PURCHID = ?
  AND L.ISDELETED = 0
                    """, (purch_id,))
        # cur.execute("""
        #     SELECT
        #         L.LINENUMBER,
        #         L.ITEMID,
        #         L.NAME,
        #         L.PURCHQTY,
        #         L.PURCHUNIT,
        #         L.PURCHPRICE,
        #         L.LINEAMOUNT,
        #         L.DELIVERYDATE

        #     FROM PURCHLINE L
        #     WHERE L.PURCHID = ?
        #       AND L.ISDELETED = 0
        # """, (purch_id,))

        lines = []
        for l in cur.fetchall():
            lines.append({
                "line_number": l[0],
                "item": l[1],
                "description": l[2] or " ",
                "procurement_category": l[3] or " ",   # ✅ NEW
                "quantity": float(l[4] or 0),
                "uom": l[5],
                "unit_price": float(l[6] or 0),
                "amount": float(l[7] or 0),
                "delivery_date": format_ist_date_only(l[8]) if l[8] else None
            })
        

        # =========================
        # INVOICE DETAILS
        # =========================
        cur.execute("""
            SELECT 
                INVOICEID,DUEDATE,INVOICEAMOUNT,INVOICEAMOUNTMST,CURRENCYCODE,INVOICEDATE
            FROM vendinvoicejour 
            WHERE purchid = ?
        """, (purch_id,))

        rows = cur.fetchall()
        invoices = []

        for inv in rows:
            invoices.append({
                "invoice_no": inv[0],
                "due_date": format_ist_date_only(inv[1]) if inv[1] else None,
                "invoice_amount_inr": float(inv[2] or 0),
                "invoice_amount": float(inv[3] or 0),
                "currency": inv[4],
                "invoice_date": format_ist_date_only(inv[5]) if inv[5] else None,
                })

        # =========================
        # FINAL RESPONSE
        # =========================
        return {
                        "header": {
                "po_number": header_row[0],
                "rfq_number": header_row[9],  # ✅ FIXED
                "status": map_po_status(header_row[2]),
                "issue_date": format_ist_date_only(header_row[1]),
                "confirmed_date": format_ist_date_only(header_row[10]),  # ✅ FIXED
                "delivery_terms": header_row[4],
                "delivery_mode": header_row[5],
                "payment_terms": header_row[6],
                "payment_mode": header_row[7],
                "delivery_date": format_ist_date_only(header_row[8]),
                "currency":header_row[9]
            },
            # "header": {
            #     "po_number": header_row[0],
            #     "rfq_number": header_row[4],
            #     "status": map_po_status(header_row[2]),
            #     "issue_date": format_ist_date_only(header_row[1]),
            #     "confirmed_date": format_ist_date_only(header_row[5]),
            #     "delivery_terms": header_row[6],
            #     "delivery_mode": header_row[7],
            #     "payment_terms": header_row[8],
            #     "payment_mode": header_row[9],
            #     "delivery_date":format_ist_date_only(header_row[10])
            # },

            # USING YOUR PROFILE FUNCTION
            # "vendor": {
            #     "vendor_account": vendor_account,
            #     "name": profile["name"],
            #     "email": profile["email"],
            #     "phone": profile["phone"],
            #     "address": profile["address"]
            # },
            "vendor": {
                "vendor_account": vendor_account,
                "name": profile.get("name") or vendor_account,
                "email": profile.get("email"),
                "phone": profile.get("phone"),
                "address": profile.get("address")
            },

            "lines": lines,
            "invoice":invoices
            
        }
    

async def get_bidding_po_details(purch_id:str):
    return await run_in_threadpool(get_bidding_po_details_sync,purch_id)
    
def get_invoice_lines_sync(invoice_id: str):
    with get_d365_connection() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT 
                VIT.PURCHID,
                VIT.InvoiceId,
                VIT.LineNum,
                VIT.ItemId,
                PC.NAME AS ProcurementCategory,
                VIT.NAME AS Description,
                VIT.QTY,
                VIT.PURCHPRICE AS UnitPrice,
                VIT.PURCHUNIT AS UOM,
                VIT.LineAmount,
                ISNULL(SUM(TT.TaxAmountCur), 0) AS LineTaxAmount

            FROM VendInvoiceTrans VIT

            LEFT JOIN TaxTrans TT
                ON TT.SourceRecId = VIT.RecId
                AND TT.SourceTableId = (
                    SELECT TOP 1 TableId 
                    FROM SQLDICTIONARY 
                    WHERE NAME = 'VendInvoiceTrans' 
                      AND FIELDID = 0
                )

            LEFT JOIN EcoResCategory PC 
                ON PC.RECID = VIT.PROCUREMENTCATEGORY

            WHERE VIT.INVOICEID = ?

            GROUP BY 
                VIT.PURCHID,
                VIT.InvoiceId,
                VIT.LineNum,
                VIT.ItemId,
                PC.NAME,
                VIT.NAME,
                VIT.QTY,
                VIT.PURCHPRICE,
                VIT.PURCHUNIT,
                VIT.LineAmount

            ORDER BY VIT.LineNum
        """, (invoice_id,))

        rows = cur.fetchall()

        return [
            {
                "purch_id": r[0],
                "invoice_id": r[1],
                "line_num": r[2],
                "item_id": r[3],
                "procurement_category": r[4],
                "description": r[5],
                "quantity": float(r[6]) if r[6] else 0,
                "unit_price": float(r[7]) if r[7] else 0,
                "uom": r[8],
                "line_amount": float(r[9]) if r[9] else 0,
                "tax_amount": float(r[10]) if r[10] else 0
            }
            for r in rows
        ]
    

async def get_invoice_lines(invoice_id:str):
    return await run_in_threadpool(get_invoice_lines_sync,invoice_id)