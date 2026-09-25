from datetime import datetime
from app.db.base import get_connection, get_d365_connection
from app.core.config import settings
from fastapi.concurrency import run_in_threadpool

SCHEMA = settings.DB_SCHEMA

# ── Schema-prefixed Vendor Portal tables ──────────────────
VP_USER_TABLE    = f"{SCHEMA}.HIQ_VENDORPORTALUSER"
VP_REPLIES_TABLE = f"{SCHEMA}.HIQ_VENDORRFQREPLIES"

MONTH_MAP = {
    1: "Jan",  2: "Feb",  3: "Mar",  4: "Apr",
    5: "May",  6: "Jun",  7: "Jul",  8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
}


def get_dashboard_sync(year: int):

    # ══════════════════════════════════════════════════════
    # BLOCK 1 — VENDOR PORTAL DB
    # get_connection()
    # Tables: HIQ_VENDORPORTALUSER, HIQ_VENDORRFQREPLIES
    # ══════════════════════════════════════════════════════

    active_vendors  = 0
    pending_vendors = 0
    response_data   = {}
    action_required = []
    recent_activity = []

    with get_connection() as conn:
        cur = conn.cursor()

        # ── Active vendor count ───────────────────────────
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {VP_USER_TABLE} WITH (NOLOCK)
            WHERE STATUS    = 1
              AND ISCURRENT = 1
        """)
        active_vendors = cur.fetchone()[0]

        # ── Pending vendor count ──────────────────────────
        cur.execute(f"""
            SELECT COUNT(*)
            FROM {VP_USER_TABLE} WITH (NOLOCK)
            WHERE STATUS    = 0
              AND ISCURRENT = 1
        """)
        pending_vendors = cur.fetchone()[0]

        # ── Vendor responses per month (trend) ────────────
        cur.execute(f"""
            SELECT
                MONTH(CREATEDDATETIME),
                COUNT(DISTINCT RFQID)
            FROM {VP_REPLIES_TABLE} WITH (NOLOCK)
            WHERE YEAR(CREATEDDATETIME) = ?
              AND SUBMISSIONSTATUS      = 1
            GROUP BY MONTH(CREATEDDATETIME)
        """, year)
        response_data = dict(cur.fetchall())

        # ── Action required: vendors pending activation ───
        cur.execute(f"""
            SELECT TOP 10
                VENDORACCOUNT,
                EMAILADDRESS,
                CREATEDDATETIME
            FROM {VP_USER_TABLE} WITH (NOLOCK)
            WHERE STATUS    = 0
              AND ISCURRENT = 1
            ORDER BY CREATEDDATETIME DESC
        """)
        for row in cur.fetchall():
            action_required.append({
                "type":    "Vendor",
                "message": f"{row[0]} ({row[1]}) - Activation pending"
            })

        # ── Recent activity: vendor activations ──────────
        cur.execute(f"""
            SELECT TOP 5
                VENDORACCOUNT,
                MODIFIEDDATETIME
            FROM {VP_USER_TABLE} WITH (NOLOCK)
            WHERE STATUS             IN (1, 2)
              AND ISCURRENT           = 1
              AND MODIFIEDDATETIME IS NOT NULL
            ORDER BY MODIFIEDDATETIME DESC
        """)
        for row in cur.fetchall():
            dt = row[1]
            recent_activity.append({
                "message":  f"{row[0]} activated vendor portal",
                "time":     dt.strftime("%d %b %I:%M %p") if dt else "",
                "sort_key": dt
            })

    # ══════════════════════════════════════════════════════
    # BLOCK 2 — D365 DB
    # get_d365_connection()
    # Tables: VENDTABLE, PurchRFQCaseTable, PurchRFQTable,
    #         PURCHTABLE, PURCHRFQLINE
    # ══════════════════════════════════════════════════════

    total_vendors = 0
    open_cases    = 0
    open_pos      = 0
    rfq_data      = {}
    po_data       = {}

    with get_d365_connection() as conn:
        cur = conn.cursor()

        # ── Total vendors ─────────────────────────────────
        cur.execute("""
            SELECT COUNT(*)
            FROM VENDTABLE WITH (NOLOCK)
        """)
        total_vendors = cur.fetchone()[0]

        # ── Open RFQ cases ────────────────────────────────
        cur.execute("""
            SELECT COUNT(*)
            FROM PurchRFQCaseTable WITH (NOLOCK)
            WHERE EXPIRYDATETIME >= GETDATE()
        """)
        open_cases = cur.fetchone()[0]

        # ── Open POs ─────────────────────────────────────
        cur.execute("""
            SELECT COUNT(*)
            FROM PURCHTABLE WITH (NOLOCK)
            WHERE PURCHSTATUS IN (0, 1)
        """)
        open_pos = cur.fetchone()[0]

        # ── RFQ created per month (trend) ─────────────────
        cur.execute("""
            SELECT
                MONTH(CREATEDDATETIME),
                COUNT(DISTINCT RFQID)
            FROM PurchRFQTable WITH (NOLOCK)
            WHERE YEAR(CREATEDDATETIME) = ?
            GROUP BY MONTH(CREATEDDATETIME)
        """, year)
        rfq_data = dict(cur.fetchall())

        # ── PO data per month (trend) ─────────────────────
        cur.execute("""
            SELECT
                MONTH(PT.CREATEDDATETIME),
                COUNT(DISTINCT PT.PURCHID),
                COUNT(DISTINCT CASE
                    WHEN PR.RFQID IS NOT NULL THEN PT.PURCHID
                END),
                COUNT(DISTINCT CASE
                    WHEN PR.RFQID IS NULL THEN PT.PURCHID
                END)
            FROM PURCHTABLE PT WITH (NOLOCK)
            LEFT JOIN PURCHRFQLINE PR WITH (NOLOCK)
                ON PT.PURCHID = PR.PURCHID
            WHERE YEAR(PT.CREATEDDATETIME) = ?
            GROUP BY MONTH(PT.CREATEDDATETIME)
        """, year)
        for r in cur.fetchall():
            po_data[r[0]] = {
                "total_po":           r[1],
                "po_from_bidding":    r[2],
                "po_without_bidding": r[3]
            }

        # ── Action required: RFQs closing today ───────────
        cur.execute("""
            SELECT RFQCASEID
            FROM PurchRFQCaseTable WITH (NOLOCK)
            WHERE CAST(EXPIRYDATETIME AS DATE) = CAST(GETDATE() AS DATE)
        """)
        for row in cur.fetchall():
            action_required.append({
                "type":    "RFQ",
                "message": f"{row[0]} - Closing today"
            })

        # ── Recent activity: new POs ──────────────────────
        cur.execute("""
            SELECT TOP 5 PURCHID, CREATEDDATETIME
            FROM PURCHTABLE WITH (NOLOCK)
            ORDER BY CREATEDDATETIME DESC
        """)
        for row in cur.fetchall():
            dt = row[1]
            recent_activity.append({
                "message":  f"PO {row[0]} issued",
                "time":     dt.strftime("%d %b %I:%M %p") if dt else "",
                "sort_key": dt
            })

        # ── Recent activity: new RFQs ─────────────────────
        cur.execute("""
            SELECT TOP 5 RFQCASEID, CREATEDDATETIME
            FROM PurchRFQCaseTable WITH (NOLOCK)
            ORDER BY CREATEDDATETIME DESC
        """)
        for row in cur.fetchall():
            dt = row[1]
            recent_activity.append({
                "message":  f"RFQ {row[0]} created",
                "time":     dt.strftime("%d %b %I:%M %p") if dt else "",
                "sort_key": dt
            })

    # ══════════════════════════════════════════════════════
    # BUILD TREND — combine all 3 month datasets
    # ══════════════════════════════════════════════════════

    trend = []
    for m in range(1, 13):
        po_info = po_data.get(m, {})
        trend.append({
            "month":              MONTH_MAP[m],
            "rfq_created":        rfq_data.get(m, 0),
            "vendor_responses":   response_data.get(m, 0),
            "po_issued":          po_info.get("po_from_bidding", 0),
            "total_po":           po_info.get("total_po", 0),
            "po_without_bidding": po_info.get("po_without_bidding", 0)
        })

    # ── Sort recent activity by datetime desc ─────────────
    recent_activity.sort(
        key=lambda x: x["sort_key"] or datetime.min,
        reverse=True
    )
    for item in recent_activity:
        item.pop("sort_key", None)

    # ══════════════════════════════════════════════════════
    # RETURN
    # ══════════════════════════════════════════════════════

    return {
        "summary": {
            "total_vendors":   total_vendors,
            "active_vendors":  active_vendors,
            "pending_vendors": pending_vendors,
            "open_cases":      open_cases,
            "open_pos":        open_pos
        },
        "trend":           trend,
        "action_required": action_required,
        "recent_activity": recent_activity
    }


async def get_dashboard(year: int):
    return await run_in_threadpool(get_dashboard_sync, year)


# from app.db.base import get_connection
# from app.utils.date_utils import format_date
# from fastapi.concurrency import run_in_threadpool

# def get_dashboard_sync(year: int):

#     trend = []
#     action_required = []
#     recent_activity = []

#     month_map = {
#         1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
#         5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
#         9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
#     }

#     with get_connection() as conn:
#         cur = conn.cursor()

#         # =========================
#         # SUMMARY
#         # =========================
#         cur.execute("SELECT COUNT(*) FROM vendtable")
#         total_vendors = cur.fetchone()[0]

#         cur.execute("""
#             SELECT COUNT(*)
#             FROM HIQ_VendorPortalUser
#             WHERE STATUS in (1)
#         """)
#         active_vendors = cur.fetchone()[0]

