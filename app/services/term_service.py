from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile
from pathlib import Path
from typing import Any

import pyodbc

from app.core.config import settings
from app.db.base import get_connection
from app.schemas.term_schema import TermsVersionUploadRequest,TermsFileFetchRequest


DB_SCHEMA = settings.DB_SCHEMA

TERMS_VERSION_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_TermsAndConditionsVersion]"
)

FILE_BYTES_COLUMN = "PdfFileBytes"

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

PDF_CONTENT_TYPE = "application/pdf"

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)

ALLOWED_FILE_TYPES = {
    ".pdf": PDF_CONTENT_TYPE,
    ".docx": DOCX_CONTENT_TYPE,
}



def _safe_file_name(file_name: str) -> str:
    cleaned = Path(
        str(file_name or "").strip()
    ).name

    if not cleaned:
        raise ValueError("FILE_NAME is required.")

    if len(cleaned) > 255:
        raise ValueError(
            "FILE_NAME cannot exceed 255 characters."
        )

    return cleaned


def _extract_base64_content(
    base64_value: str,
) -> str:
    if not base64_value or not base64_value.strip():
        raise ValueError("FILE_BASE64 is required.")

    encoded_value = base64_value.strip()

    if encoded_value.lower().startswith("data:"):
        header, separator, content = (
            encoded_value.partition(",")
        )

        if not separator:
            raise ValueError(
                "Invalid Base64 data URL format."
            )

        if ";base64" not in header.lower():
            raise ValueError(
                "The data URL is not Base64 encoded."
            )

        encoded_value = content

    encoded_value = re.sub(
        r"\s+",
        "",
        encoded_value,
    )

    if not encoded_value:
        raise ValueError(
            "FILE_BASE64 does not contain file content."
        )

    missing_padding = len(encoded_value) % 4

    if missing_padding:
        encoded_value += "=" * (
            4 - missing_padding
        )

    return encoded_value


def _validate_pdf(file_bytes: bytes) -> None:
    if b"%PDF-" not in file_bytes[:1024]:
        raise ValueError(
            "The decoded content is not a valid PDF file."
        )


def _validate_docx(file_bytes: bytes) -> None:
    try:
        with zipfile.ZipFile(
            io.BytesIO(file_bytes)
        ) as archive:
            names = set(
                archive.namelist()
            )

            required_files = {
                "[Content_Types].xml",
                "word/document.xml",
            }

            if not required_files.issubset(names):
                raise ValueError(
                    "The decoded content is not a valid DOCX file."
                )

    except zipfile.BadZipFile as exc:
        raise ValueError(
            "The decoded content is not a valid DOCX file."
        ) from exc


def decode_uploaded_file(
    file_name: str,
    file_base64: str,
) -> tuple[str, str, str, bytes]:
    safe_file_name = _safe_file_name(
        file_name
    )

    extension = Path(
        safe_file_name
    ).suffix.lower()

    if extension not in ALLOWED_FILE_TYPES:
        raise ValueError(
            "FILE_NAME must end with .pdf or .docx."
        )

    encoded_value = _extract_base64_content(
        file_base64
    )

    try:
        file_bytes = base64.b64decode(
            encoded_value,
            validate=True,
        )

    except (
        binascii.Error,
        ValueError,
    ) as exc:
        raise ValueError(
            "FILE_BASE64 contains invalid Base64 content."
        ) from exc

    if not file_bytes:
        raise ValueError(
            "The decoded file is empty."
        )

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            "The file exceeds the maximum allowed size "
            "of 10 MB."
        )

    if extension == ".pdf":
        _validate_pdf(file_bytes)

    elif extension == ".docx":
        _validate_docx(file_bytes)

    content_type = ALLOWED_FILE_TYPES[
        extension
    ]

    return (
        safe_file_name,
        extension.lstrip("."),
        content_type,
        file_bytes,
    )


