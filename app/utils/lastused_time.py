from datetime import datetime

def format_last_edited(dt) -> str:
    """
    Converts datetime to '2 hours ago', '1 day ago' etc.
    """
    if not dt:
        return "N/A"
    
    now  = datetime.now()
    diff = now - dt

    seconds = int(diff.total_seconds())
    minutes = seconds // 60
    hours   = minutes // 60
    days    = hours   // 24

    if seconds < 60:
        return "Just now"
    elif minutes < 60:
        return f"{minutes} min ago"
    elif hours < 24:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    else:
        return f"{days} day{'s' if days > 1 else ''} ago"
