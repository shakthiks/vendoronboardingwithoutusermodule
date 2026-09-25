from datetime import datetime
import hashlib

from app.db.base import get_connection, get_d365_connection
from app.core.config import settings


SCHEMA = settings.DB_SCHEMA

VENDOR_USER_TABLE = f"{SCHEMA}.HIQ_VENDORPORTALUSER"
OTP_TABLE = f"{SCHEMA}.HIQ_VENDOROTP"
LOGIN_LOG_TABLE = f"{SCHEMA}.HIQ_VENDORLOGINLOG"
AUTH_TOKEN_TABLE = f"{SCHEMA}.HIQ_VendorAuthToken"
REFRESH_TOKEN_TABLE = f"{SCHEMA}.HIQ_VENDORREFRESHTOKEN"


STATUS_MAP = {
    "Pending": 0,
    "Active": 1,
    "Inactive": 2,
}

LOGIN_METHOD_MAP = {
    "PASSWORD": 0,
    "PHONE_OTP": 1,
    "EMAIL_OTP": 2,
    "SMS_OTP": 2,
    "GOOGLE": 3,
}

LOGIN_STATUS_MAP = {
    "SUCCESS": 0,
    "FAILED": 1,
}

TOKEN_TYPE_MAP = {
    "SET_PASSWORD": 1,
    "RESET_PASSWORD": 2,
    "EMAIL_OTP": 3,
}

TOKEN_TYPE_REVERSE = {v: k for k, v in TOKEN_TYPE_MAP.items()}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_vendor_from_master(identifier: str):
    q = """
    SELECT TOP 1
        V.ACCOUNTNUM,
        P.NAME,
        E.LOCATOR AS email
    FROM VENDTABLE V WITH (NOLOCK)
    LEFT JOIN HIQ_vendorPostalADDRESSVIEW P
        ON P.ACCOUNTNUM = V.ACCOUNTNUM
       AND P.ISPRIMARY = 1
    LEFT JOIN HIQ_vendorELECTRONICADDRESSVIEW E
        ON E.ACCOUNTNUM = V.ACCOUNTNUM
       AND E.ISPRIMARY1 = 1 AND E.TYPE=2
    WHERE LOWER(E.LOCATOR) = LOWER(?)
       OR V.ACCOUNTNUM = ?
    """

    with get_d365_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, identifier, identifier)
        row = cur.fetchone()

        if not row:
            return None

        return {
            "accountnum": row[0],
            "name": row[1],
            "email": row[2],
        }

def get_user_by_email(email: str):
    q = f"""
    SELECT TOP 1 *
    FROM {VENDOR_USER_TABLE} WITH (NOLOCK)
    WHERE UPPER(EMAILADDRESS) = UPPER(?)
      AND ISCURRENT = 1
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, email)

        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0].lower() for c in cur.description]
        return dict(zip(cols, row))
def get_vendor_name(vendor_account: str):
    q = """
    SELECT TOP 1
        ISNULL(DP.NAME, VT.ACCOUNTNUM) AS NAME
    FROM VENDTABLE VT WITH (NOLOCK)
    LEFT JOIN DIRPARTYTABLE DP WITH (NOLOCK)
        ON DP.RECID = VT.PARTY
    WHERE UPPER(VT.ACCOUNTNUM) = UPPER(?)
    """

    with get_d365_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, vendor_account)
        row = cur.fetchone()

        return row[0] if row else vendor_account


def get_user_by_email_or_phone(identifier: str):
    q = f"""
    SELECT TOP 1
        ID,
        VENDORACCOUNT,
        EMAILADDRESS,
        PHONENUMBER,
        PASSWORDHASH,
        STATUS,
        ISCURRENT,
        CREATEDDATETIME
    FROM {VENDOR_USER_TABLE} WITH (NOLOCK)
    WHERE (EMAILADDRESS = ? OR PHONENUMBER = ?)
      AND ISCURRENT = 1
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, identifier, identifier)
        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0].lower() for c in cur.description]
        user = dict(zip(cols, row))

    user["name"] = get_vendor_name(user["vendoraccount"])
    return user