#         cur.execute("""
#             SELECT COUNT(*)
#             FROM PurchRFQCaseTable
#             WHERE EXPIRYDATETIME >= GETDATE()
#         """)
#         open_cases = cur.fetchone()[0]

#         cur.execute("""
#             SELECT COUNT(*)
#             FROM PURCHTABLE
#             WHERE PURCHSTATUS IN (0,1)
#         """)
#         open_pos = cur.fetchone()[0]

#         # =========================
#         # TREND (FINAL FIXED)
#         # =========================

#         # RFQ CREATED
#         cur.execute("""
#             SELECT 
#                 MONTH(CREATEDDATETIME),
#                 COUNT(DISTINCT RFQID)
#             FROM PurchRFQTable
#             WHERE YEAR(CREATEDDATETIME) = ?
#               AND DATAAREAID = 'hi-q'
#             GROUP BY MONTH(CREATEDDATETIME)
#         """, (year,))
#         rfq_data = dict(cur.fetchall())

#         # VENDOR RESPONSES
#         cur.execute("""
#             SELECT MONTH(CREATED_AT), COUNT(DISTINCT RFQ_ID)
#             FROM HIQ_VENDORRFQREPLIES
#             WHERE YEAR(CREATED_AT) = ?
#               AND SUBMISSION_STATUS = 1
#             GROUP BY MONTH(CREATED_AT)
#         """, (year,))
#         response_data = dict(cur.fetchall())

