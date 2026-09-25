from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.db.base import get_connection
from app.schemas.vendoroboardingattach_schema import (
    AttachmentContentRequest,
)


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_SCHEMA = settings.DB_SCHEMA

ATTACHMENT_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorFileAttachment]"
)


# ============================================================
# EXCEPTIONS
# ============================================================

class AttachmentNotFoundError(LookupError):
    pass


class AttachmentMetadataMismatchError(ValueError):
    pass


class AttachmentContentMissingError(LookupError):
    pass


# ============================================================
# HELPERS
# ============================================================

NULL_TEXT_VALUES = {
    "",
    "null",
    "none",
    "undefined",
    "n/a",
}


def _normalize_optional_text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    normalized = str(
        value
    ).strip()

    if normalized.lower() in NULL_TEXT_VALUES:
        return None

    return normalized


def _convert_to_bytes(
    value: Any,
) -> bytes:
    """
    Convert SQL Server VARBINARY value to Python bytes.
    """

    if value is None:
        raise AttachmentContentMissingError(
            "The attachment does not contain FileContent"
        )

    if isinstance(value, bytes):
        file_bytes = value

    elif isinstance(value, bytearray):
        file_bytes = bytes(value)

    elif isinstance(value, memoryview):
        file_bytes = value.tobytes()

    else:
        try:
            file_bytes = bytes(value)
        except Exception as exc:
            raise AttachmentContentMissingError(
                "FileContent could not be converted to bytes"
            ) from exc

    if not file_bytes:
        raise AttachmentContentMissingError(
            "The attachment FileContent is empty"
        )

    return file_bytes


# ============================================================
# FETCH FILE CONTENT
# ============================================================

def fetch_attachment_content(
    payload: AttachmentContentRequest,
) -> dict[str, Any]:
    """
    Fetch attachment file content.

    VendAccount is not validated here.
    Attachment is identified using AttachmentId + ProspectId.
    Optional validation is done only for AttachmentFor and FileName.
    """

    requested_prospect_id = str(
        payload.ProspectId
    ).strip().upper()

    requested_attachment_for = (
        _normalize_optional_text(
            payload.AttachmentFor
        )
    )

    requested_file_name = (
        _normalize_optional_text(
            payload.FileName
        )
    )

    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            cursor.execute(
                f"""
                SELECT
                    Id,
                    ProspectId,
                    AttachmentFor,
                    FileName,
                    FileExtension,
                    ContentType,
                    FileSizeBytes,
                    FileContent
                FROM {ATTACHMENT_TABLE}
                WHERE Id = ?
                  AND ProspectId = ?
                """,
                (
                    payload.AttachmentId,
                    requested_prospect_id,
                ),
            )

            row = cursor.fetchone()

            if row is None:
                raise AttachmentNotFoundError(
                    "Attachment was not found for "
                    f"AttachmentId={payload.AttachmentId} and "
                    f"ProspectId={requested_prospect_id}"
                )

            attachment_id = int(row[0])

            prospect_id = str(
                row[1]
            ).strip().upper()

            attachment_for = (
                _normalize_optional_text(row[2])
                or ""
            )

            file_name = (
                _normalize_optional_text(row[3])
                or ""
            )

            file_extension = (
                (
                    _normalize_optional_text(row[4])
                    or ""
                )
                .lower()
                .lstrip(".")
            )

            content_type = (
                (
                    _normalize_optional_text(row[5])
                    or "application/octet-stream"
                )
                .lower()
            )

            stored_file_size = int(
                row[6] or 0
            )

            file_bytes = _convert_to_bytes(
                row[7]
            )

            # =================================================
            # VERIFY REQUEST METADATA
            # =================================================

            mismatched_fields: list[str] = []

            if (
                requested_attachment_for is not None
                and attachment_for.lower()
                != requested_attachment_for.lower()
            ):
                mismatched_fields.append(
                    "AttachmentFor"
                )

            if (
                requested_file_name is not None
                and file_name.lower()
                != requested_file_name.lower()
            ):
                mismatched_fields.append(
                    "FileName"
                )

            if mismatched_fields:
                raise AttachmentMetadataMismatchError(
                    "The following request fields do not match "
                    "the stored attachment: "
                    + ", ".join(mismatched_fields)
                )

            # =================================================
            # VERIFY FILE SIZE
            # =================================================

            actual_file_size = len(
                file_bytes
            )

            if (
                stored_file_size > 0
                and stored_file_size != actual_file_size
            ):
                raise AttachmentContentMissingError(
                    "Stored FileSizeBytes does not match "
                    "the actual FileContent size. "
                    f"Stored={stored_file_size}, "
                    f"Actual={actual_file_size}"
                )

            return {
                "ATTACHMENT_ID": attachment_id,
                "PROSPECT_ID": prospect_id,
                "ATTACHMENT_FOR": attachment_for,
                "FILE_NAME": file_name,
                "FILE_EXTENSION": file_extension,
                "CONTENT_TYPE": content_type,
                "FILE_SIZE_BYTES": actual_file_size,
                "FILE_BYTES": file_bytes,
            }

        finally:
            cursor.close()