def get_portal_user(vendor_account: str):
    q = f"""
    SELECT TOP 1 *
    FROM {VENDOR_USER_TABLE} WITH (NOLOCK)
    WHERE VENDORACCOUNT = ?
      AND ISCURRENT = 1
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, vendor_account)
        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0].lower() for c in cur.description]
        return dict(zip(cols, row))


def get_user_by_vendor_account(vendor_account: str):
    return get_portal_user(vendor_account)

def insert_portal_user(
    vendor_account: str,
    email: str,
    phone: str = None
):

    q = f"""
    INSERT INTO {VENDOR_USER_TABLE}
    (
        VENDORACCOUNT,
        EMAILADDRESS,
        PHONENUMBER,
        STATUS,
        ISCURRENT,
        CREATEDBY,
        CREATEDDATETIME
    )
    VALUES (?, ?, ?, ?, 1, 'PORTAL', GETUTCDATE())
    """

    with get_connection() as conn:

        cur = conn.cursor()

        cur.execute(
            q,
            vendor_account,
            email,
            phone or "",
            STATUS_MAP["Pending"]
        )

        conn.commit()

    # REFETCH USER
    user = get_portal_user(vendor_account)

    if not user:
        raise Exception(
            f"User insert failed for {vendor_account}"
        )

    return user["id"]
# def insert_portal_user(vendor_account: str, email: str, phone: str = None):
#     q = f"""
#     INSERT INTO {VENDOR_USER_TABLE}
#     (
#         VENDORACCOUNT,
#         EMAILADDRESS,
#         PHONENUMBER,
#         STATUS,
#         ISCURRENT,
#         CREATEDBY,
#         CREATEDDATETIME
#     )
#     VALUES (?, ?, ?, ?, 1, 'PORTAL', GETUTCDATE())
#     """

#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, vendor_account, email, phone or "", STATUS_MAP["Pending"])
#         cur.execute("SELECT SCOPE_IDENTITY()")
#         new_id = int(cur.fetchone()[0])
#         conn.commit()
#         return new_id


def update_portal_user_status(vendor_account: str, status: int):
    q = f"""
    UPDATE {VENDOR_USER_TABLE}
    SET
        STATUS = ?,
        MODIFIEDDATETIME = GETUTCDATE(),
        MODIFIEDBY = 'PORTAL',
        RECVERSION = RECVERSION + 1
    WHERE VENDORACCOUNT = ?
      AND ISCURRENT = 1
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, status, vendor_account)
        conn.commit()


def update_password(user_id: int, password_hash: str):
    q = f"""
    UPDATE {VENDOR_USER_TABLE}
    SET
        PASSWORDHASH = ?,
        PASSWORDSETAT = GETUTCDATE(),
        STATUS = ?,
        MODIFIEDDATETIME = GETUTCDATE(),
        MODIFIEDBY = 'PORTAL',
        RECVERSION = RECVERSION + 1
    WHERE ID = ?
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, password_hash, STATUS_MAP["Active"], user_id)
        conn.commit()


def insert_otp(user_id: int, otp_code: str, expires_at: datetime):
    q = f"""
    INSERT INTO {OTP_TABLE}
    (
        USERID,
        OTPCODE,
        ISUSED,
        ATTEMPTS,
        EXPIRESAT,
        CREATEDBY,
        CREATEDDATETIME
    )
    VALUES (?, ?, 0, 0, ?, 'PORTAL', GETUTCDATE())
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, str(user_id), otp_code, expires_at)
        cur.execute("SELECT SCOPE_IDENTITY()")
        otp_id = int(cur.fetchone()[0])
        conn.commit()
        return otp_id


def get_valid_otp(user_id: int, otp_code: str):
    q = f"""
    SELECT TOP 1 *
    FROM {OTP_TABLE} WITH (NOLOCK)
    WHERE USERID = ?
      AND OTPCODE = ?
      AND ISUSED = 0
      AND EXPIRESAT >= GETUTCDATE()
    ORDER BY EXPIRESAT DESC
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, str(user_id), otp_code)
        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0].lower() for c in cur.description]
        return dict(zip(cols, row))


