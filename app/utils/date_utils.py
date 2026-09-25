# from datetime import datetime

# def format_date(value):
#     if value:
#         return value.strftime("%d/%m/%Y")
#     return None

from datetime import datetime

def format_date(value):
    if not value:
        return None

    #
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")


    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", ""))
            return dt.strftime("%d/%m/%Y")
        except:
            return value  

    return str(value) 



from datetime import datetime, timedelta

def format_ist_date_only(dt) -> str | None:
    """
    Converts UTC datetime from D365 DB → IST date string.
    Returns date only (no time) — same for ALL vendors worldwide.
    
    India vendor  → "23/04/2026" 
    USA vendor    → "23/04/2026"   
    Austria vendor→ "23/04/2026" 
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        ist = dt + timedelta(hours=5, minutes=30)
        return ist.strftime("%d/%m/%Y")
    return None 


# app/utils/date_utils.py

def format_utc_iso(dt) -> str | None:
    """
    Returns UTC ISO string for frontend timezone conversion.
    Frontend browser auto-converts to user's local time.
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # → "2026-04-22T18:30:00Z"
    return None 

