from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.base import get_connection


def _setting_text(
    *names: str,
    default: str = "dev",
) -> str:
    for name in names:
        value = getattr(settings, name, None)

        if value is not None and str(value).strip():
            return str(value).strip()

    return default


# ============================================================
# CLOUD DATABASE SCHEMA
# ============================================================

CLOUD_DB_SCHEMA = _setting_text(
    "DB_SCHEMA",
    default="dev",
)


def _table(table_name: str) -> str:
    schema = CLOUD_DB_SCHEMA.strip().strip("[]")
    table = table_name.strip().strip("[]")

    return f"[{schema}].[{table}]"


# ============================================================
# CLOUD TABLES
# ============================================================

REGISTRATION_TABLE = _table(
    "HIQ_VendorRegistration"
)

PROSPECT_TABLE = _table(
    "d365_VendorProspect"
)


def normalize_status(status: Any) -> str:
    if status is None:
        return "UNKNOWN"

    text = str(status).strip()

    if not text:
        return "UNKNOWN"

    return text.upper()


def _serialize(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()

    return value


def get_prospect_list_sync() -> dict[str, Any]:

    query = f"""
        SELECT
            r.ProspectId,
            v.VendorAccount,
            r.CompanyName,
            v.Email,
            r.State,
            r.DateOfSubmission,

            CASE
                WHEN UPPER(LTRIM(RTRIM(r.Status))) = N'DRAFT'
                     AND ISNULL(r.IsDraft, 0) = 0
                THEN N'INVITED'
                ELSE r.Status
            END AS Status

        FROM {REGISTRATION_TABLE} AS r

        INNER JOIN {PROSPECT_TABLE} AS v
            ON r.ProspectId = v.ProspectId

        ORDER BY r.ProspectId DESC
    """

    # CLOUD DATABASE CONNECTION
    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(query)

            rows = cursor.fetchall()

            if cursor.description is None:
                return {
                    "all_prospects": [],
                }

            cols = [
                column[0].lower()
                for column in cursor.description
            ]

            prospects = []

            for row in rows:
                item = dict(zip(cols, row))

                for key, value in list(item.items()):
                    item[key] = _serialize(value)

                prospects.append(item)

            response: dict[str, Any] = {
                "all_prospects": prospects,
            }

            for prospect in prospects:
                status_key = normalize_status(
                    prospect.get("status")
                )

                response.setdefault(
                    status_key,
                    [],
                )

                response[status_key].append(
                    prospect
                )

            return response

        finally:
            cursor.close()


async def get_prospect_list() -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            get_prospect_list_sync
        )

    except Exception as exc:
        raise Exception(
            f"[PROSPECTLIST] Cloud connection failed: {str(exc)}"
        ) from exc