#         #  FIXED PO LOGIC (USING PURCHRFQLINE)
#         cur.execute("""
#             SELECT 
#                 MONTH(PT.CREATEDDATETIME),

#                 COUNT(DISTINCT PT.PURCHID),

#                 COUNT(DISTINCT CASE 
#                     WHEN PR.RFQID IS NOT NULL THEN PT.PURCHID
#                 END),

#                 COUNT(DISTINCT CASE 
#                     WHEN PR.RFQID IS NULL THEN PT.PURCHID
#                 END)

#             FROM PURCHTABLE PT

#             LEFT JOIN PURCHRFQLINE PR
#                 ON PT.PURCHID = PR.PURCHID

#             WHERE YEAR(PT.CREATEDDATETIME) = ?
#               AND PT.DATAAREAID = 'hi-q'

#             GROUP BY MONTH(PT.CREATEDDATETIME)
#         """, (year,))

#         rows = cur.fetchall()
#         po_data = {}
#         for r in rows:
#             po_data[r[0]] = {
#                 "total_po": r[1],
#                 "po_from_bidding": r[2],
#                 "po_without_bidding": r[3]
#             }

#         # BUILD TREND
#         for m in range(1, 13):
#             po_info = po_data.get(m, {})
#             trend.append({
#                 "month": month_map[m],
#                 "rfq_created": rfq_data.get(m, 0),
#                 "vendor_responses": response_data.get(m, 0),
#                 "po_issued": po_info.get("po_from_bidding", 0),
#                 "total_po": po_info.get("total_po", 0),
#                 "po_without_bidding": po_info.get("po_without_bidding", 0)
#             })

