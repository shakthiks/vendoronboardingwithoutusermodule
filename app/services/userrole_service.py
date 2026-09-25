from __future__ import annotations

from typing import Any

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


USERS_TABLE = _table("HIQ_Users")
ROLES_TABLE = _table("HIQ_Roles")
USER_ROLES_TABLE = _table("HIQ_UserRoles")


def _to_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(
            f"{field_name} must be a valid integer."
        ) from exc

    if parsed <= 0:
        raise ValueError(
            f"{field_name} must be greater than zero."
        )

    return parsed


def assign_role(payload) -> dict[str, Any]:
    try:
        user_id = _to_int(
            payload.UserId,
            "UserId",
        )

        role_id = _to_int(
            payload.RoleId,
            "RoleId",
        )

        assigned_by = _to_int(
            payload.AssignedBy,
            "AssignedBy",
        )

    except Exception as exc:
        return {
            "status": False,
            "message": str(exc),
        }

    with get_secondary_connection() as conn:
        cursor = conn.cursor()

        try:
            # ====================================================
            # CHECK USER EXISTS
            # ====================================================
            cursor.execute(
                f"""
                SELECT TOP 1
                    UserId
                FROM {USERS_TABLE}
                WHERE UserId = ?
                  AND IsActive = 1
                """,
                (
                    user_id,
                ),
            )

            if not cursor.fetchone():
                return {
                    "status": False,
                    "message": "Active user not found.",
                }

            # ====================================================
            # CHECK ROLE EXISTS
            # ====================================================
            cursor.execute(
                f"""
                SELECT TOP 1
                    RoleId
                FROM {ROLES_TABLE}
                WHERE RoleId = ?
                  AND IsActive = 1
                """,
                (
                    role_id,
                ),
            )

            if not cursor.fetchone():
                return {
                    "status": False,
                    "message": "Active role not found.",
                }

            # ====================================================
            # CHECK ASSIGNED BY USER EXISTS
            # ====================================================
            cursor.execute(
                f"""
                SELECT TOP 1
                    UserId
                FROM {USERS_TABLE}
                WHERE UserId = ?
                  AND IsActive = 1
                """,
                (
                    assigned_by,
                ),
            )

            if not cursor.fetchone():
                return {
                    "status": False,
                    "message": "AssignedBy user not found.",
                }

            # ====================================================
            # CHECK DUPLICATE ROLE ASSIGNMENT
            # ====================================================
            cursor.execute(
                f"""
                SELECT TOP 1
                    UserRoleId
                FROM {USER_ROLES_TABLE}
                WHERE UserId = ?
                  AND RoleId = ?
                """,
                (
                    user_id,
                    role_id,
                ),
            )

            if cursor.fetchone():
                return {
                    "status": False,
                    "message": "Role already assigned to this user.",
                }

            # ====================================================
            # INSERT USER ROLE
            # ====================================================
            cursor.execute(
                f"""
                INSERT INTO {USER_ROLES_TABLE}
                (
                    UserId,
                    RoleId,
                    AssignedOn,
                    AssignedBy
                )
                VALUES
                (
                    ?,
                    ?,
                    SYSUTCDATETIME(),
                    ?
                )
                """,
                (
                    user_id,
                    role_id,
                    assigned_by,
                ),
            )

            conn.commit()

            return {
                "status": True,
                "message": "Role assigned successfully.",
            }

        except Exception as exc:
            conn.rollback()

            return {
                "status": False,
                "message": str(exc),
            }

        finally:
            cursor.close()