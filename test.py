from app.db.base import get_connection, get_d365_connection


def test_vendor_portal_connection():
    """
    Test Vendor Portal Database Connection
    """

    try:
        with get_connection() as conn:

            cursor = conn.cursor()

            cursor.execute("""
                SELECT TOP 1 *
                FROM dev.HIQ_VENDORBIDSUBMISSIONHEADER
            """)

            columns = [column[0] for column in cursor.description]
            row = cursor.fetchone()

            print("\n==============================")
            print("✅ VENDOR PORTAL DB CONNECTED")
            print("==============================")

            if row:
                result = dict(zip(columns, row))

                print("✅ Vendor Portal Data Retrieved Successfully")
                print(result)

            else:
                print("⚠ No data found in HIQ_VENDORBIDSUBMISSIONHEADER")

    except Exception as e:
        print("\n❌ Vendor Portal DB Connection Failed")
        print(str(e))


def test_d365_connection():
    """
    Test D365 Database Connection
    """

    try:
        with get_d365_connection() as conn:

            cursor = conn.cursor()

            cursor.execute("""
                SELECT TOP 1 *
                FROM purchtable
            """)

            columns = [column[0] for column in cursor.description]
            row = cursor.fetchone()

            print("\n==============================")
            print("✅ D365 DB CONNECTED")
            print("==============================")

            if row:
                result = dict(zip(columns, row))

                print("✅ purchtable Data Retrieved Successfully")
                print(result)

            else:
                print("⚠ No data found in purchtable")

    except Exception as e:
        print("\n❌ D365 DB Connection Failed")
        print(str(e))


if __name__ == "__main__":

    print("\n🚀 TESTING DATABASE CONNECTIONS...\n")

    # Test Vendor Portal DB
    test_vendor_portal_connection()

    # Test D365 DB
    test_d365_connection()

    print("\n🏁 TEST COMPLETED\n")