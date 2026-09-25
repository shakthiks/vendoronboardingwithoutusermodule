from __future__ import annotations

from typing import Any

import bcrypt

from app.core.config import settings
from app.db.base import get_secondary_connection


# ============================================================
# DB SCHEMA FROM CONFIG ONLY
# ============================================================

def _get_db_schema_from_settings() -> str:
    schema = (
        getattr(settings, "SECONDARY_DB_SCHEMA", None)
        or getattr(settings, "DB_SCHEMA", None)
    )

    if schema is None or not str(schema).strip():
        raise RuntimeError(
            "Database schema is missing. "
            "Set SECONDARY_DB_SCHEMA or DB_SCHEMA in config."
        )

    return str(schema).strip().strip("[]")


DB_SCHEMA = _get_db_schema_from_settings()


def _table(table_name: str) -> str:
    schema = DB_SCHEMA.strip().strip("[]")
    table = table_name.strip().strip("[]")

    return f"[{schema}].[{table}]"


USERS_TABLE = _table(
    "HIQ_Users"
)

ROLES_TABLE = _table(
    "HIQ_Roles"
)


# ============================================================
# HELPERS
# ============================================================

def _clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _bool_to_bit(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0

    if isinstance(value, int):
        return 1 if value else 0

    text = str(value or "").strip().lower()

    return 1 if text in {"1", "true", "yes", "y"} else 0


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


# ============================================================
# CREATE / UPDATE USER
# ============================================================

def create_users(payload) -> dict[str, Any]:
    user_id = getattr(
        payload,
        "UserId",
        None,
    )

    username = _clean_text(
        getattr(
            payload,
            "Username",
            None,
        )
    )

    email = _clean_text(
        getattr(
            payload,
            "Email",
            None,
        )
    )

    full_name = _clean_text(
        getattr(
            payload,
            "FullName",
            None,
        )
    )

    password = _clean_text(
        getattr(
            payload,
            "hashed_password",
            None,
        )
    )

    is_active = _bool_to_bit(
        getattr(
            payload,
            "IsActive",
            True,
        )
    )

    failed_login_count = int(
        getattr(
            payload,
            "FailedLoginCount",
            0,
        )
        or 0
    )

    is_locked = _bool_to_bit(
        getattr(
            payload,
            "IsLocked",
            False,
        )
    )

    if not username:
        return {
            "status": False,
            "message": "Username is required.",
        }

    if not email:
        return {
            "status": False,
            "message": "Email is required.",
        }

    if not full_name:
        return {
            "status": False,
            "message": "FullName is required.",
        }

    with get_secondary_connection() as secondary_conn:
        secondary_cursor = secondary_conn.cursor()

        try:
            # ====================================================
            # UPDATE USER
            # ====================================================
            if user_id:
                secondary_cursor.execute(
                    f"""
                    SELECT TOP 1
                        UserId
                    FROM {USERS_TABLE}
                    WHERE UserId = ?
                    """,
                    (
                        user_id,
                    ),
                )

                if not secondary_cursor.fetchone():
                    return {
                        "status": False,
                        "message": "User not found.",
                    }

                # Check duplicate username for another user
                secondary_cursor.execute(
                    f"""
                    SELECT TOP 1
                        UserId
                    FROM {USERS_TABLE}
                    WHERE UPPER(LTRIM(RTRIM(Username))) =
                          UPPER(LTRIM(RTRIM(?)))
                      AND UserId <> ?
                    """,
                    (
                        username,
                        user_id,
                    ),
                )

                if secondary_cursor.fetchone():
                    return {
                        "status": False,
                        "message": "Username already exists.",
                    }

                # Check duplicate email for another user
                secondary_cursor.execute(
                    f"""
                    SELECT TOP 1
                        UserId
                    FROM {USERS_TABLE}
                    WHERE UPPER(LTRIM(RTRIM(Email))) =
                          UPPER(LTRIM(RTRIM(?)))
                      AND UserId <> ?
                    """,
                    (
                        email,
                        user_id,
                    ),
                )

                if secondary_cursor.fetchone():
                    return {
                        "status": False,
                        "message": "Email already exists.",
                    }

                if password:
                    hashed_password = _hash_password(
                        password
                    )

                    secondary_cursor.execute(
                        f"""
                        UPDATE {USERS_TABLE}
                        SET
                            Username = ?,
                            Email = ?,
                            PasswordHash = ?,
                            FullName = ?,
                            IsActive = ?,
                            FailedLoginCount = ?,
                            IsLocked = ?,
                            ModifiedOn = SYSUTCDATETIME()
                        WHERE UserId = ?
                        """,
                        (
                            username,
                            email,
                            hashed_password,
                            full_name,
                            is_active,
                            failed_login_count,
                            is_locked,
                            user_id,
                        ),
                    )

                else:
                    secondary_cursor.execute(
                        f"""
                        UPDATE {USERS_TABLE}
                        SET
                            Username = ?,
                            Email = ?,
                            FullName = ?,
                            IsActive = ?,
                            FailedLoginCount = ?,
                            IsLocked = ?,
                            ModifiedOn = SYSUTCDATETIME()
                        WHERE UserId = ?
                        """,
                        (
                            username,
                            email,
                            full_name,
                            is_active,
                            failed_login_count,
                            is_locked,
                            user_id,
                        ),
                    )

                secondary_conn.commit()

                return {
                    "status": True,
                    "message": "User updated successfully.",
                }

            # ====================================================
            # CREATE USER
            # ====================================================
            if not password:
                return {
                    "status": False,
                    "message": "Password is required.",
                }

            secondary_cursor.execute(
                f"""
                SELECT TOP 1
                    UserId
                FROM {USERS_TABLE}
                WHERE UPPER(LTRIM(RTRIM(Username))) =
                      UPPER(LTRIM(RTRIM(?)))
                """,
                (
                    username,
                ),
            )

            if secondary_cursor.fetchone():
                return {
                    "status": False,
                    "message": "Username already exists.",
                }

            secondary_cursor.execute(
                f"""
                SELECT TOP 1
                    UserId
                FROM {USERS_TABLE}
                WHERE UPPER(LTRIM(RTRIM(Email))) =
                      UPPER(LTRIM(RTRIM(?)))
                """,
                (
                    email,
                ),
            )

            if secondary_cursor.fetchone():
                return {
                    "status": False,
                    "message": "Email already exists.",
                }

            hashed_password = _hash_password(
                password
            )

            secondary_cursor.execute(
                f"""
                INSERT INTO {USERS_TABLE}
                (
                    Username,
                    Email,
                    PasswordHash,
                    FullName,
                    IsActive,
                    FailedLoginCount,
                    IsLocked,
                    CreatedOn,
                    ModifiedOn
                )
                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    SYSUTCDATETIME(),
                    SYSUTCDATETIME()
                )
                """,
                (
                    username,
                    email,
                    hashed_password,
                    full_name,
                    is_active,
                    failed_login_count,
                    is_locked,
                ),
            )

            secondary_conn.commit()

            return {
                "status": True,
                "message": "User created successfully.",
            }

        except Exception as exc:
            secondary_conn.rollback()

            return {
                "status": False,
                "message": str(exc),
            }

        finally:
            secondary_cursor.close()