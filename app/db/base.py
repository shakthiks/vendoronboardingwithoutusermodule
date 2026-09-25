
from __future__ import annotations
import pyodbc
from contextlib import contextmanager
from app.core.config import settings          # ← import settings object, not variables

from contextlib import contextmanager
from typing import Any, Generator
 
import pyodbc
 
from app.core.config import settings
pyodbc.pooling = True


# ── VENDORPORTAL DB (your 3 portal tables) ────────────────────
CONNECTION_STRING = (
    f"DSN={settings.SQL_DSN};"
    f"UID={settings.SQL_USERNAME};"
    f"PWD={settings.SQL_PASSWORD};"
    "TrustServerCertificate=yes;"
)
# VENDOR_CONNECTION_STRING = (
#     f"DSN={settings.VSQL_DSN};"
#     f"UID={settings.SQL_USERNAME};"
#     f"PWD={settings.SQL_PASSWORD};"
#     "TrustServerCertificate=yes;"
# )
VENDOR_DB_CONNECTION = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={settings.VENDOR_DB_SERVER};"
    f"DATABASE={settings.VENDOR_DB_NAME};"
    f"UID={settings.VENDOR_DB_USER};"
    f"PWD={settings.VENDOR_DB_PASSWORD};"
    "TrustServerCertificate=yes;"
)
# ── D365 SQL Server (direct read + write bids) ────────────────
D365_CONNECTION_STRING = (
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={settings.D365_DB_SERVER};"
    f"DATABASE={settings.D365_DB_NAME};"
    f"UID={settings.D365_DB_USER};"
    f"PWD={settings.D365_DB_PASSWORD};"
    "TrustServerCertificate=yes;"
    "Connection Timeout=60;"
)



@contextmanager
def get_connection():
    """VENDORPORTAL DB — vendor_portal_user, sync_log, notification_log"""
    conn = None
    try:
        conn = pyodbc.connect(VENDOR_DB_CONNECTION,timeout=10)
        yield conn
    except Exception as e:
        raise Exception(f"[VENDORPORTAL] Connection failed: {str(e)}")
    finally:
        if conn:
            conn.close()


@contextmanager
def get_d365_connection():
    """D365 SQL Server — read RFQ/PO/vendor + write bids"""
    conn = None
    try:
        conn = pyodbc.connect(CONNECTION_STRING, timeout=10)
        yield conn
    except Exception as e:
        raise Exception(f"[D365] Connection failed: {str(e)}")
    finally:
        if conn:
            conn.close()


def rows_to_dict(cursor) -> list[dict]:
    """Converts all cursor rows → list of dicts with lowercase keys."""
    if not cursor.description:
        return []
    cols = [col[0].lower() for col in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def row_to_dict(cursor) -> dict | None:
    """Converts single cursor row → dict. Returns None if not found."""
    if not cursor.description:
        return None
    cols = [col[0].lower() for col in cursor.description]
    row  = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


 

 
def _build_connection_string(
    server: str,
    database: str,
    username: str,
    password: str,
    driver: str = "ODBC Driver 17 for SQL Server",
) -> str:
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=30;"
    )
 
 
@contextmanager
def get_connection() -> Generator[
    pyodbc.Connection,
    None,
    None,
]:
    """Primary Vendor Portal database connection."""
 
    connection: pyodbc.Connection | None = None
 
    try:
        connection = pyodbc.connect(
            _build_connection_string(
                server=settings.VENDOR_DB_SERVER,
                database=settings.VENDOR_DB_NAME,
                username=settings.VENDOR_DB_USER,
                password=settings.VENDOR_DB_PASSWORD,
            ),
            timeout=30,
        )
 
    except pyodbc.Error as exc:
        print(
            "[VENDORPORTAL] Database connection failed: "
            f"{exc}"
        )
        raise
 
    try:
        yield connection
 
    except Exception:
        try:
            connection.rollback()
        except pyodbc.Error:
            pass
        raise
 
    finally:
        try:
            connection.close()
        except pyodbc.Error:
            pass
 
 
