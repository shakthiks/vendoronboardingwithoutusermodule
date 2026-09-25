from app.db.base import get_connection
def fetch_vendor_profile(vendor_account: str):

    profile = {
        "email": None,
        "phone": None,
        "address": None,
        "name":None
    }

    with get_connection() as conn:
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