def mark_otp_used(otp_id: int, current_attempts: int = 0):
    q = f"""
    UPDATE {OTP_TABLE}
    SET
        ISUSED = 1,
        ATTEMPTS = ?,
        MODIFIEDDATETIME = GETUTCDATE(),
        MODIFIEDBY = 'PORTAL',
        RECVERSION = RECVERSION + 1
    WHERE ID = ?
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, current_attempts + 1, otp_id)
        conn.commit()


def log_login(user_id: int, method: str, status: str, ip: str = None, reason: str = None):
    method_int = LOGIN_METHOD_MAP.get(method.upper(), LOGIN_METHOD_MAP["PASSWORD"])
    status_int = LOGIN_STATUS_MAP.get(status.upper(), LOGIN_STATUS_MAP["FAILED"])

    q = f"""
    INSERT INTO {LOGIN_LOG_TABLE}
    (
        USERID,
        LOGINMETHOD,
        STATUS,
        IPADDRESS,
        FAILUREREASON,
        CREATEDBY,
        CREATEDDATETIME
    )
    VALUES (?, ?, ?, ?, ?, 'PORTAL', GETUTCDATE())
    """

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(q, str(user_id), method_int, status_int, ip or "", reason or "")
            conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("[LOGIN LOG FAILED] %s", str(e))


def insert_token(user_id: int, token: str, token_type: str, expires_at: datetime):
    q = f"""
    INSERT INTO {AUTH_TOKEN_TABLE}
    (
        USER_ID,
        TOKEN,
        TOKEN_TYPE,
        EXPIRES_AT
    )
    VALUES (?, ?, ?, ?)
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, user_id, token, TOKEN_TYPE_MAP[token_type], expires_at)
        conn.commit()


def get_valid_token(token: str, token_type: str):
    q = f"""
    SELECT TOP 1 *
    FROM {AUTH_TOKEN_TABLE} WITH (NOLOCK)
    WHERE TOKEN = ?
      AND TOKEN_TYPE = ?
      AND IS_USED = 0
      AND EXPIRES_AT >= GETUTCDATE()
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, token, TOKEN_TYPE_MAP[token_type])
        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0].lower() for c in cur.description]
        result = dict(zip(cols, row))
        result["token_type"] = TOKEN_TYPE_REVERSE.get(
            result["token_type"],
            result["token_type"]
        )
        return result


def mark_token_used(token_id: int):
    q = f"""
    UPDATE {AUTH_TOKEN_TABLE}
    SET IS_USED = 1
    WHERE ID = ?
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, token_id)
        conn.commit()


def save_refresh_token(
    user_id: int,
    vendor_account: str,
    refresh_token: str,
    expires_at: datetime,
    ip: str = None,
    device_info: str = None
):
    token_hash = hash_token(refresh_token)

    q = f"""
    INSERT INTO {REFRESH_TOKEN_TABLE}
    (
        USERID,
        VENDORACCOUNT,
        TOKENHASH,
        DEVICEINFO,
        IPADDRESS,
        ISREVOKED,
        EXPIRESAT,
        CREATEDBY,
        CREATEDDATETIME
    )
    VALUES (?, ?, ?, ?, ?, 0, ?, 'PORTAL', GETUTCDATE())
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            q,
            str(user_id),
            vendor_account,
            token_hash,
            device_info or "",
            ip or "",
            expires_at
        )
        conn.commit()


def get_refresh_token(refresh_token: str):
    token_hash = hash_token(refresh_token)

    q = f"""
    SELECT TOP 1 *
    FROM {REFRESH_TOKEN_TABLE} WITH (NOLOCK)
    WHERE TOKENHASH = ?
      AND ISREVOKED = 0
      AND EXPIRESAT >= GETUTCDATE()
    ORDER BY CREATEDDATETIME DESC
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, token_hash)
        row = cur.fetchone()

        if not row:
            return None

        cols = [c[0].lower() for c in cur.description]
        return dict(zip(cols, row))


