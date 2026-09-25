from datetime import datetime
def calculate_days_left(expiry_date):
    if not expiry_date:
        return None

    today = datetime.today().date()
    expiry = expiry_date.date()

    return (expiry - today).days 

def format_expiry_label(expiry_date):

    days = calculate_days_left(expiry_date)

    if days < 0:
        return "Expired"

    elif days == 0:
        return "Today"

    elif days == 1:
        return "1 day"

    else:
        return f"{days} days"