
from app.db.base import get_connection
from app.utils.date_utils import format_date,format_ist_date_only
from app.utils.remainingdate import calculate_days_left

from app.services.underreviewrfq import (
    get_underreview_cases,
    get_vendor_underreview_rfqs
)

from app.services.onbidrfq import (
    fetch_on_bidding_cases,
    get_vendor_bidrfqs
)

from app.services.closedrfq import (
    get_closed_cases,
    get_vendor_closed_rfqs
)

from app.services.expiryrfq import (
    get_expired_cases,
    get_vendor_expired_rfqs
)

import asyncio
from datetime import datetime


async def get_rfq_cases(status: str = "all"):

    under_review, on_bidding, closed, expired = await asyncio.gather(
        get_underreview_cases(),
        fetch_on_bidding_cases(),
        get_closed_cases(),
        get_expired_cases()
    )
    # ==========================================================
    # COMBINE ALL CASES
    # ==========================================================
    all_cases = (
        under_review +
        on_bidding +
        closed +
        expired
    )

    # ==========================================================
    # REMOVE DUPLICATE RFQ CASES
    # ==========================================================
    case_map = {}

    for case in all_cases:

        key = case["rfq_case_id"]

        if key not in case_map:

            case_map[key] = {

                "rfq_case_id": case["rfq_case_id"],

                "case_name": case["case_name"],

                "created_date": case["created_date"],

                "expiry_date": case["expiry_date"],

                "vendor_count": case["vendor_count"]
            }

    final_data = list(case_map.values())

    # ==========================================================
    # SORT
    # ==========================================================
    final_data.sort(
        key=lambda x: datetime.strptime(
            x["created_date"],
            "%d/%m/%Y"
        ),
        reverse=True
    )
    return final_data

    # final_data = (
    #     under_review +
    #     on_bidding +
    #     closed +
    #     expired
    # )

    # final_data.sort(
    #     key=lambda x: datetime.strptime(
    #         x["created_date"],
    #         "%d/%m/%Y"
    #     ),
    #     reverse=True
    # )

    # if status == "under_review":
    #     final_data = [
    #         c for c in final_data
    #         if c["status"] == "Under Review"
    #     ]

    # elif status == "on_bidding":
    #     final_data = [
    #         c for c in final_data
    #         if c["status"] == "On Bidding"
    #     ]

    # elif status == "closed":
    #     final_data = [
    #         c for c in final_data
    #         if c["status"] == "Closed"
    #     ]

    # elif status == "expired":
    #     final_data = [
    #         c for c in final_data
    #         if c["status"] == "Expired"
    #     ]

    # return final_data


async def get_case_full_details(
    rfq_case_id: str,
    status: str
):

    if status == "On Bidding":
        return await get_vendor_bidrfqs(rfq_case_id)

    elif status == "Under Review":
        return await get_vendor_underreview_rfqs(rfq_case_id)

    elif status == "Closed":
        return await get_vendor_closed_rfqs(rfq_case_id)

    elif status == "Expired":
        return await get_vendor_expired_rfqs(rfq_case_id)

    return {
        "ok": False,
        "message": f"Invalid status: {status}"
    }
async def get_case_statuses(rfq_case_id: str):

    under_review, on_bidding, closed, expired = await asyncio.gather(
        get_underreview_cases(),
        fetch_on_bidding_cases(),
        get_closed_cases(),
        get_expired_cases()
    )

    all_cases = (
        under_review +
        on_bidding +
        closed +
        expired
    )

    case_info = None
    statuses = []

    for case in all_cases:

        if case["rfq_case_id"] != rfq_case_id:
            continue

        if case_info is None:

            case_info = {

                "rfq_case_id": case["rfq_case_id"],
                "case_name": case["case_name"],
                "created_date": case["created_date"],
                "expiry_date": case["expiry_date"]
            }

        statuses.append({

            "status": case["status"],

            "vendor_count": case.get(
                "expired_vendor_count",
                case.get("vendor_count", 0)
            )
        })

    if not case_info:
        return {}

    case_info["statuses"] = statuses

    return case_info



# from app.db.base import get_connection
# from app.utils.date_utils import format_date,format_ist_date_only
# from app.utils.remainingdate import calculate_days_left
# from app.services.underreviewrfq import get_underreview_cases
# from app.services.onbidrfq import fetch_on_bidding_cases
# from app.services.closedrfq import get_closed_cases
# import asyncio
# async def get_rfq_cases(status: str = "all"):
#     under_review, on_bidding, closed = await asyncio.gather(
#         get_underreview_cases(),
#         fetch_on_bidding_cases(),
#         get_closed_cases()
#     )

#     # under_review = get_underreview_cases()
#     # on_bidding   = fetch_on_bidding_cases()
#     # closed       = get_closed_cases()

#     # ✅ Combine all (NO UNIQUE)
#     final_data = under_review + on_bidding + closed

#     # ✅ Sort by created date DESC
#     from datetime import datetime

#     final_data.sort(
#         key=lambda x: datetime.strptime(x["created_date"], "%d/%m/%Y"),
#         reverse=True
#     )
#     # final_data.sort(key=lambda x: x["created_date"], reverse=True)

#     # ✅ Filter by tab
#     if status == "under_review":
#         final_data = [c for c in final_data if c["status"] == "Under Review"]

#     elif status == "on_bidding":
#         final_data = [c for c in final_data if c["status"] == "On Bidding"]

#     elif status == "closed":
#         final_data = [c for c in final_data if c["status"] == "Closed"]

#     return final_data
# # def get_rfq_cases(status: str = "all"):

# #     under_review = get_underreview_cases()
# #     on_bidding   = fetch_on_bidding_cases()
# #     closed       = get_closed_cases()

# #     # ✅ Combine all
# #     all_cases = under_review + on_bidding + closed

# #     # ✅ Remove duplicates (important)
# #     unique_cases = {}
# #     for c in all_cases:
# #         unique_cases[c["rfq_case_id"]] = c

# #     final_data = list(unique_cases.values())

# #     # ✅ Sort by created date DESC
# #     final_data.sort(key=lambda x: x["created_date"], reverse=True)

# #     # ✅ Filter by tab
# #     if status == "under_review":
# #         final_data = [c for c in final_data if c["status"] == "Under Review"]

# #     elif status == "on_bidding":
# #         final_data = [c for c in final_data if c["status"] == "On Bidding"]

# #     elif status == "closed":
# #         final_data = [c for c in final_data if c["status"] == "Closed"]

# #     return final_data

# from app.services.onbidrfq import get_vendor_bidrfqs
# from app.services.underreviewrfq import get_vendor_underreview_rfqs
# from app.services.closedrfq import get_vendor_closed_rfqs

# async def get_case_full_details(rfq_case_id: str):

#     # 🔹 Step 1: Get case status from header API
#     cases = await get_rfq_cases()

#     case = next((c for c in cases if c["rfq_case_id"] == rfq_case_id), None)

#     if not case:
#         return {"ok": False, "message": "RFQ Case not found"}

#     status = case["status"]

#     # 🔹 Step 2: Route to correct API
#     if status == "On Bidding":
#         detail = await get_vendor_bidrfqs(rfq_case_id)

#     elif status == "Under Review":
#         detail =await  get_vendor_underreview_rfqs(rfq_case_id)

#     elif status == "Closed":
#         detail = await get_vendor_closed_rfqs(rfq_case_id)

#     else:
#         detail = {}

#     return detail