def revoke_refresh_token(refresh_token: str):
    token_hash = hash_token(refresh_token)

    q = f"""
    UPDATE {REFRESH_TOKEN_TABLE}
    SET
        ISREVOKED = 1,
        MODIFIEDDATETIME = GETUTCDATE(),
        MODIFIEDBY = 'PORTAL'
    WHERE TOKENHASH = ?
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, token_hash)
        conn.commit()

def update_portal_user_email(
    vendor_account: str,
    email: str
):
    q = f"""
    UPDATE {VENDOR_USER_TABLE}
    SET
        EMAILADDRESS = ?,
        MODIFIEDDATETIME = GETUTCDATE(),
        MODIFIEDBY = 'PORTAL',
        RECVERSION = RECVERSION + 1
    WHERE VENDORACCOUNT = ?
      AND ISCURRENT = 1
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(q, email, vendor_account)
        conn.commit()
# import random
# from datetime import datetime
# from app.db.base import get_connection
# import requests
# from app.core.config import settings
# from app.db.base import get_connection
# # from app.api.routes.rfq_reply import fmt_datetime_ms
# from app.core.d365_auth import get_d365_token
# # TOKEN_TYPE int mapping (D365 table stores int not string)

# from datetime import datetime
# def fmt_expiry_datetime_ms(val=None):
#     """
#     Same as fmt_datetime_ms but forces time to 23:59:59.000
#     for expiry dates that come in as DD/MM/YYYY (date only, no time).
#     """
#     if not val:
#         return None

#     if isinstance(val, datetime):
#         # If already a datetime, just set end-of-day time
#         dt = val.replace(hour=23, minute=59, second=59, microsecond=0)
#         return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

#     val = str(val).replace("T", " ").strip()

#     # DD/MM/YYYY — date only, force end of day
#     if "/" in val:
#         try:
#             dt = datetime.strptime(val[:10], "%d/%m/%Y")
#             return dt.strftime("%Y-%m-%d 23:59:59.000")
#         except:
#             return None

#     # YYYY-MM-DD HH:MM:SS — has time already, use as-is
#     try:
#         dt = datetime.strptime(val[:19], "%Y-%m-%d %H:%M:%S")
#         return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
#     except:
#         pass

#     # YYYY-MM-DD — date only, force end of day
#     try:
#         dt = datetime.strptime(val[:10], "%Y-%m-%d")
#         return dt.strftime("%Y-%m-%d 23:59:59.000")
#     except:
#         return None
# def fmt_datetime_ms(val=None):
#     if not val:
#         dt = datetime.now()
#     elif isinstance(val, datetime):
#         dt = val
#     else:
#         val = str(val).replace("T", " ").strip()
        
#         # Handle DD/MM/YYYY
#         if "/" in val:
#             try:
#                 dt = datetime.strptime(val[:10], "%d/%m/%Y")
#             except:
#                 dt = datetime.now()
#         else:
#             try:
#                 dt = datetime.strptime(val[:19], "%Y-%m-%d %H:%M:%S")
#             except:
#                 try:
#                     dt = datetime.strptime(val[:10], "%Y-%m-%d")
#                 except:
#                     return None

#     return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
# TOKEN_TYPE_MAP = {
#     "SET_PASSWORD":   1,
#     "RESET_PASSWORD": 2,
#     "EMAIL_OTP":      3
# }
# TOKEN_TYPE_REVERSE = {v: k for k, v in TOKEN_TYPE_MAP.items()}
# # Status mapping — same pattern as TOKEN_TYPE
# STATUS_MAP = {
#     "Pending":  0,
#     "Active":   1,
#     "Inactive": 2
# }

# # Login method mapping
# LOGIN_METHOD_MAP = {
#     "PASSWORD":  1,
#     "EMAIL_OTP": 2,
#     "SMS_OTP":   3,
#     "GOOGLE":    4,
# }

# # Login status mapping
# LOGIN_STATUS_MAP = {
#     "SUCCESS": 1,
#     "FAILED":  2,
# }