# from __future__ import annotations

# from typing import Any

# from app.core.config import settings
# from app.db.base import get_secondary_connection
# from app.schemas.vendoroboardingattach_schema import (
#     AttachmentContentRequest,
# )


# # ============================================================
# # DATABASE CONFIGURATION
# # ============================================================

# SECONDARY_SCHEMA = getattr(
#     settings,
#     "SECONDARY_DB_SCHEMA",
#     settings.DB_SCHEMA,
# )

# ATTACHMENT_TABLE = (
#     f"[{SECONDARY_SCHEMA}].[HIQ_VendorFileAttachment]"
# )


# # ============================================================
# # EXCEPTIONS
# # ============================================================

# class AttachmentNotFoundError(LookupError):
#     pass


# class AttachmentMetadataMismatchError(ValueError):
#     pass


# class AttachmentContentMissingError(LookupError):
#     pass


# # ============================================================
# # HELPERS
# # ============================================================

# NULL_TEXT_VALUES = {
#     "",
#     "null",
#     "none",
#     "undefined",
#     "n/a",
# }


# def _normalize_optional_text(
#     value: Any,
# ) -> str | None:
#     if value is None:
#         return None

#     normalized = str(
#         value
#     ).strip()

#     if normalized.lower() in NULL_TEXT_VALUES:
#         return None

#     return normalized


# def _normalize_optional_upper(
#     value: Any,
# ) -> str | None:
#     normalized = _normalize_optional_text(
#         value
#     )

#     if normalized is None:
#         return None

#     return normalized.upper()


# def _convert_to_bytes(
#     value: Any,
# ) -> bytes:
#     """Convert SQL Server VARBINARY value to Python bytes."""

#     if value is None:
#         raise AttachmentContentMissingError(
#             "The attachment does not contain FileContent"
#         )

#     if isinstance(value, bytes):
#         file_bytes = value
#     elif isinstance(value, bytearray):
#         file_bytes = bytes(value)
#     elif isinstance(value, memoryview):
#         file_bytes = value.tobytes()
#     else:
#         try:
#             file_bytes = bytes(value)
#         except Exception as exc:
#             raise AttachmentContentMissingError(
#                 "FileContent could not be converted to bytes"
#             ) from exc

#     if not file_bytes:
#         raise AttachmentContentMissingError(
#             "The attachment FileContent is empty"
#         )

#     return file_bytes


# # ============================================================
# # FETCH FILE CONTENT
# # ============================================================

# def fetch_attachment_content(
#     payload: AttachmentContentRequest,
# ) -> dict[str, Any]:
#     """
#     Fetch an attachment from the secondary database.

#     VendAccount validation is skipped when the frontend sends
#     null, "", "null", "none", "undefined", or "n/a".
#     """

