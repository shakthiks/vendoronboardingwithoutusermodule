from typing import Any

from app.core.config import settings
from app.db.base import get_connection


DB_SCHEMA = getattr(
    settings,
    "SECONDARY_DB_SCHEMA",
    settings.DB_SCHEMA,
)

TERMS_VERSION_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_TermsAndConditionsVersion]"
)


def get_latest_terms_pdf(
    terms_version_id: int | None = None,
) -> dict[str, Any] | None:
    """
    Fetch a Terms and Conditions PDF as raw bytes.

    Behaviour:
    - When terms_version_id is supplied, fetch that exact version,
      whether it is active or inactive.
    - When terms_version_id is omitted, fetch the latest active version.

    Returns:
        PDF metadata and raw bytes, or None when no matching row exists.
    """

    if terms_version_id is not None:
        try:
            normalized_version_id = int(terms_version_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "TERMSVERSIONID must be a valid number."
            ) from exc

        if normalized_version_id <= 0:
            raise ValueError(
                "TERMSVERSIONID must be greater than zero."
            )
    else:
        normalized_version_id = None

    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            if normalized_version_id is not None:
                cursor.execute(
                    f"""
                    SELECT TOP 1
                        TermsVersionId,
                        VersionNumber,
                        FileName,
                        ContentType,
                        PdfFileBytes,
                        IsActive,
                        CreatedOn
                    FROM {TERMS_VERSION_TABLE}
                    WHERE TermsVersionId = ?
                    """,
                    (normalized_version_id,),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT TOP 1
                        TermsVersionId,
                        VersionNumber,
                        FileName,
                        ContentType,
                        PdfFileBytes,
                        IsActive,
                        CreatedOn
                    FROM {TERMS_VERSION_TABLE}
                    WHERE IsActive = 1
                    ORDER BY
                        CreatedOn DESC,
                        TermsVersionId DESC
                    """
                )

            row = cursor.fetchone()

            if not row or row.PdfFileBytes is None:
                return None

            file_bytes = bytes(row.PdfFileBytes)

            return {
                "terms_version_id": int(row.TermsVersionId),
                "version_number": row.VersionNumber,
                "file_name": row.FileName,
                "content_type": (
                    row.ContentType or "application/pdf"
                ),
                "file_size_bytes": len(file_bytes),
                "file_bytes": file_bytes,
                "is_active": bool(row.IsActive),
                "created_on": row.CreatedOn,
            }

        finally:
            cursor.close()