# def _new_id() -> int:
#     """Generate unique ID within SQL Server int range."""
#     return random.randint(100000, 200000)


# # ─────────────────────────────────────────────
# # USER LOOKUP
# # ─────────────────────────────────────────────
# # def get_user_by_email_or_phone(identifier: str):
# #     q = """
# #     SELECT TOP 1 *
# #     FROM HIQ_VendorPortalUser WITH (NOLOCK)
# #     WHERE email_address = ? OR phone_number = ?
# #     """
# #     with get_connection() as conn:
# #         cur = conn.cursor()
# #         cur.execute(q, identifier, identifier)
# #         row = cur.fetchone()
# #         if not row:
# #             return None
# #         cols = [c[0].lower() for c in cur.description]
# #         return dict(zip(cols, row))
# def get_user_by_email_or_phone(identifier: str):
#     q = """
#     SELECT TOP 1 
#         U.*,

#         -- ✅ Get correct vendor name
#         ISNULL(DP.NAME, VT.ACCOUNTNUM) AS name

#     FROM HIQ_VendorPortalUser U WITH (NOLOCK)

#     -- ✅ Join Vendor Master
#     LEFT JOIN VENDTABLE VT WITH (NOLOCK)
#         ON UPPER(VT.ACCOUNTNUM) = UPPER(U.vendor_account)

#     -- ✅ Join Party table (actual name)
#     LEFT JOIN DIRPARTYTABLE DP WITH (NOLOCK)
#         ON DP.RECID = VT.PARTY

#     WHERE U.email_address = ? OR U.phone_number = ?
#     """

#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, identifier, identifier)

#         row = cur.fetchone()
#         if not row:
#             return None

#         cols = [c[0].lower() for c in cur.description]
#         user = dict(zip(cols, row))

#         return user

# # def get_user_by_email_or_phone(identifier: str):
# #     q = """
# #     SELECT TOP 1 
# #         U.*,
# #         DP.NAME AS vendor_name   -- ✅ Correct name source

# #     FROM HIQ_VendorPortalUser U WITH (NOLOCK)

# #     LEFT JOIN VENDTABLE VT WITH (NOLOCK)
# #         ON VT.ACCOUNTNUM = U.vendor_account

# #     LEFT JOIN DIRPARTYTABLE DP WITH (NOLOCK)
# #         ON DP.RECID = VT.PARTY

# #     WHERE U.email_address = ? OR U.phone_number = ?
# #     """
# #     with get_connection() as conn:
# #         cur = conn.cursor()
# #         cur.execute(q, identifier, identifier)
# #         row = cur.fetchone()
# #         if not row:
# #             return None

# #         cols = [c[0].lower() for c in cur.description]
# #         return dict(zip(cols, row))
# # ─────────────────────────────────────────────
# # TOKEN TABLE
# # ─────────────────────────────────────────────
# def insert_token(user_id: int, token: str, token_type: str, expires_at: datetime):
#     q = """
#     INSERT INTO HIQ_VendorAuthToken 
#         (id, user_id, token, token_type, expires_at)
#     VALUES (?, ?, ?, ?, ?)
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, _new_id(), user_id, token,
#                     TOKEN_TYPE_MAP[token_type], expires_at)
#         conn.commit()


# def get_valid_token(token: str, token_type: str):
#     q = """
#     SELECT TOP 1 *
#     FROM HIQ_VendorAuthToken WITH (NOLOCK)
#     WHERE token      = ?
#       AND token_type = ?
#       AND is_used    = 0
#       AND expires_at >= GETUTCDATE()
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, token, TOKEN_TYPE_MAP[token_type])
#         row = cur.fetchone()
#         if not row:
#             return None
#         cols = [c[0].lower() for c in cur.description]
#         result = dict(zip(cols, row))
#         result["token_type"] = TOKEN_TYPE_REVERSE.get(
#             result["token_type"], result["token_type"]
#         )
#         return result


# def mark_token_used(token_id: int):
#     q = "UPDATE HIQ_VendorAuthToken SET is_used = 1 WHERE id = ?"
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, token_id)
#         conn.commit()