#     requested_prospect_id = str(
#         payload.ProspectId
#     ).strip().upper()

#     requested_attachment_for = (
#         _normalize_optional_text(
#             payload.AttachmentFor
#         )
#     )

#     requested_vend_account = (
#         _normalize_optional_upper(
#             payload.VendAccount
#         )
#     )

#     requested_file_name = (
#         _normalize_optional_text(
#             payload.FileName
#         )
#     )

#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 f"""
#                 SELECT
#                     Id,
#                     ProspectId,
#                     AttachmentFor,
#                     VendAccount,
#                     FileName,
#                     FileExtension,
#                     ContentType,
#                     FileSizeBytes,
#                     FileContent
#                 FROM {ATTACHMENT_TABLE}
#                 WHERE Id = ?
#                   AND ProspectId = ?
#                 """,
#                 (
#                     payload.AttachmentId,
#                     requested_prospect_id,
#                 ),
#             )

#             row = cursor.fetchone()

#             if row is None:
#                 raise AttachmentNotFoundError(
#                     "Attachment was not found for "
#                     f"AttachmentId={payload.AttachmentId} and "
#                     f"ProspectId={requested_prospect_id}"
#                 )

#             attachment_id = int(row[0])
#             prospect_id = str(row[1]).strip().upper()
#             attachment_for = (
#                 _normalize_optional_text(row[2])
#                 or ""
#             )
#             vend_account = _normalize_optional_upper(row[3])
#             file_name = (
#                 _normalize_optional_text(row[4])
#                 or ""
#             )
#             file_extension = (
#                 (
#                     _normalize_optional_text(row[5])
#                     or ""
#                 )
#                 .lower()
#                 .lstrip(".")
#             )
#             content_type = (
#                 (
#                     _normalize_optional_text(row[6])
#                     or "application/octet-stream"
#                 )
#                 .lower()
#             )
#             stored_file_size = int(row[7] or 0)
#             file_bytes = _convert_to_bytes(row[8])

#             # =================================================
#             # VERIFY REQUEST METADATA
#             # =================================================

#             mismatched_fields: list[str] = []

#             if (
#                 requested_attachment_for is not None
#                 and attachment_for.lower()
#                 != requested_attachment_for.lower()
#             ):
#                 mismatched_fields.append("AttachmentFor")

#             # Vendor account may be absent before D365 vendor
#             # creation. Compare only when a real value is sent.
#             if (
#                 requested_vend_account is not None
#                 and vend_account != requested_vend_account
#             ):
#                 mismatched_fields.append("VendAccount")

#             if (
#                 requested_file_name is not None
#                 and file_name.lower()
#                 != requested_file_name.lower()
#             ):
#                 mismatched_fields.append("FileName")

#             if mismatched_fields:
#                 raise AttachmentMetadataMismatchError(
#                     "The following request fields do not match "
#                     "the stored attachment: "
#                     + ", ".join(mismatched_fields)
#                 )

#             # =================================================
#             # VERIFY FILE SIZE
#             # =================================================

#             actual_file_size = len(file_bytes)

#             if (
#                 stored_file_size > 0
#                 and stored_file_size != actual_file_size
#             ):
#                 raise AttachmentContentMissingError(
#                     "Stored FileSizeBytes does not match "
#                     "the actual FileContent size. "
#                     f"Stored={stored_file_size}, "
#                     f"Actual={actual_file_size}"
#                 )

#             return {
#                 "ATTACHMENT_ID": attachment_id,
#                 "PROSPECT_ID": prospect_id,
#                 "ATTACHMENT_FOR": attachment_for,
#                 "VEND_ACCOUNT": vend_account,
#                 "FILE_NAME": file_name,
#                 "FILE_EXTENSION": file_extension,
#                 "CONTENT_TYPE": content_type,
#                 "FILE_SIZE_BYTES": actual_file_size,
#                 "FILE_BYTES": file_bytes,
#             }

#         finally:
#             cursor.close()