def create_terms_version(
    payload: TermsVersionUploadRequest,
) -> dict[str, Any]:
    file_base64 = (
        payload.FILE_BASE64
        or payload.PDF_BASE64
    )

    if not file_base64:
        raise ValueError(
            "FILE_BASE64 is required."
        )

    (
        file_name,
        file_extension,
        content_type,
        file_bytes,
    ) = decode_uploaded_file(
        file_name=payload.FILE_NAME,
        file_base64=file_base64,
    )

    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;"
            )

            cursor.execute(
                f"""
                SELECT ISNULL(
                    MAX(
                        TRY_CONVERT(
                            INT,
                            REPLACE(
                                VersionNumber,
                                'V',
                                ''
                            )
                        )
                    ),
                    0
                )
                FROM {TERMS_VERSION_TABLE}
                    WITH (UPDLOCK, HOLDLOCK)
                """
            )

            row = cursor.fetchone()

            current_version = (
                int(row[0])
                if row and row[0] is not None
                else 0
            )

            version_number = (
                f"V{current_version + 1:03d}"
            )

            cursor.execute(
                f"""
                UPDATE {TERMS_VERSION_TABLE}
                SET IsActive = 0
                WHERE IsActive = 1
                """
            )

            cursor.execute(
                f"""
                INSERT INTO {TERMS_VERSION_TABLE}
                (
                    VersionNumber,
                    FileName,
                    ContentType,
                    {FILE_BYTES_COLUMN},
                    IsActive,
                    CreatedByUserId,
                    CreatedOn
                )
                OUTPUT
                    INSERTED.TermsVersionId,
                    INSERTED.VersionNumber,
                    INSERTED.FileName,
                    INSERTED.ContentType,
                    INSERTED.IsActive,
                    INSERTED.CreatedOn
                VALUES
                (
                    ?,
                    ?,
                    ?,
                    ?,
                    1,
                    ?,
                    SYSUTCDATETIME()
                )
                """,
                version_number,
                file_name,
                content_type,
                pyodbc.Binary(file_bytes),
                payload.CREATED_BY_USER_ID,
            )

            inserted = cursor.fetchone()

            if inserted is None:
                raise RuntimeError(
                    "Terms and Conditions version "
                    "was not created."
                )

            conn.commit()

            return {
                "SUCCESS": True,
                "MESSAGE": (
                    "Terms and Conditions version "
                    "uploaded successfully."
                ),
                "TERMSVERSIONID": int(
                    inserted.TermsVersionId
                ),
                "VERSIONNUMBER": (
                    inserted.VersionNumber
                ),
                "FILENAME": inserted.FileName,
                "FILEEXTENSION": file_extension,
                "CONTENTTYPE": inserted.ContentType,
                "FILESIZEBYTES": len(file_bytes),
                "ISACTIVE": bool(
                    inserted.IsActive
                ),
                "CREATEDON": (
                    inserted.CreatedOn.isoformat()
                    if inserted.CreatedOn
                    else None
                ),
            }

        except Exception:
            conn.rollback()
            raise

        finally:
            cursor.close()


def get_terms_file(
    terms_version_id: int | None = None,
) -> dict[str, Any] | None:
    if (
        terms_version_id is not None
        and terms_version_id <= 0
    ):
        raise ValueError(
            "TERMSVERSIONID must be greater than zero."
        )

    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            if terms_version_id is not None:
                cursor.execute(
                    f"""
                    SELECT
                        TermsVersionId,
                        VersionNumber,
                        FileName,
                        ContentType,
                        DATALENGTH(
                            {FILE_BYTES_COLUMN}
                        ) AS FileSizeBytes,
                        {FILE_BYTES_COLUMN} AS FileBytes,
                        IsActive,
                        CreatedOn
                    FROM {TERMS_VERSION_TABLE}
                    WHERE TermsVersionId = ?
                    """,
                    terms_version_id,
                )

            else:
                cursor.execute(
                    f"""
                    SELECT TOP (1)
                        TermsVersionId,
                        VersionNumber,
                        FileName,
                        ContentType,
                        DATALENGTH(
                            {FILE_BYTES_COLUMN}
                        ) AS FileSizeBytes,
                        {FILE_BYTES_COLUMN} AS FileBytes,
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

            if row is None:
                return None

            if row.FileBytes is None:
                return None

            raw_bytes = bytes(
                row.FileBytes
            )

            return {
                "terms_version_id": int(
                    row.TermsVersionId
                ),
                "version_number": row.VersionNumber,
                "file_name": row.FileName,
                "content_type": (
                    row.ContentType
                    or "application/octet-stream"
                ),
                "file_size_bytes": int(
                    row.FileSizeBytes
                    or len(raw_bytes)
                ),
                "file_bytes": raw_bytes,
                "is_active": bool(
                    row.IsActive
                ),
                "created_on": row.CreatedOn,
            }

        finally:
            cursor.close()