# # ─────────────────────────────────────────────
# # PASSWORD UPDATE
# # ─────────────────────────────────────────────
# def update_password(user_id: int, password_hash: str):
#     q = """
#     UPDATE HIQ_VendorPortalUser
#     SET password_hash   = ?,
#         password_set_at = GETDATE(),
#         status          = ?,
#         updated_at      = GETDATE()
#     WHERE id = ?
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, password_hash, STATUS_MAP["Active"], user_id)
#         conn.commit()


# # ─────────────────────────────────────────────
# # OTP TABLE
# # ─────────────────────────────────────────────
# def insert_otp(user_id: int, otp_code: str, expires_at: datetime):
#     q = """
#     INSERT INTO HIQ_VendorOtp 
#         (id, user_id, otp_code, expires_at)
#     VALUES (?, ?, ?, ?)
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, _new_id(), user_id, otp_code, expires_at)
#         conn.commit()


# def get_valid_otp(user_id: int, otp_code: str):
#     q = """
#     SELECT TOP 1 *
#     FROM HIQ_VendorOtp WITH (NOLOCK)
#     WHERE user_id  = ?
#       AND otp_code = ?
#       AND is_used  = 0
#       AND expires_at >= GETUTCDATE()
#       --GETDATE()
#     ORDER BY created_at DESC
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, user_id, otp_code)
#         row = cur.fetchone()
#         if not row:
#             return None
#         cols = [c[0].lower() for c in cur.description]
#         return dict(zip(cols, row))


# def mark_otp_used(otp_id: int):
#     q = "UPDATE HIQ_VendorOtp SET is_used = 1 WHERE id = ?"
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, otp_id)
#         conn.commit()


# # ─────────────────────────────────────────────
# # LOGIN LOG
# # ─────────────────────────────────────────────
# def log_login_d365(user_id, login_type, status, ip, error_msg):
#     import requests
#     from app.core.d365_auth import get_d365_token
#     from app.core.config import settings
#     from datetime import datetime

#     def fmt_datetime():
#         return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#     try:
#         token = get_d365_token()

#         body = {
#             "_request": {
#                 "requestType": 3, 
#                 "userId": user_id,
#                 "loginType": login_type,
#                 "status": status,
#                 "ipAddress": ip,
#                 "errorMessage": error_msg,
#                 "createdAt": fmt_datetime()
#             }
#         }

#         resp = requests.post(
#             settings.D365_VENDOR_RFQREPLY,
#             headers={
#                 "Authorization": f"Bearer {token}",
#                 "Content-Type": "application/json"
#             },
#             json=body,  
#             verify=False
#         )

#         print("LOGIN LOG STATUS:", resp.status_code)

#     except requests.exceptions.ConnectionError as e:
#         print(f"[LOGIN LOG FAILED] Connection error: {e}")

#     except requests.exceptions.Timeout:
#         print("[LOGIN LOG FAILED] Timeout")

#     except Exception as e:
#         print(f"[LOGIN LOG FAILED] Unexpected error: {e}")

#     # ✅ DO NOT raise anything
#     return

# # def log_login(user_id: int, method: str, status: str,
# #               ip: str = None, reason: str = None):
# #     q = """
# #     INSERT INTO HIQ_VendorLoginLog
# #         (id, user_id, login_method, status, ip_address, failure_reason)
# #     VALUES (?, ?, ?, ?, ?, ?)
# #     """
# #     method_int = LOGIN_METHOD_MAP.get(method, LOGIN_METHOD_MAP["PASSWORD"])
# #     status_int = LOGIN_STATUS_MAP.get(status, LOGIN_STATUS_MAP["FAILED"])
# #     with get_connection() as conn:
# #         cur = conn.cursor()
# #         cur.execute(q, _new_id(), user_id, method_int, status_int, ip or "", reason or "")
# #         conn.commit()

