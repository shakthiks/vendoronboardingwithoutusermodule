from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.schemas.vendgroupdropdown_schema import VendorGroupDropdown


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


VENDGROUP_TABLE = _table(
    "HIQ_VendGroup"
)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def get_vendor_group_dropdown(conn) -> list[VendorGroupDropdown]:
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"""
            SELECT
                VendGroup,
                Description
            FROM {VENDGROUP_TABLE}
            WHERE VendGroup IS NOT NULL
            ORDER BY VendGroup
            """
        )

        rows = cursor.fetchall()

        return [
            VendorGroupDropdown(
                VendGroup=_clean_text(row[0]),
                Description=_clean_text(row[1]),
            )
            for row in rows
            if _clean_text(row[0]) is not None
        ]

    finally:
        cursor.close()