@contextmanager
def get_secondary_connection() -> Generator[
    pyodbc.Connection,
    None,
    None,
]:
    """Secondary Vendor Portal database connection."""
 
    connection: pyodbc.Connection | None = None
 
    try:
        connection = pyodbc.connect(
            _build_connection_string(
                server=settings.SECONDARY_DB_SERVER,
                database=settings.SECONDARY_DB_NAME,
                username=settings.SECONDARY_DB_USER,
                password=settings.SECONDARY_DB_PASSWORD,
            ),
            timeout=30,
        )
 
    except pyodbc.Error as exc:
        print(
            "[SECONDARY DB] Database connection failed: "
            f"{exc}"
        )
        raise
 
    try:
        yield connection
 
    except Exception:
        try:
            connection.rollback()
        except pyodbc.Error:
            pass
        raise
 
    finally:
        try:
            connection.close()
        except pyodbc.Error:
            pass
 
 
def rows_to_dict(
    cursor: pyodbc.Cursor,
) -> list[dict[str, Any]]:
    if not cursor.description:
        return []
 
    columns = [
        column[0].lower()
        for column in cursor.description
    ]
 
    return [
        dict(zip(columns, row))
        for row in cursor.fetchall()
    ]
 
 
def row_to_dict(
    cursor: pyodbc.Cursor,
) -> dict[str, Any] | None:
    if not cursor.description:
        return None
 
    row = cursor.fetchone()
 
    if row is None:
        return None
 
    columns = [
        column[0].lower()
        for column in cursor.description
    ]
 
    return dict(zip(columns, row))
 
 
def get_database_info(
    connection: pyodbc.Connection,
) -> dict[str, Any]:
    cursor = connection.cursor()
 
    try:
        cursor.execute(
            """
            SELECT
                @@SERVERNAME AS ServerName,
                DB_NAME() AS DatabaseName,
                USER_NAME() AS LoginUser
            """
        )
 
        row = cursor.fetchone()
 
        return {
            "server_name": row[0],
            "database_name": row[1],
            "login_user": row[2],
        }
 
    finally:
        cursor.close()

# # ── Reuse EXACT same pattern as vendorportal ─────────────────────
# import pyodbc
# from contextlib import contextmanager
# from app.core.config import settings

# pyodbc.pooling = True

# # ── VENDORPORTAL DB ──────────────────────────────────────────────
# CONNECTION_STRING = (
#     f"DSN={settings.SQL_DSN};"
#     f"UID={settings.SQL_USERNAME};"
#     f"PWD={settings.SQL_PASSWORD};"
#     "TrustServerCertificate=yes;"
# )

# # ── D365 SQL Server (vendor list lives here) ─────────────────────
# D365_CONNECTION_STRING = (
#     f"DRIVER={{ODBC Driver 17 for SQL Server}};"
#     f"SERVER={settings.D365_DB_SERVER};"
#     f"DATABASE={settings.D365_DB_NAME};"
#     f"UID={settings.D365_DB_USER};"
#     f"PWD={settings.D365_DB_PASSWORD};"
#     "TrustServerCertificate=yes;"
#     "Connection Timeout=60;"
# )

# @contextmanager
# def get_connection():
#     """VENDORPORTAL DB"""
#     conn = None
#     try:
#         conn = pyodbc.connect(D365_CONNECTION_STRING, timeout=5)
#         yield conn
#     except Exception as e:
#         raise Exception(f"[VENDORPORTAL] Connection failed: {str(e)}")
#     finally:
#         if conn:
#             conn.close()

# @contextmanager
# def get_d365_connection():
#     """D365 SQL — VendTable + DirPartyTable + PostalAddress"""
#     conn = None
#     try:
#         conn = pyodbc.connect(D365_CONNECTION_STRING, timeout=10)
#         yield conn
#     except Exception as e:
#         raise Exception(f"[D365] Connection failed: {str(e)}")
#     finally:
#         if conn:
#             conn.close()

# def rows_to_dict(cursor) -> list[dict]:
#     if not cursor.description:
#         return []
#     cols = [col[0].lower() for col in cursor.description]
#     return [dict(zip(cols, row)) for row in cursor.fetchall()]

# def row_to_dict(cursor) -> dict | None:
#     if not cursor.description:
#         return None
#     cols = [col[0].lower() for col in cursor.description]
#     row  = cursor.fetchone()
#     return dict(zip(cols, row)) if row else None