# # def mark_vendor_invited(vendor_account: str):
# #     q = """
# #     UPDATE VENDTABLE
# #     SET hiq_vendorcollaboration = 1
# #     WHERE ACCOUNTNUM = ?
# #     """
# #     with get_connection() as conn:
# #         cur = conn.cursor()
# #         cur.execute(q, vendor_account)
# #         conn.commit()
# STATUS_MAP = {
#     "Pending":  0,
#     "Active":   1,
#     "Inactive": 2
# }
# def get_portal_user(vendor_account: str):
#     q = """
#     SELECT TOP 1 *
#     FROM HIQ_VendorPortalUser WITH (NOLOCK)
#     WHERE vendor_account = ?
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, vendor_account)
#         row = cur.fetchone()
#         if not row:
#             return None
#         cols = [c[0].lower() for c in cur.description]
#         return dict(zip(cols, row))
    
# def insert_user_d365(vendor_account, email, phone):
#     token = get_d365_token()

#     user_id = _new_id()

#     body = {
#         "_request": {
#             "requestType": 2,
#             "id": int(user_id),
#             "vendorAccount": vendor_account,
#             "emailAddress": email,
#             "phoneNumber": phone,
#             "passwordHash": "",
#             "passwordSetAt": "1900-01-01 00:00:00.000",
#             "createdAt": fmt_datetime_ms(),
#             "updatedAt": "1900-01-01 00:00:00.000",
#             "googleId": "",
#             "googleEmail": "",
#             "PortalUserStatus": 0
#         }
#     }

#     print("==== INSERT USER DEBUG ====")
#     print("USER ID:", user_id)
#     print("REQUEST BODY:", body)

#     resp = requests.post(
#         settings.D365_VENDOR_RFQREPLY,
#         headers={
#             "Authorization": f"Bearer {token}",
#             "Content-Type": "application/json"
#         },
#         json=body,
#         verify=False
#     )

#     print("INSERT STATUS:", resp.status_code)
#     print("INSERT RESPONSE:", resp.text)
#     print("==========================")

#     if resp.status_code >= 400:
#         raise Exception(resp.text)

#     return user_id
# def update_user_d365(user_id, vendor_account, updates: dict):
#     token = get_d365_token()

#     body = {
#         "_request": {
#             "requestType": 2,
#             "id": int(user_id),
#             "vendorAccount": vendor_account,
#             **updates
#         }
#     }

#     print("==== UPDATE USER DEBUG ====")
#     print("USER ID:", user_id)
#     print("UPDATES:", updates)
#     print("REQUEST BODY:", body)

#     resp = requests.post(
#         settings.D365_VENDOR_RFQREPLY,
#         headers={
#             "Authorization": f"Bearer {token}",
#             "Content-Type": "application/json"
#         },
#         json=body,
#         verify=False
#     )

#     print("UPDATE STATUS:", resp.status_code)
#     print("UPDATE RESPONSE:", resp.text)
#     print("===========================")

#     if resp.status_code >= 400:
#         raise Exception(resp.text)

#     return resp.text
# def get_vendor_from_master(identifier: str):
#     q = """
#     SELECT TOP 1 
#         V.ACCOUNTNUM,
#         P.NAME,
#         E.LOCATOR AS email
#     FROM vendtable V

#     LEFT JOIN HIQ_vendorPostalADDRESSVIEW P
#         ON P.ACCOUNTNUM = V.ACCOUNTNUM
#         AND P.ISPRIMARY = 1

#     LEFT JOIN HIQ_vendorELECTRONICADDRESSVIEW E
#         ON E.ACCOUNTNUM = V.ACCOUNTNUM
#         AND E.ISPRIMARY1 = 1   -- ✅ IMPORTANT

#     WHERE 
#         LOWER(E.LOCATOR) = LOWER(?)   -- email match
#         OR V.ACCOUNTNUM = ?           -- vendor account match
#     """
#     with get_connection() as conn:
#         cur = conn.cursor()
#         cur.execute(q, identifier, identifier)
#         row = cur.fetchone()

#         if not row:
#             return None

#         return {
#             "accountnum": row[0],
#             "name": row[1],
#             "email": row[2]
#         }

