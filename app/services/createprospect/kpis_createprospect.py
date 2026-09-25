from __future__ import annotations

from typing import Any
import logging

from fastapi import status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.base import get_connection


logger = logging.getLogger(__name__)


# ============================================================
# CLOUD DB SCHEMA FROM CONFIG ONLY
# ============================================================

def _get_db_schema_from_settings() -> str:
    schema = getattr(
        settings,
        "DB_SCHEMA",
        None,
    )

    if schema is None or not str(schema).strip():
        raise RuntimeError(
            "Database schema is missing. "
            "Set DB_SCHEMA in config."
        )

    return str(schema).strip().strip("[]")


DB_SCHEMA = _get_db_schema_from_settings()


def _table(table_name: str) -> str:
    schema = DB_SCHEMA.strip().strip("[]")
    table = table_name.strip().strip("[]")

    return f"[{schema}].[{table}]"


PROSPECT_TABLE = _table(
    "d365_VendorProspect"
)

VENDOR_REGISTRATION_TABLE = _table(
    "HIQ_VendorRegistration"
)


# ============================================================
# DASHBOARD KPIs
# ============================================================

def get_dashboard_kpis():
    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            # Open prospects
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {PROSPECT_TABLE}
                WHERE VendorAccount IS NULL
                   OR LTRIM(RTRIM(VendorAccount)) = ''
                """
            )

            open_prospects = int(
                cursor.fetchone()[0] or 0
            )

            # Awaiting risk review
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {VENDOR_REGISTRATION_TABLE}
                WHERE UPPER(LTRIM(RTRIM(Status))) = N'TO_EVALUATE'
                """
            )

            awaiting_risk_review = int(
                cursor.fetchone()[0] or 0
            )

            # Pending approvals
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {VENDOR_REGISTRATION_TABLE}
                WHERE UPPER(LTRIM(RTRIM(Status))) = N'IN_APPROVAL'
                """
            )

            pending_approvals = int(
                cursor.fetchone()[0] or 0
            )

            # Approved / vendor created
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {VENDOR_REGISTRATION_TABLE}
                WHERE UPPER(LTRIM(RTRIM(Status))) = N'VENDOR_CREATED'
                """
            )

            approved_prospects = int(
                cursor.fetchone()[0] or 0
            )

            # Rejected prospects
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {VENDOR_REGISTRATION_TABLE}
                WHERE UPPER(LTRIM(RTRIM(Status))) = N'REJECTED'
                """
            )

            rejected_prospects = int(
                cursor.fetchone()[0] or 0
            )

            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": True,
                    "message": (
                        "Dashboard KPIs fetched successfully."
                    ),
                    "data": {
                        "OpenProspects": open_prospects,
                        "AwaitingRiskReview": awaiting_risk_review,
                        "PendingApprovals": pending_approvals,
                        "ApprovedProspects": approved_prospects,
                        "RejectedProspects": rejected_prospects,
                    },
                },
            )

        except Exception as exc:
            logger.exception(
                "[DASHBOARD KPIS] Failed to fetch dashboard KPIs"
            )

            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": False,
                    "message": str(exc),
                },
            )

        finally:
            cursor.close()