#         # =========================
#         # ACTION REQUIRED
#         # =========================
#         cur.execute("""
#             SELECT VENDOR_ACCOUNT
#             FROM HIQ_VendorPortalUser
#             WHERE STATUS = 0
#         """)
#         for row in cur.fetchall():
#             action_required.append({
#                 "type": "Vendor",
#                 "message": f"{row.VENDOR_ACCOUNT} - Activation pending"
#             })

#         cur.execute("""
#             SELECT RFQCASEID
#             FROM PurchRFQCaseTable
#             WHERE CAST(EXPIRYDATETIME AS DATE) = CAST(GETDATE() AS DATE)
#         """)
#         for row in cur.fetchall():
#             action_required.append({
#                 "type": "RFQ",
#                 "message": f"{row.RFQCASEID} - Closing today"
#             })

#         # =========================
#         # RECENT ACTIVITY
#         # =========================
#         cur.execute("""
#             SELECT TOP 5 VENDOR_ACCOUNT, UPDATED_AT
#             FROM HIQ_VendorPortalUser
#             WHERE STATUS IN (1,2)
#             ORDER BY UPDATED_AT DESC
#         """)
#         for row in cur.fetchall():
#             recent_activity.append({
#                 "message": f"{row.VENDOR_ACCOUNT} activated vendor portal",
#                 "time": row.UPDATED_AT.strftime("%d %b %I:%M %p")
#             })

#         cur.execute("""
#             SELECT TOP 5 PURCHID, CREATEDDATETIME
#             FROM PURCHTABLE
#             ORDER BY CREATEDDATETIME DESC
#         """)
#         for row in cur.fetchall():
#             recent_activity.append({
#                 "message": f"PO {row.PURCHID} issued",
#                 "time": row.CREATEDDATETIME.strftime("%d %b %I:%M %p")
#             })

#         cur.execute("""
#             SELECT TOP 5 RFQCASEID, t.CREATEDDATETIME
#             FROM PurchRFQCaseTable  t
#             ORDER BY t.CREATEDDATETIME DESC
#         """)
#         for row in cur.fetchall():
#             recent_activity.append({
#                 "message": f"RFQ {row.RFQCASEID} created",
#                 "time": row.CREATEDDATETIME.strftime("%d %b %I:%M %p")
#             })

#     recent_activity.sort(key=lambda x: x["time"], reverse=True)

#     return {
#         "summary": {
#             "total_vendors": total_vendors,
#             "active_vendors": active_vendors,
#             "open_cases": open_cases,
#             "open_pos": open_pos
#         },
#         "trend": trend,
#         "action_required": action_required,
#         "recent_activity": recent_activity
#     }

# async def get_dashboard(year:int):
#     return await run_in_threadpool(get_dashboard_sync,year)