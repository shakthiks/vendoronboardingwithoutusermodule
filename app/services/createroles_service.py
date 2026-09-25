from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.db.base import get_secondary_connection


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


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def create_role(payload) -> dict[str, Any]:
    role_name = _clean_text(
        payload.RoleName
    )

    role_description = _clean_text(
        getattr(
            payload,
            "RoleDescription",
            None,
        )
    )

    is_active = bool(
        getattr(
            payload,
            "IsActive",
            True,
        )
    )

    if not role_name:
        return {
            "status": False,
            "message": "RoleName is required.",
        }

    with get_secondary_connection() as secondary_conn:
        secondary_cursor = secondary_conn.cursor()

        try:
            secondary_cursor.execute(
                f"""
                SELECT TOP 1
                    RoleId
                FROM {ROLES_TABLE}
                WHERE UPPER(LTRIM(RTRIM(RoleName))) =
                      UPPER(LTRIM(RTRIM(?)))
                """,
                (
                    role_name,
                ),
            )

            if secondary_cursor.fetchone():
                return {
                    "status": False,
                    "message": "Role already exists.",
                }

            secondary_cursor.execute(
                f"""
                INSERT INTO {ROLES_TABLE}
                (
                    RoleName,
                    Description,
                    IsActive,
                    CreatedOn
                )
                VALUES
                (
                    ?,
                    ?,
                    ?,
                    SYSUTCDATETIME()
                )
                """,
                (
                    role_name,
                    role_description,
                    1 if is_active else 0,
                ),
            )

            secondary_conn.commit()

            return {
                "status": True,
                "message": "Role created successfully.",
            }

        except Exception as exc:
            secondary_conn.rollback()

            return {
                "status": False,
                "message": str(exc),
            }

        finally:
            secondary_cursor.close()