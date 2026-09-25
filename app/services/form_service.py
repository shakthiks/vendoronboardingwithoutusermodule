from __future__ import annotations

import json
import base64
import mimetypes
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, TypeVar

import pyodbc
from pydantic import BaseModel

from app.core.config import settings

from app.db.base import get_connection
from app.schemas.vendoronboardingform_schema import (
    AttachmentMetadata,
    VendorFetchRequest,
    VendorSaveRequest,
    QualityQuestionnaireInput
)


# ============================================================
# DATABASE TABLES
# ============================================================

DB_SCHEMA = getattr(
    settings,
    "SECONDARY_DB_SCHEMA",
    settings.DB_SCHEMA,
)

# Terms upload/fetch services use settings.DB_SCHEMA.
# Keeping this separate prevents the form API from querying
# a different schema when SECONDARY_DB_SCHEMA is configured.
TERMS_DB_SCHEMA = getattr(
    settings,
    "TERMS_DB_SCHEMA",
    settings.DB_SCHEMA,
)

PROSPECT_TABLE = (
    f"[{DB_SCHEMA}].[d365_VendorProspect]"
)

REGISTRATION_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
)

CONTACT_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorContact]"
)

CUSTOMER_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorMajorCustomer]"
)

OEM_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorOEMDetail]"
)

CERTIFICATION_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorCertification]"
)

LEGAL_DOCUMENT_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorLegalDocument]"
)

QUESTIONNAIRE_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorQualityQuestionnaire]"
)

ATTACHMENT_TABLE = (
    f"[{DB_SCHEMA}].[HIQ_VendorFileAttachment]"
)
TERMS_VERSION_TABLE = (
    f"[{TERMS_DB_SCHEMA}].[HIQ_TermsAndConditionsVersion]"
)

PROSPECT_TERMS_HISTORY_TABLE = (
    f"[{TERMS_DB_SCHEMA}].[HIQ_ProspectTermsHistory]"
)

# ============================================================
# FILE SETTINGS
# ============================================================

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
ALLOWED_FILE_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}
# ALLOWED_FILE_EXTENSIONS = {
#     ".pdf",
#     ".jpg",
#     ".jpeg",
#     ".png",
# }

# ALLOWED_CONTENT_TYPES = {
#     "application/pdf",
#     "image/jpeg",
#     "image/jpg",
#     "image/png",
#     "application/octet-stream",
# }
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
}
# ALLOWED_ATTACHMENT_TYPES = {
#     "FactoryLicense",
#     "OEMCert",
#     "Certification",
# }
def _validate_attachment_for(value: Any) -> str:
    attachment_for = str(value or "").strip()

    if not attachment_for:
        raise ValueError("ATTACHMENT_FOR is required")

    if len(attachment_for) > 100:
        raise ValueError("ATTACHMENT_FOR cannot exceed 100 characters")

    return attachment_for

# ============================================================
# EXCEPTIONS
# ============================================================

class SubmissionValidationError(ValueError):

    def __init__(
        self,
        errors: list[str],
    ) -> None:
        self.errors = errors
        super().__init__(
            "; ".join(errors)
        )


# ============================================================
# COMMON HELPERS
# ============================================================

ModelType = TypeVar(
    "ModelType",
    bound=BaseModel,
)


def _execute(
    cursor: pyodbc.Cursor,
    query: str,
    parameters: Iterable[Any] = (),
) -> pyodbc.Cursor:

    values = tuple(parameters)

    if values:
        return cursor.execute(
            query,
            *values,
        )

    return cursor.execute(query)


def _clean_text(
    value: Any,
) -> Any:

    if isinstance(value, str):
        value = value.strip()
        return value if value else None

    return value


def _normalize_id(
    value: Any,
) -> int | None:

    if value in {
        None,
        "",
    }:
        return None

    try:
        numeric_value = int(value)

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            f"Invalid record ID: {value}"
        ) from exc

    if numeric_value <= 0:
        return None

    return numeric_value


def _normalize_prospect_id(
    prospect_id: str,
) -> str:

    value = (
        prospect_id or ""
    ).strip().upper()

    if not value:
        raise ValueError(
            "PROSPECT_ID is required"
        )

    if len(value) > 10:
        raise ValueError(
            "PROSPECT_ID cannot exceed 10 characters"
        )

    return value


def _action_value(
    action: Any,
) -> str:

    if hasattr(action, "value"):
        return str(
            action.value
        ).strip().upper()

    return str(action).strip().upper()


def _yes_no_to_bit(
    value: Any,
) -> int | None:

    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        if value in {
            0,
            1,
        }:
            return value

    normalized = str(
        value
    ).strip().lower()

    if normalized in {
        "yes",
        "true",
        "1",
        "y",
    }:
        return 1

    if normalized in {
        "no",
        "false",
        "0",
        "n",
    }:
        return 0

    raise ValueError(
        f"Invalid Yes/No value: {value}"
    )


def _parse_integer(
    value: Any,
    field_name: str,
) -> int | None:

    if value in {
        None,
        "",
    }:
        return None

    try:
        return int(value)

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            f"{field_name} must be a number"
        ) from exc

def questionnaire_to_varchar(
    value: Any,
) -> str | None:
    """
    Store questionnaire value directly as VARCHAR.
    No Yes/No validation.
    No 20-character validation in code.
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    return text
def _parse_establishment_year(
    value: Any,
) -> int | None:

    if value in {
        None,
        "",
    }:
        return None

    if isinstance(value, int):
        return value

    text = str(
        value
    ).strip()

    if text.isdigit():
        return int(text)

    match = re.fullmatch(
        r"(\d+)\s*years?",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        years_old = int(
            match.group(1)
        )

        return (
            datetime.now().year
            - years_old
        )

    raise ValueError(
        "YEAROFESTABLISHMENT must be an actual year "
        'or a value such as "3 years"'
    )


def _parse_valid_until(
    value: Any,
) -> date | None:

    if value in {
        None,
        "",
    }:
        return None

    if isinstance(
        value,
        datetime,
    ):
        parsed_datetime = value

    elif isinstance(
        value,
        date,
    ):
        return value

    else:
        text = str(
            value
        ).strip()

        try:
            # Date-only value.
            if (
                "T" not in text
                and len(text) == 10
            ):
                return date.fromisoformat(
                    text
                )

            parsed_datetime = (
                datetime.fromisoformat(
                    text.replace(
                        "Z",
                        "+00:00",
                    )
                )
            )

        except ValueError as exc:
            raise ValueError(
                f"Invalid VALIDUNTIL value: {value}"
            ) from exc

    if (
        parsed_datetime.tzinfo
        is not None
    ):
        ist = timezone(
            timedelta(
                hours=5,
                minutes=30,
            )
        )

        parsed_datetime = (
            parsed_datetime
            .astimezone(ist)
        )

    return parsed_datetime.date()


def _fetch_one(
    cursor: pyodbc.Cursor,
) -> dict[str, Any]:

    row = cursor.fetchone()

    if row is None:
        return {}

    columns = [
        description[0]
        for description
        in cursor.description
    ]

    return dict(
        zip(
            columns,
            row,
        )
    )


def _fetch_all(
    cursor: pyodbc.Cursor,
) -> list[dict[str, Any]]:

    columns = [
        description[0]
        for description
        in cursor.description
    ]

    return [
        dict(
            zip(
                columns,
                row,
            )
        )
        for row in cursor.fetchall()
    ]


def _fetch_scalar(
    cursor: pyodbc.Cursor,
) -> Any:

    row = cursor.fetchone()

    if row is None:
        return None

    return row[0]


def _validate_unique_ids(
    values: list[int],
    field_name: str,
) -> None:

    if len(values) != len(
        set(values)
    ):
        raise ValueError(
            f"Duplicate {field_name} values were supplied"
        )


def _parse_model_json(
    model_class: type[ModelType],
    json_value: str,
) -> ModelType:

    if hasattr(
        model_class,
        "model_validate_json",
    ):
        return model_class.model_validate_json(
            json_value
        )

    return model_class.parse_raw(
        json_value
    )


def _parse_model_object(
    model_class: type[ModelType],
    value: Any,
) -> ModelType:

    if hasattr(
        model_class,
        "model_validate",
    ):
        return model_class.model_validate(
            value
        )

    return model_class.parse_obj(
        value
    )


def _parse_save_request(
    form_data_json: str,
    attachment_metadata_json: str | None,
) -> tuple[
    VendorSaveRequest,
    list[AttachmentMetadata],
]:

    if not form_data_json:
        raise ValueError(
            "FORM_DATA is required"
        )

    payload = _parse_model_json(
        VendorSaveRequest,
        form_data_json,
    )

    metadata_json = (
        attachment_metadata_json
        or "[]"
    ).strip()

    try:
        raw_metadata = json.loads(
            metadata_json
        )

    except json.JSONDecodeError as exc:
        raise ValueError(
            "ATTACHMENT_METADATA contains invalid JSON"
        ) from exc

    if not isinstance(
        raw_metadata,
        list,
    ):
        raise ValueError(
            "ATTACHMENT_METADATA must be a JSON array"
        )

    metadata = [
        _parse_model_object(
            AttachmentMetadata,
            item,
        )
        for item in raw_metadata
    ]

    return payload, metadata


def _to_iso_string(
    value: Any,
) -> str | None:

    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    return str(value)


def _bit_to_yes_no(
    value: Any,
) -> str | None:

    if value is None:
        return None

    return (
        "Yes"
        if bool(value)
        else "No"
    )
def _clean_text_list_json(value: Any) -> str | None:
    """
    For fields like CURRENTSUPPLYLOCATION.

    Frontend can send:
    ["IND", "IDN"]

    DB stores:
    ["IND", "IDN"]
    """

    if value is None:
        return None

    if isinstance(value, list):
        cleaned_values = [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ]

        if not cleaned_values:
            return None

        return json.dumps(cleaned_values)

    text = str(value).strip()

    if not text:
        return None

    # Single country selected as string.
    return json.dumps([text])

def _json_list_from_db(
    value: Any,
) -> list[str]:
    """
    Return location values exactly as stored in the database.
    No uppercase, lowercase, or title-case conversion.
    """

    if value is None:
        return []

    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if (
                item is not None
                and str(item).strip()
            )
        ]

    text = str(value).strip()

    if not text:
        return []

    try:
        parsed = json.loads(text)

        if isinstance(parsed, list):
            return [
                str(item).strip()
                for item in parsed
                if (
                    item is not None
                    and str(item).strip()
                )
            ]

        if isinstance(parsed, str):
            return [
                parsed.strip()
            ]

    except (
        json.JSONDecodeError,
        TypeError,
    ):
        pass

    # Support old comma-separated DB values.
    return [
        item.strip()
        for item in text.split(",")
        if item.strip()
    ]


def _find_attachment(
    attachments: list[dict[str, Any]],
    attachment_for: str,
    parent_record_id: int | None = None,
) -> dict[str, Any] | None:
    """
    Return the newest matching attachment for one logical slot.

    FactoryLicense uses ParentRecordId = NULL.
    OEMCert uses ParentRecordId = OEMId.
    Certification uses ParentRecordId = CertificationId.
    """

    target_type = attachment_for.strip().lower()

    for attachment in attachments:
        current_type = str(
            attachment.get("AttachmentFor") or ""
        ).strip().lower()

        if current_type != target_type:
            continue

        if attachment.get("ParentRecordId") == parent_record_id:
            return attachment

    return None


def _attachment_fetch_fields(
    attachment: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build nested attachment metadata for the frontend fetch response."""

    if not attachment:
        return {
            "ATTACHMENTID": None,
            "ATTACHMENTFOR": None,
            "PARENTRECORDID": None,
            "VENDACCOUNT": None,
            "FILEEXTENSION": None,
            "CONTENTTYPE": None,
            "FILESIZEBYTES": None,
        }

    return {
        "ATTACHMENTID": attachment.get("AttachmentId"),
        "ATTACHMENTFOR": attachment.get("AttachmentFor"),
        "PARENTRECORDID": attachment.get("ParentRecordId"),
        "VENDACCOUNT": attachment.get("VendAccount"),
        "FILEEXTENSION": attachment.get("FileExtension"),
        "CONTENTTYPE": attachment.get("ContentType"),
        "FILESIZEBYTES": attachment.get("FileSizeBytes"),
    }


def _prepare_frontend_base64_file(
    base64_value: str,
    frontend_file_name: str | None,
    content_field_name: str,
    file_name_field_name: str,
) -> tuple[str, str, str, bytes]:
    """
    Validate a frontend-supplied filename and base64 file.

    Supported file types:
    PDF, JPG, JPEG, PNG, DOC, DOCX, XLS and XLSX.
    """

    clean_file_name = _clean_text(
        frontend_file_name
    )

    if not clean_file_name:
        raise ValueError(
            f"{file_name_field_name} is required when "
            f"{content_field_name} is provided"
        )

    file_name = Path(
        str(clean_file_name)
    ).name

    extension = Path(
        file_name
    ).suffix.lower()

    if extension not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(
            sorted(
                value.lstrip(".").upper()
                for value in ALLOWED_FILE_EXTENSIONS
            )
        )
        raise ValueError(
            f"{file_name}: unsupported file type. "
            f"Allowed types: {allowed}"
        )

    encoded_value = str(
        base64_value
    ).strip()

    if encoded_value.lower().startswith("data:"):
        if "," not in encoded_value:
            raise ValueError(
                f"{content_field_name} contains an invalid data URL"
            )

        encoded_value = encoded_value.split(
            ",",
            1,
        )[1]

    encoded_value = re.sub(
        r"\s+",
        "",
        encoded_value,
    )

    try:
        file_bytes = base64.b64decode(
            encoded_value,
            validate=True,
        )

    except Exception as exc:
        raise ValueError(
            f"{content_field_name} contains invalid base64 data"
        ) from exc

    if not file_bytes:
        raise ValueError(
            f"{content_field_name} decoded to empty bytes"
        )

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES // (
            1024 * 1024
        )
        raise ValueError(
            f"{file_name} exceeds the {max_mb} MB limit"
        )

    content_type_map = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".doc": "application/msword",
        ".docx": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        ".xls": "application/vnd.ms-excel",
        ".xlsx": (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    }

    content_type = (
        content_type_map.get(extension)
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )

    ole_signature = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    )
    zip_signatures = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    )

    signature_matches = {
        ".pdf": file_bytes.startswith(b"%PDF"),
        ".jpg": file_bytes.startswith(b"\xff\xd8\xff"),
        ".jpeg": file_bytes.startswith(b"\xff\xd8\xff"),
        ".png": file_bytes.startswith(
            b"\x89PNG\r\n\x1a\n"
        ),
        ".doc": file_bytes.startswith(
            ole_signature
        ),
        ".xls": file_bytes.startswith(
            ole_signature
        ),
        ".docx": file_bytes.startswith(
            zip_signatures
        ),
        ".xlsx": file_bytes.startswith(
            zip_signatures
        ),
    }

    if not signature_matches.get(
        extension,
        True,
    ):
        raise ValueError(
            f"{file_name}: the file extension does not match "
            "the uploaded file content"
        )

    return (
        file_name,
        extension.lstrip("."),
        content_type,
        file_bytes,
    )

def _fetch_prospect_terms_history(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> list[dict[str, Any]]:
    """
    Fetch all accepted Terms and Conditions versions
    for the given prospect.

    Returns an empty list when the prospect has not
    accepted any Terms and Conditions.
    """

    _execute(
        cursor,
        f"""
        SELECT
            h.ProspectTermsHistoryId,
            h.ProspectId,
            h.TermsVersionId,
            h.IsCurrent,
            h.AssignedOn,
            h.AcceptedOn,
            h.AssignedByUserId,
            h.AcceptedByUserId,
            h.Remarks,
            h.CreatedOn,

            v.VersionNumber,
            v.FileName,
            v.ContentType,
            v.IsActive AS IsVersionActive

        FROM {PROSPECT_TERMS_HISTORY_TABLE} AS h

        LEFT JOIN {TERMS_VERSION_TABLE} AS v
            ON v.TermsVersionId = h.TermsVersionId

        WHERE h.ProspectId = ?
          AND h.AcceptedOn IS NOT NULL

        ORDER BY
            h.IsCurrent DESC,
            h.ProspectTermsHistoryId DESC
        """,
        (
            prospect_id,
        ),
    )

    rows = _fetch_all(cursor)

    return [
        {
            "PROSPECTTERMSHISTORYID": row.get(
                "ProspectTermsHistoryId"
            ),
            "PROSPECTID": row.get(
                "ProspectId"
            ),
            "TERMSVERSIONID": row.get(
                "TermsVersionId"
            ),
            "VERSIONNUMBER": row.get(
                "VersionNumber"
            ),
            "FILENAME": row.get(
                "FileName"
            ),
            "CONTENTTYPE": (
                row.get("ContentType")
                or "application/pdf"
            ),
            "ISCURRENT": bool(
                row.get("IsCurrent")
            ),
            "ISVERSIONACTIVE": bool(
                row.get("IsVersionActive")
            ),
            "ASSIGNEDON": _to_iso_string(
                row.get("AssignedOn")
            ),
            "ACCEPTEDON": _to_iso_string(
                row.get("AcceptedOn")
            ),
            "ASSIGNEDBYUSERID": row.get(
                "AssignedByUserId"
            ),
            "ACCEPTEDBYUSERID": row.get(
                "AcceptedByUserId"
            ),
            "REMARKS": row.get(
                "Remarks"
            ),
            "CREATEDON": _to_iso_string(
                row.get("CreatedOn")
            ),
        }
        for row in rows
    ]
# ============================================================
# GENERIC DATABASE HELPERS
# ============================================================
def _save_prospect_terms_acceptance(
    cursor: pyodbc.Cursor,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> dict[str, Any]:
    """
    Validate that the frontend submitted the current active
    TermsVersionId and store the prospect acceptance history.
    """

    declaration = payload.declaration

    accepted = _yes_no_to_bit(
        getattr(
            declaration,
            "ACCEPTTERMS",
            None,
        )
    )

    if accepted != 1:
        raise ValueError(
            "Terms and Conditions must be accepted."
        )

    submitted_version_id = _normalize_id(
        getattr(
            declaration,
            "TERMSVERSIONID",
            None,
        )
    )

    if submitted_version_id is None:
        raise ValueError(
            "TERMSVERSIONID is required when accepting "
            "Terms and Conditions."
        )

    # Lock and obtain the latest active version.
    _execute(
        cursor,
        f"""
        SELECT TOP 1
            TermsVersionId,
            VersionNumber
        FROM {TERMS_VERSION_TABLE}
            WITH (UPDLOCK, HOLDLOCK)
        WHERE IsActive = 1
        ORDER BY TermsVersionId DESC
        """
    )

    active_version = _fetch_one(cursor)

    if not active_version:
        raise ValueError(
            "No active Terms and Conditions version was found."
        )

    active_version_id = int(
        active_version["TermsVersionId"]
    )

    # Prevent acceptance of an old version.
    if submitted_version_id != active_version_id:
        raise ValueError(
            "The Terms and Conditions were updated. "
            "Please refresh the form and accept the latest version."
        )

    # Check whether the same version is already accepted.
    _execute(
        cursor,
        f"""
        SELECT TOP 1
            ProspectTermsHistoryId,
            AcceptedOn
        FROM {PROSPECT_TERMS_HISTORY_TABLE}
        WHERE ProspectId = ?
          AND TermsVersionId = ?
        ORDER BY ProspectTermsHistoryId DESC
        """,
        (
            prospect_id,
            active_version_id,
        ),
    )

    existing_acceptance = _fetch_one(cursor)

    if (
        existing_acceptance
        and existing_acceptance.get("AcceptedOn")
        is not None
    ):
        return {
            "TERMS_VERSION_ID":
                active_version_id,
            "VERSION_NUMBER":
                active_version.get("VersionNumber"),
            "ALREADY_ACCEPTED": True,
            "ACCEPTED_ON": _to_iso_string(
                existing_acceptance.get(
                    "AcceptedOn"
                )
            ),
        }

    # Previous version remains in history but is no longer current.
    _execute(
        cursor,
        f"""
        UPDATE {PROSPECT_TERMS_HISTORY_TABLE}
        SET IsCurrent = 0
        WHERE ProspectId = ?
          AND IsCurrent = 1
        """,
        (
            prospect_id,
        ),
    )

    if existing_acceptance:
        history_id = int(
            existing_acceptance[
                "ProspectTermsHistoryId"
            ]
        )

        _execute(
            cursor,
            f"""
            UPDATE {PROSPECT_TERMS_HISTORY_TABLE}
            SET
                IsCurrent = 1,
                AcceptedOn = SYSUTCDATETIME()
            WHERE ProspectTermsHistoryId = ?
            """,
            (
                history_id,
            ),
        )

    else:
        inserted = _execute(
            cursor,
            f"""
            INSERT INTO {PROSPECT_TERMS_HISTORY_TABLE}
            (
                ProspectId,
                TermsVersionId,
                IsCurrent,
                AssignedOn,
                AcceptedOn,
                CreatedOn
            )
            OUTPUT
                INSERTED.ProspectTermsHistoryId
            VALUES
            (
                ?,
                ?,
                1,
                SYSUTCDATETIME(),
                SYSUTCDATETIME(),
                SYSUTCDATETIME()
            )
            """,
            (
                prospect_id,
                active_version_id,
            ),
        ).fetchone()

        if inserted is None:
            raise RuntimeError(
                "Prospect Terms acceptance was not saved."
            )

        history_id = int(inserted[0])

    return {
        "PROSPECT_TERMS_HISTORY_ID": history_id,
        "TERMS_VERSION_ID": active_version_id,
        "VERSION_NUMBER": active_version.get(
            "VersionNumber"
        ),
        "ALREADY_ACCEPTED": False,
    }
def _get_existing_ids(
    cursor: pyodbc.Cursor,
    table_name: str,
    id_column: str,
    prospect_id: str,
) -> set[int]:

    _execute(
        cursor,
        f"""
        SELECT [{id_column}]
        FROM {table_name}
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    return {
        int(row[0])
        for row in cursor.fetchall()
    }


def _delete_ids(
    cursor: pyodbc.Cursor,
    table_name: str,
    id_column: str,
    prospect_id: str,
    record_ids: set[int],
) -> list[int]:

    if not record_ids:
        return []

    sorted_ids = sorted(
        record_ids
    )

    placeholders = ", ".join(
        "?"
        for _ in sorted_ids
    )

    _execute(
        cursor,
        f"""
        DELETE FROM {table_name}
        WHERE ProspectId = ?
          AND [{id_column}] IN ({placeholders})
        """,
        (
            prospect_id,
            *sorted_ids,
        ),
    )

    return sorted_ids


# ============================================================
# PROSPECT AND REGISTRATION
# ============================================================

def _get_prospect(
    cursor: pyodbc.Cursor,
    prospect_id: str,
    lock: bool = False,
) -> dict[str, Any]:

    lock_hint = (
        "WITH (UPDLOCK, HOLDLOCK)"
        if lock
        else ""
    )

    _execute(
        cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            VendorAccount,
            Name,
            Email,
            VendGroup
        FROM {PROSPECT_TABLE}
        {lock_hint}
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    prospect = _fetch_one(
        cursor
    )

    if not prospect:
        raise LookupError(
            f"PROSPECT_ID {prospect_id} was not found "
            "in d365_VendorProspect"
        )

    return prospect

def _populate_attachment_vendor_account(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> str | None:
    """
    Fetch VendorAccount from d365_VendorProspect and populate
    all attachment rows belonging to the ProspectId.
    """

    _execute(
        cursor,
        f"""
        SELECT
            VendorAccount
        FROM {PROSPECT_TABLE}
        WHERE ProspectId = ?
        """,
        (
            prospect_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        raise LookupError(
            f"PROSPECT_ID {prospect_id} was not found "
            "in d365_VendorProspect"
        )

    vendor_account = _clean_text(
        row[0]
    )

    # VendorAccount may not be generated yet.
    if vendor_account is None:
        return None

    _execute(
        cursor,
        f"""
        UPDATE {ATTACHMENT_TABLE}
        SET
            VendAccount = ?,
            ModifiedDateTime = GETDATE()
        WHERE ProspectId = ?
        """,
        (
            vendor_account,
            prospect_id,
        ),
    )

    return str(
        vendor_account
    )
def _get_or_create_registration(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any]:

    prospect = _get_prospect(
        cursor=cursor,
        prospect_id=prospect_id,
        lock=True,
    )

    _execute(
        cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    registration = _fetch_one(
        cursor
    )

    if registration:
        return registration

    source_prospect_seq = int(
        prospect["ProspectSeq"]
    )

    company_name = (
        _clean_text(
            prospect.get("Name")
        )
        or ""
    )

    _execute(
        cursor,
        f"""
        SELECT ProspectId
        FROM {REGISTRATION_TABLE}
        WHERE ProspectSeq = ?
        """,
        (source_prospect_seq,),
    )

    existing_sequence = (
        cursor.fetchone()
    )

    if existing_sequence:
        existing_id = str(
            existing_sequence[0]
        ).strip().upper()

        if existing_id != prospect_id:
            raise RuntimeError(
                "Prospect sequence conflict. "
                f"ProspectSeq={source_prospect_seq}, "
                f"ExistingProspectId={existing_id}, "
                f"RequestedProspectId={prospect_id}"
            )

    else:
        identity_insert_enabled = False

        try:
            _execute(
                cursor,
                f"""
                SET IDENTITY_INSERT
                {REGISTRATION_TABLE} ON
                """,
            )

            identity_insert_enabled = True

            _execute(
                cursor,
                f"""
                INSERT INTO {REGISTRATION_TABLE}
                (
                    ProspectSeq,
                    CompanyName,
                    Status,
                    IsDraft,
                    CreatedOn,
                    ModifiedOn
                )
                VALUES
                (
                    ?,
                    ?,
                    'INVITED',
                    0,
                    GETDATE(),
                    GETDATE()
                )
                """,
                (
                    source_prospect_seq,
                    company_name,
                ),
            )

        finally:
            if identity_insert_enabled:
                _execute(
                    cursor,
                    f"""
                    SET IDENTITY_INSERT
                    {REGISTRATION_TABLE} OFF
                    """,
                )

    _execute(
        cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {REGISTRATION_TABLE}
        WHERE ProspectSeq = ?
        """,
        (source_prospect_seq,),
    )

    registration = _fetch_one(
        cursor
    )

    if not registration:
        raise RuntimeError(
            "Failed to create vendor registration"
        )

    generated_prospect_id = str(
        registration["ProspectId"]
    ).strip().upper()

    if generated_prospect_id != prospect_id:
        raise RuntimeError(
            "Prospect ID mismatch. "
            f"Requested={prospect_id}, "
            f"Generated={generated_prospect_id}"
        )

    return registration



def create_invited_registration(
    prospect_id: str,
    changed_by: str | None = "INVITATION_SERVICE",
) -> dict[str, Any]:
    """
    Create the vendor invitation shell only in the secondary database.

    The prospect must already exist in d365_VendorProspect. A new
    HIQ_VendorRegistration row starts with Status=INVITED and IsDraft=0.
    Existing registrations are returned without resetting their status.
    """

    normalized_prospect_id = _normalize_prospect_id(
        prospect_id
    )

    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            prospect = _get_prospect(
                cursor=cursor,
                prospect_id=normalized_prospect_id,
                lock=True,
            )

            _execute(
                cursor,
                f"""
                SELECT
                    ProspectSeq,
                    ProspectId,
                    CompanyName,
                    Status,
                    IsDraft,
                    IsAuthorizedDistributor
                FROM {REGISTRATION_TABLE}
                WITH (UPDLOCK, HOLDLOCK)
                WHERE ProspectId = ?
                """,
                (normalized_prospect_id,),
            )

            existing_registration = _fetch_one(
                cursor
            )

            if existing_registration:
                connection.commit()

                return {
                    "SUCCESS": True,
                    "MESSAGE": "Vendor invitation registration already exists",
                    "PROSPECT_ID": normalized_prospect_id,
                    "PROSPECT_SEQ": int(
                        existing_registration["ProspectSeq"]
                    ),
                    "COMPANY_NAME": existing_registration.get(
                        "CompanyName"
                    ),
                    "EMAIL": prospect.get("Email"),
                    "VENDOR_ACCOUNT": prospect.get("VendorAccount"),
                    "STATUS": str(
                        existing_registration.get("Status") or "INVITED"
                    ).strip().upper(),
                    "ISDRAFT": int(
                        existing_registration.get("IsDraft") or 0
                    ),
                    "ROW_CREATED": False,
                    "CHANGED_BY": changed_by,
                }

            registration = _get_or_create_registration(
                cursor=cursor,
                prospect_id=normalized_prospect_id,
            )

            connection.commit()

            return {
                "SUCCESS": True,
                "MESSAGE": "Vendor invitation registration created successfully",
                "PROSPECT_ID": normalized_prospect_id,
                "PROSPECT_SEQ": int(
                    registration["ProspectSeq"]
                ),
                "COMPANY_NAME": registration.get(
                    "CompanyName"
                ),
                "EMAIL": prospect.get("Email"),
                "VENDOR_ACCOUNT": prospect.get("VendorAccount"),
                "STATUS": "INVITED",
                "ISDRAFT": int(
                    registration.get("IsDraft") or 0
                ),
                "ROW_CREATED": True,
                "CHANGED_BY": changed_by,
            }

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()


def _get_registration_for_update(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any]:
    """
    Fetch the invitation registration for form save/update.

    The invitation step must create the registration first. The form API
    does not create a second copy in another database.
    """

    _execute(
        cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    registration = _fetch_one(
        cursor
    )

    if not registration:
        raise LookupError(
            "Invitation registration was not found for "
            f"{prospect_id}. Send the invitation first."
        )

    return registration

def _update_main_registration(
    cursor: pyodbc.Cursor,
    payload: VendorSaveRequest,
    registration: dict[str, Any],
    prospect_id: str,
    registration_status: str,
    is_draft: int,
) -> None:
    general = payload.generalInformation
    financial = payload.financialCommercial
    declaration = payload.declaration
    # general   = payload.generalInformation
    # financial = payload.financialCommercial

    # declaration = payload.declaration

    company_name = _clean_text(
        general.COMPANYNAME
    )

    if company_name is None:
        company_name = (
            registration.get(
                "CompanyName"
            )
            or ""
        )

    _execute(
        cursor,
        f"""
        UPDATE {REGISTRATION_TABLE}
        SET
            CompanyName = ?,
            CompanyRegNumber = ?,
            NatureOfCompany = ?,
            NatureOfBusiness = ?,
            ScopeOfSupply = ?,
            YearOfEstablishment = ?,
            NumberOfEmployees = ?,
            CountryOfOrigin = ?,

            Street = ?,
            City = ?,
            District = ?,
            State = ?,
            Country = ?,
            ZipCode = ?,
            Location = ?,
            WorkingTimeZone = ?,
            WeeklyHoliday = ?,

            SupplierType = ?,
            Currency = ?,
            PaymentTerms = ?,
            DeliveryTerms = ?,
            
            BankName = ?,
            BankAddress = ?,
            BankAccountNumber = ?,
            IFSCCode = ?,
            IFCCode = ?,
            BeneficiaryName = ?,
            BeneficiaryAddress = ?,
            SwiftCode = ?,
            IBAN = ?,
            BankBranchCode = ?,
            BankContactNumber = ?,
            PANNumber = ?,
            RegistrationType = ?,
            RegistrationNumber = ?,
            FactoryLicenseNumber = ?,

            IsAuthorizedDistributor = ?,

            RepTitle = ?,
            RepName = ?,
            RepDesignation = ?,
            RepEmail = ?,
            RepMobileNumber = ?,
            DeclarationAgreed = ?,
            AuthorizedToSubmit = ?,
            AcceptTerms = ?,

            Status = ?,
            IsDraft = ?,

            DateOfSubmission =
                CASE
                    WHEN ? = 1
                    THEN COALESCE(
                        DateOfSubmission,
                        CAST(GETDATE() AS DATE)
                    )
                    ELSE DateOfSubmission
                END,

            ModifiedOn = GETDATE()

        WHERE ProspectId = ?
        """,
        (
            company_name,
            _clean_text(
                general.COMPANYREGISTERNUMBER
            ),
            _clean_text(
                general.NATUREOFCOMPANY
            ),
            _clean_text(
                general.NATUREOFBUSINESS
            ),
            _clean_text(
                general.SCOPEOFSUPPLY
            ),
            _clean_text(
                general.YEAROFESTABLISHMENT
            ),
           _clean_text(
                general.NUMBEROFEMPLOYEES
            ),
            _clean_text(
                general.COUNTRYOFORIGIN
            ),

            _clean_text(
                general.STREET
            ),
            _clean_text(
                general.CITY
            ),
            _clean_text(
                general.DISTRICT
            ),
            _clean_text(
                general.STATE
            ),
            _clean_text(
                general.COUNTRY
            ),
            _clean_text(
                general.POSTALCODE
            ),
            _clean_text_list_json(
                general.CURRENTSUPPLYLOCATION
            ),
            _clean_text(
                general.WORKINGTIMEZONE
            ),
            _clean_text(
                general.WEEKLYHOLIDAY
            ),
            _clean_text(
                financial.SUPPLIERTYPE
            ),
            _clean_text(
                financial.CURRENCY
            ),
            _clean_text(
                financial.PAYMENTTERMS
            ),
            _clean_text(
                financial.DELIVERYTERMS
            ),
            _clean_text(
                financial.BANKNAME
            ),
            _clean_text(
                financial.BANKADDRESS
            ),
            _clean_text(
                financial.BANKACCOUNTNUMBER
            ),
            _clean_text(
                financial.IFSCCODE
            ),
           _clean_text(
                getattr(
                    financial,
                    "IFCCODE",
                    None,
                )
            ),
            _clean_text(
                financial.BENEFICIARYNAME
            ),
            _clean_text(
                getattr(
                    financial,
                    "BENEFICIARYADDRESS",
                    None,
                )
            ),
            _clean_text(
                getattr(
                    financial,
                    "SWIFTCODE",
                    None,
                )
            ),
            _clean_text(
                getattr(
                    financial,
                    "IBAN",
                    None,
                )
            ),
            _clean_text(
                getattr(
                    financial,
                    "BANKBRANCHCODE",
                    None,
                )
            ),
            _clean_text(
                financial.BANKCONTACTNUMBER
            ),
            _clean_text(
                financial.PANNUMBER
            ),
            _clean_text(
                financial.REGISTRATIONTYPE
            ),
            _clean_text(
                financial.REGISTRATIONNUMBER
            ),
            _clean_text(
                financial.FACTORYLICENCENUMBER
            ),

            _yes_no_to_bit(
                payload.DISTRIBUTORAUTHORIZATION
            ),

            # _clean_text(
            #     general.SUPPLIERTYPE
            #     or financial.SUPPLIERTYPE
            # ),
            # _clean_text(
            #     general.PAYMENTTERMS
            #     or financial.PAYMENTTERMS
            # ),
            # _clean_text(
            #     general.DELIVERYTERMS
            #     or financial.DELIVERYTERMS
            # ),
            # _clean_text(
            #     general.BANKNAME
            #     or financial.BANKNAME
            # ),
            # _clean_text(
            #     general.BANKADDRESS
            #     or financial.BANKADDRESS
            # ),
            # _clean_text(
            #     general.PANNUMBER
            #     or financial.PANNUMBER
            # ),
            # _clean_text(
            #     general.REGISTRATIONTYPE
            #     or financial.REGISTRATIONTYPE
            # ),
            # _clean_text(
            #     general.REGISTRATIONNUMBER
            #     or financial.REGISTRATIONNUMBER
            # ),
            # _clean_text(
            #     general.FACTORYLICENSENUMBER
            #     or financial.FACTORYLICENCENUMBER
            # ),

            # _yes_no_to_bit(
            #     general
            #     .DISTRIBUTORAUTHORIZATION
            # ),

            _clean_text(
                declaration.TITLE
            ),
            _clean_text(
                declaration.NAME
            ),
            _clean_text(
                declaration.DESIGNATION
            ),
            _clean_text(
                declaration.EMAIL
            ),
            _clean_text(
                declaration.MOBILENUMBER
            ),
            _yes_no_to_bit(
                declaration
                .AGREEDTODECLARATION
            ),
            _yes_no_to_bit(
                declaration
                .AUTHORIZEDCONFIRM
            ),
            _yes_no_to_bit(
                getattr(
                    declaration,
                    "ACCEPTTERMS",
                    None,
                )
            ),

            registration_status,
            is_draft,
            is_draft,
            prospect_id,
        ),
    )


# ============================================================
# CONTACT SYNCHRONIZATION
# ============================================================

def _sync_contacts(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> tuple[
    list[dict[str, Any]],
    list[int],
]:

    contacts = (
        payload.contacts
        or []
    )

    existing_ids = _get_existing_ids(
        cursor,
        CONTACT_TABLE,
        "ContactId",
        prospect_id,
    )

    incoming_ids = [
        contact_id
        for contact in contacts
        if (
            contact_id := _normalize_id(
                contact.CONTACTID
            )
        ) is not None
    ]

    _validate_unique_ids(
        incoming_ids,
        "CONTACTID",
    )

    unknown_ids = (
        set(incoming_ids)
        - existing_ids
    )

    if unknown_ids:
        raise LookupError(
            "The following CONTACTID values were not "
            f"found for {prospect_id}: "
            f"{sorted(unknown_ids)}"
        )

    deleted_ids = _delete_ids(
        cursor=cursor,
        table_name=CONTACT_TABLE,
        id_column="ContactId",
        prospect_id=prospect_id,
        record_ids=(
            existing_ids
            - set(incoming_ids)
        ),
    )

    if any(
        contact.ISPRIMARY is True
        for contact in contacts
    ):
        _execute(
            cursor,
            f"""
            UPDATE {CONTACT_TABLE}
            SET IsPrimary = 0
            WHERE ProspectId = ?
            """,
            (prospect_id,),
        )

    saved: list[
        dict[str, Any]
    ] = []

    for index, contact in enumerate(
        contacts
    ):
        contact_id = _normalize_id(
            contact.CONTACTID
        )

        contact_name = _clean_text(
            contact.CONTACTPERSONNAME
        )

        if contact_id is not None:
            _execute(
                cursor,
                f"""
                UPDATE {CONTACT_TABLE}
                SET
                    ContactName = ?,
                    Designation = ?,
                    EmailAddress = ?,
                    MobileNumber = ?,
                    LandlineNumber = ?,
                    IsPrimary = ?
                WHERE ContactId = ?
                  AND ProspectId = ?
                """,
                (
                    contact_name,
                    _clean_text(
                        contact.DESIGNATION
                    ),
                    _clean_text(
                        contact.EMAIL
                    ),
                    _clean_text(
                        contact.MOBILENUMBER
                    ),
                    _clean_text(
                        getattr(
                            contact,
                            "LANLINE",
                            None,
                        )
                    ),
                    _yes_no_to_bit(
                        contact.ISPRIMARY
                    ),
                    contact_id,
                    prospect_id,
                ),
            )

            saved_id = contact_id

        else:
            if not contact_name:
                continue

            row = _execute(
                cursor,
                f"""
                INSERT INTO {CONTACT_TABLE}
                (
                    ProspectSeq,
                    ProspectId,
                    ContactName,
                    Designation,
                    EmailAddress,
                    MobileNumber,
                    LandlineNumber,
                    IsPrimary
                )
                OUTPUT INSERTED.ContactId
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prospect_seq,
                    prospect_id,
                    contact_name,
                    _clean_text(
                        contact.DESIGNATION
                    ),
                    _clean_text(
                        contact.EMAIL
                    ),
                    _clean_text(
                        contact.MOBILENUMBER
                    ),
                    _clean_text(
                        getattr(
                            contact,
                            "LANLINE",
                            None,
                        )
                    ),
                    _yes_no_to_bit(
                        contact.ISPRIMARY
                    ),
                ),
            ).fetchone()

            saved_id = int(
                row[0]
            )

        saved.append(
            {
                "INDEX": index,
                "CONTACTID": saved_id,
            }
        )

    return saved, deleted_ids


# ============================================================
# BUSINESS REFERENCE SYNCHRONIZATION
# ============================================================

def _sync_business_references(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> tuple[
    list[dict[str, Any]],
    list[int],
]:

    customers = (
        payload.businessReference
        or []
    )

    existing_ids = _get_existing_ids(
        cursor,
        CUSTOMER_TABLE,
        "CustomerId",
        prospect_id,
    )

    incoming_ids = [
        customer_id
        for customer in customers
        if (
            customer_id := _normalize_id(
                customer.CUSTOMERID
            )
        ) is not None
    ]

    _validate_unique_ids(
        incoming_ids,
        "CUSTOMERID",
    )

    unknown_ids = (
        set(incoming_ids)
        - existing_ids
    )

    if unknown_ids:
        raise LookupError(
            "The following CUSTOMERID values were not "
            f"found for {prospect_id}: "
            f"{sorted(unknown_ids)}"
        )

    deleted_ids = _delete_ids(
        cursor=cursor,
        table_name=CUSTOMER_TABLE,
        id_column="CustomerId",
        prospect_id=prospect_id,
        record_ids=(
            existing_ids
            - set(incoming_ids)
        ),
    )

    saved: list[
        dict[str, Any]
    ] = []

    for index, customer in enumerate(
        customers
    ):
        customer_id = _normalize_id(
            customer.CUSTOMERID
        )

        customer_name = _clean_text(
            customer.CUTOMERNAME
        )

        if customer_id is not None:
            _execute(
                cursor,
                f"""
                UPDATE {CUSTOMER_TABLE}
                SET
                    CustomerName = ?,
                    Industry = ?,
                    Country = ?
                WHERE CustomerId = ?
                  AND ProspectId = ?
                """,
                (
                    customer_name,
                    _clean_text(
                        customer.INDUSTRY
                    ),
                    _clean_text(
                        customer.COUNTRY
                    ),
                    customer_id,
                    prospect_id,
                ),
            )

            saved_id = customer_id

        else:
            if not customer_name:
                continue

            row = _execute(
                cursor,
                f"""
                INSERT INTO {CUSTOMER_TABLE}
                (
                    ProspectSeq,
                    ProspectId,
                    CustomerName,
                    Industry,
                    Country
                )
                OUTPUT INSERTED.CustomerId
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    prospect_seq,
                    prospect_id,
                    customer_name,
                    _clean_text(
                        customer.INDUSTRY
                    ),
                    _clean_text(
                        customer.COUNTRY
                    ),
                ),
            ).fetchone()

            saved_id = int(
                row[0]
            )

        saved.append(
            {
                "INDEX": index,
                "CUSTOMERID": saved_id,
            }
        )

    return saved, deleted_ids


# ============================================================
# OEM SYNCHRONIZATION
# ============================================================

def _sync_oems(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
    list[int],
]:

    oems = (
        payload.oemdetails
        or []
    )

    existing_ids = _get_existing_ids(
        cursor,
        OEM_TABLE,
        "OEMId",
        prospect_id,
    )

    incoming_ids = [
        oem_id
        for oem in oems
        if (
            oem_id := _normalize_id(
                oem.OEMID
            )
        ) is not None
    ]

    _validate_unique_ids(
        incoming_ids,
        "OEMID",
    )

    unknown_ids = (
        set(incoming_ids)
        - existing_ids
    )

    if unknown_ids:
        raise LookupError(
            "The following OEMID values were not found "
            f"for {prospect_id}: "
            f"{sorted(unknown_ids)}"
        )

    ids_to_delete = (
        existing_ids
        - set(incoming_ids)
    )

    if ids_to_delete:
        placeholders = ", ".join(
            "?"
            for _ in ids_to_delete
        )

        _execute(
            cursor,
            f"""
            DELETE FROM {ATTACHMENT_TABLE}
            WHERE ProspectId = ?
              AND AttachmentFor = 'OEMCert'
              AND ParentRecordId IN ({placeholders})
            """,
            (
                prospect_id,
                *sorted(ids_to_delete),
            ),
        )

    deleted_ids = _delete_ids(
        cursor=cursor,
        table_name=OEM_TABLE,
        id_column="OEMId",
        prospect_id=prospect_id,
        record_ids=ids_to_delete,
    )

    saved: list[
        dict[str, Any]
    ] = []

    client_key_map: dict[
        str,
        int
    ] = {}

    for index, oem in enumerate(
        oems
    ):
        oem_id = _normalize_id(
            oem.OEMID
        )

        oem_name = _clean_text(
            oem.OEMNAME
        )

        client_key = (
            _clean_text(
                oem.CLIENTKEY
            )
            or f"OEM_{index + 1}"
        )

        if oem_id is not None:
            _execute(
                cursor,
                f"""
                UPDATE {OEM_TABLE}
                SET OEMName = ?
                WHERE OEMId = ?
                  AND ProspectId = ?
                """,
                (
                    oem_name,
                    oem_id,
                    prospect_id,
                ),
            )

            saved_id = oem_id

        else:
            if not oem_name:
                continue

            row = _execute(
                cursor,
                f"""
                INSERT INTO {OEM_TABLE}
                (
                    ProspectSeq,
                    ProspectId,
                    OEMName
                )
                OUTPUT INSERTED.OEMId
                VALUES (?, ?, ?)
                """,
                (
                    prospect_seq,
                    prospect_id,
                    oem_name,
                ),
            ).fetchone()

            saved_id = int(
                row[0]
            )

        if client_key in client_key_map:
            raise ValueError(
                f"Duplicate OEM CLIENTKEY: {client_key}"
            )

        client_key_map[
            client_key
        ] = saved_id

        # Save OEM CERTIFICATE (base64) to HIQ_VendorFileAttachment
        if _clean_text(getattr(oem, "CERTIFICATE", None)):
            _save_oem_certificate_base64(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                oem_id=saved_id,
                certificate_b64=_clean_text(
                    oem.CERTIFICATE
                ),
                certificate_name=_clean_text(
                    oem.CERTIFICATENAME
                ),
            )

        saved.append(
            {
                "INDEX": index,
                "OEMID": saved_id,
                "CLIENTKEY": client_key,
            }
        )

    return (
        saved,
        client_key_map,
        deleted_ids,
    )


# ============================================================
# OEM CERTIFICATE BASE64 → HIQ_VendorFileAttachment
# ============================================================

def _save_oem_certificate_base64(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    oem_id: int,
    certificate_b64: str,
    certificate_name: str | None,
) -> None:
    """Save an OEM certificate using the filename sent by the frontend."""

    (
        file_name,
        file_extension,
        content_type,
        file_bytes,
    ) = _prepare_frontend_base64_file(
        base64_value=certificate_b64,
        frontend_file_name=certificate_name,
        content_field_name="CERTIFICATE",
        file_name_field_name="CERTIFICATENAME",
    )

    _execute(
        cursor,
        f"""
        DELETE FROM {ATTACHMENT_TABLE}
        WHERE ProspectId = ?
          AND AttachmentFor = 'OEMCert'
          AND ParentRecordId = ?
        """,
        (
            prospect_id,
            oem_id,
        ),
    )

    _execute(
        cursor,
        f"""
        INSERT INTO {ATTACHMENT_TABLE}
        (
            ProspectSeq,
            ProspectId,
            AttachmentFor,
            ParentRecordId,
            DocumentId,
            FileName,
            FileExtension,
            ContentType,
            FileSizeBytes,
            FileContent,
            ReceivedFromD365,
            CreatedDateTime,
            ModifiedDateTime
        )
        VALUES
        (
            ?, ?,
            'OEMCert',
            ?,
            NEWID(),
            ?, ?, ?,
            ?,
            ?,
            0,
            GETDATE(),
            GETDATE()
        )
        """,
        (
            prospect_seq,
            prospect_id,
            oem_id,
            file_name,
            file_extension,
            content_type,
            len(file_bytes),
            pyodbc.Binary(file_bytes),
        ),
    )


# ============================================================
# CERTIFICATION SYNCHRONIZATION
# ============================================================

def _sync_certifications(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
    list[int],
]:

    certifications = (
        payload.certification
        or []
    )

    existing_ids = _get_existing_ids(
        cursor,
        CERTIFICATION_TABLE,
        "CertificationId",
        prospect_id,
    )

    incoming_ids = [
        certification_id
        for certification
        in certifications
        if (
            certification_id := _normalize_id(
                certification
                .CERTIFICATIONID
            )
        ) is not None
    ]

    _validate_unique_ids(
        incoming_ids,
        "CERTIFICATIONID",
    )

    unknown_ids = (
        set(incoming_ids)
        - existing_ids
    )

    if unknown_ids:
        raise LookupError(
            "The following CERTIFICATIONID values "
            f"were not found for {prospect_id}: "
            f"{sorted(unknown_ids)}"
        )

    ids_to_delete = (
        existing_ids
        - set(incoming_ids)
    )

    if ids_to_delete:
        placeholders = ", ".join(
            "?"
            for _ in ids_to_delete
        )

        _execute(
            cursor,
            f"""
            DELETE FROM {ATTACHMENT_TABLE}
            WHERE ProspectId = ?
              AND AttachmentFor = 'Certification'
              AND ParentRecordId IN ({placeholders})
            """,
            (
                prospect_id,
                *sorted(ids_to_delete),
            ),
        )

    deleted_ids = _delete_ids(
        cursor=cursor,
        table_name=CERTIFICATION_TABLE,
        id_column="CertificationId",
        prospect_id=prospect_id,
        record_ids=ids_to_delete,
    )

    saved: list[
        dict[str, Any]
    ] = []

    client_key_map: dict[
        str,
        int
    ] = {}

    for index, certification in enumerate(
        certifications
    ):
        certification_id = _normalize_id(
            certification
            .CERTIFICATIONID
        )

        certification_type = _clean_text(
            certification
            .CERTIFICATIONTYPE
        )

        client_key = (
            _clean_text(
                certification.CLIENTKEY
            )
            or f"CERT_{index + 1}"
        )

        valid_until = (
            _parse_valid_until(
                certification.VALIDUNTIL
            )
        )

        if certification_id is not None:
            _execute(
                cursor,
                f"""
                UPDATE {CERTIFICATION_TABLE}
                SET
                    CertificationType = ?,
                    CertStatus = ?,
                    CertificateNumber = ?,
                    ValidUntil = ?
                WHERE CertificationId = ?
                  AND ProspectId = ?
                """,
                (
                    certification_type,
                    _clean_text(
                        certification.STATUS
                    ),
                    _clean_text(
                        certification
                        .CERTIFICATIONNUMBER
                    ),
                    valid_until,
                    certification_id,
                    prospect_id,
                ),
            )

            saved_id = certification_id

        else:
            if not certification_type:
                continue

            row = _execute(
                cursor,
                f"""
                INSERT INTO {CERTIFICATION_TABLE}
                (
                    ProspectSeq,
                    ProspectId,
                    CertificationType,
                    CertStatus,
                    CertificateNumber,
                    ValidUntil
                )
                OUTPUT INSERTED.CertificationId
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    prospect_seq,
                    prospect_id,
                    certification_type,
                    _clean_text(
                        certification.STATUS
                    ),
                    _clean_text(
                        certification
                        .CERTIFICATIONNUMBER
                    ),
                    valid_until,
                ),
            ).fetchone()

            saved_id = int(
                row[0]
            )

        if client_key in client_key_map:
            raise ValueError(
                "Duplicate certification "
                f"CLIENTKEY: {client_key}"
            )

        client_key_map[
            client_key
        ] = saved_id

        if _clean_text(
            getattr(
                certification,
                "ATTACHMENT",
                None,
            )
        ):
            _save_certification_attachment_base64(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                certification_id=saved_id,
                attachment_b64=_clean_text(
                    certification.ATTACHMENT
                ),
                attachment_name=_clean_text(
                    certification.ATTACHMENTNAME
                ),
            )

        saved.append(
            {
                "INDEX": index,
                "CERTIFICATIONID":
                    saved_id,
                "CLIENTKEY":
                    client_key,
            }
        )

    return (
        saved,
        client_key_map,
        deleted_ids,
    )


def _save_certification_attachment_base64(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    certification_id: int,
    attachment_b64: str,
    attachment_name: str | None,
) -> None:
    """Save a certification file using the filename sent by the frontend."""

    (
        file_name,
        file_extension,
        content_type,
        file_bytes,
    ) = _prepare_frontend_base64_file(
        base64_value=attachment_b64,
        frontend_file_name=attachment_name,
        content_field_name="ATTACHMENT",
        file_name_field_name="ATTACHMENTNAME",
    )

    _execute(
        cursor,
        f"""
        DELETE FROM {ATTACHMENT_TABLE}
        WHERE ProspectId = ?
          AND AttachmentFor = 'Certification'
          AND ParentRecordId = ?
        """,
        (
            prospect_id,
            certification_id,
        ),
    )

    _execute(
        cursor,
        f"""
        INSERT INTO {ATTACHMENT_TABLE}
        (
            ProspectSeq,
            ProspectId,
            AttachmentFor,
            ParentRecordId,
            DocumentId,
            FileName,
            FileExtension,
            ContentType,
            FileSizeBytes,
            FileContent,
            ReceivedFromD365,
            CreatedDateTime,
            ModifiedDateTime
        )
        VALUES
        (
            ?, ?,
            'Certification',
            ?,
            NEWID(),
            ?, ?, ?,
            ?,
            ?,
            0,
            GETDATE(),
            GETDATE()
        )
        """,
        (
            prospect_seq,
            prospect_id,
            certification_id,
            file_name,
            file_extension,
            content_type,
            len(file_bytes),
            pyodbc.Binary(file_bytes),
        ),
    )


# ============================================================
# LEGAL DOCUMENT SYNCHRONIZATION
# ============================================================

def _sync_legal_documents(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> tuple[
    list[dict[str, Any]],
    dict[str, int],
    list[int],
]:
    legal_documents = (
        payload.legaldocument
        or []
    )

    existing_ids = _get_existing_ids(
        cursor,
        LEGAL_DOCUMENT_TABLE,
        "LegalDocumentId",
        prospect_id,
    )

    incoming_ids = [
        legal_document_id
        for document in legal_documents
        if (
            legal_document_id := _normalize_id(
                document.LEGALDOCUMENTID
            )
        ) is not None
    ]

    _validate_unique_ids(
        incoming_ids,
        "LEGALDOCUMENTID",
    )

    unknown_ids = (
        set(incoming_ids)
        - existing_ids
    )

    if unknown_ids:
        raise LookupError(
            "The following LEGALDOCUMENTID values "
            f"were not found for {prospect_id}: "
            f"{sorted(unknown_ids)}"
        )

    ids_to_delete = (
        existing_ids
        - set(incoming_ids)
    )

    if ids_to_delete:
        placeholders = ", ".join(
            "?"
            for _ in ids_to_delete
        )

        _execute(
            cursor,
            f"""
            DELETE FROM {ATTACHMENT_TABLE}
            WHERE ProspectId = ?
              AND AttachmentFor = 'LegalDocument'
              AND ParentRecordId IN ({placeholders})
            """,
            (
                prospect_id,
                *sorted(ids_to_delete),
            ),
        )

    deleted_ids = _delete_ids(
        cursor=cursor,
        table_name=LEGAL_DOCUMENT_TABLE,
        id_column="LegalDocumentId",
        prospect_id=prospect_id,
        record_ids=ids_to_delete,
    )

    saved: list[
        dict[str, Any]
    ] = []

    client_key_map: dict[
        str,
        int
    ] = {}

    for index, document in enumerate(
        legal_documents
    ):
        legal_document_id = _normalize_id(
            document.LEGALDOCUMENTID
        )

        document_name = _clean_text(
            document.DOCUMENTNAME
        )

        client_key = (
            _clean_text(
                document.CLIENTKEY
            )
            or f"LEGAL_{index + 1}"
        )

        if legal_document_id is not None:
            _execute(
                cursor,
                f"""
                UPDATE {LEGAL_DOCUMENT_TABLE}
                SET
                    DocumentName = ?,
                    ModifiedAt = SYSUTCDATETIME()
                WHERE LegalDocumentId = ?
                  AND ProspectId = ?
                """,
                (
                    document_name,
                    legal_document_id,
                    prospect_id,
                ),
            )

            saved_id = legal_document_id

        else:
            if not document_name:
                continue

            row = _execute(
                cursor,
                f"""
                INSERT INTO {LEGAL_DOCUMENT_TABLE}
                (
                    ProspectSeq,
                    ProspectId,
                    DocumentName,
                    CreatedAt,
                    ModifiedAt
                )
                OUTPUT INSERTED.LegalDocumentId
                VALUES
                (
                    ?,
                    ?,
                    ?,
                    SYSUTCDATETIME(),
                    SYSUTCDATETIME()
                )
                """,
                (
                    prospect_seq,
                    prospect_id,
                    document_name,
                ),
            ).fetchone()

            if row is None:
                raise RuntimeError(
                    "Legal document could not be created"
                )

            saved_id = int(
                row[0]
            )

        if client_key in client_key_map:
            raise ValueError(
                "Duplicate legal-document "
                f"CLIENTKEY: {client_key}"
            )

        client_key_map[
            client_key
        ] = saved_id

        legal_file = _clean_text(
            getattr(
                document,
                "LEGALFILE",
                None,
            )
        )

        if legal_file:
            _save_legal_document_attachment_base64(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                legal_document_id=saved_id,
                legal_file_b64=legal_file,
                legal_file_name=_clean_text(
                    document.LEGALFILENAME
                ),
            )

        saved.append(
            {
                "INDEX": index,
                "LEGALDOCUMENTID": saved_id,
                "CLIENTKEY": client_key,
            }
        )

    return (
        saved,
        client_key_map,
        deleted_ids,
    )


def _save_legal_document_attachment_base64(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    legal_document_id: int,
    legal_file_b64: str,
    legal_file_name: str | None,
) -> None:
    (
        file_name,
        file_extension,
        content_type,
        file_bytes,
    ) = _prepare_frontend_base64_file(
        base64_value=legal_file_b64,
        frontend_file_name=legal_file_name,
        content_field_name="LEGALFILE",
        file_name_field_name="LEGALFILENAME",
    )

    _execute(
        cursor,
        f"""
        DELETE FROM {ATTACHMENT_TABLE}
        WHERE ProspectId = ?
          AND AttachmentFor = 'LegalDocument'
          AND ParentRecordId = ?
        """,
        (
            prospect_id,
            legal_document_id,
        ),
    )

    _execute(
        cursor,
        f"""
        INSERT INTO {ATTACHMENT_TABLE}
        (
            ProspectSeq,
            ProspectId,
            AttachmentFor,
            ParentRecordId,
            DocumentId,
            FileName,
            FileExtension,
            ContentType,
            FileSizeBytes,
            FileContent,
            ReceivedFromD365,
            CreatedDateTime,
            ModifiedDateTime
        )
        VALUES
        (
            ?,
            ?,
            'LegalDocument',
            ?,
            NEWID(),
            ?,
            ?,
            ?,
            ?,
            ?,
            0,
            GETDATE(),
            GETDATE()
        )
        """,
        (
            prospect_seq,
            prospect_id,
            legal_document_id,
            file_name,
            file_extension,
            content_type,
            len(file_bytes),
            pyodbc.Binary(file_bytes),
        ),
    )


# ============================================================
# QUESTIONNAIRE UPSERT
# ============================================================
def _save_questionnaire(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> None:

    questionnaire = payload.qualityQuestionnaire

    values = (
        questionnaire_to_varchar(
            questionnaire.INSTRUMENTSCALIBRATED
        ),
        _clean_text(
            questionnaire.INSTRUMENTSCALIBRATEDREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.ADEQUATEEQUIPMENT
        ),
        _clean_text(
            questionnaire.ADEQUATEEQUIPMENTREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.DEDICATEDQUALITYFUNCTION
        ),
        _clean_text(
            questionnaire.DEDICATEDQUALITYFUNCTIONREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.SUPPLYCOC
        ),
        _clean_text(
            questionnaire.SUPPLYCOCREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.RETAINCOC15YEARS
        ),
        _clean_text(
            questionnaire.RETAINCOC15YEARSREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.CORRECTIVEACTIONSYSTEM
        ),
        _clean_text(
            questionnaire.CORRECTIVEACTIONSYSTEMREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.PACKINGENSURESNODAMAGE
        ),
        _clean_text(
            questionnaire.PACKINGENSURESNODAMAGEREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.MEETSHELFLIFEREQUIREMENTS
        ),
        _clean_text(
            questionnaire.MEETSHELFLIFEREQUIREMENTSREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.EMPLOYEESAWAREETHICS
        ),
        _clean_text(
            questionnaire.EMPLOYEESAWAREETHICSREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.PREVENTCOUNTERFEITPARTS
        ),
        _clean_text(
            questionnaire.PREVENTCOUNTERFEITPARTSREMARK
        ),

        questionnaire_to_varchar(
            questionnaire.NOTIFYORGANIZATIONCHANGES
        ),
        _clean_text(
            questionnaire.NOTIFYORGANIZATIONCHANGESREMARK
        ),
    )

    _execute(
        cursor,
        f"""
        SELECT 1
        FROM {QUESTIONNAIRE_TABLE}
        WHERE ProspectId = ?
        """,
        (
            prospect_id,
        ),
    )

    exists = cursor.fetchone() is not None

    if exists:
        _execute(
            cursor,
            f"""
            UPDATE {QUESTIONNAIRE_TABLE}
            SET
                InstrumentsCalibrated = ?,
                InstrumentsCalibratedRemark = ?,

                AdequateEquipmentManpower = ?,
                AdequateEquipmentManpowerRemark = ?,

                DedicatedQualityFunction = ?,
                DedicatedQualityFunctionRemark = ?,

                SupplyCoC = ?,
                SupplyCoCRemark = ?,

                RetainCoCMinimum15Yrs = ?,
                RetainCoCMinimum15YrsRemark = ?,

                CorrectiveActionSystem = ?,
                CorrectiveActionSystemRemark = ?,

                PackingNoDamage = ?,
                PackingNoDamageRemark = ?,

                MeetShelfLife = ?,
                MeetShelfLifeRemark = ?,

                EthicalBehaviorAwareness = ?,
                EthicalBehaviorAwarenessRemark = ?,

                CounterfeitPartPrevention = ?,
                CounterfeitPartPreventionRemark = ?,

                NotifyOrganisationChanges = ?,
                NotifyOrganisationChangesRemark = ?
            WHERE ProspectId = ?
            """,
            (
                *values,
                prospect_id,
            ),
        )

        return

    _execute(
        cursor,
        f"""
        INSERT INTO {QUESTIONNAIRE_TABLE}
        (
            ProspectSeq,
            ProspectId,

            InstrumentsCalibrated,
            InstrumentsCalibratedRemark,

            AdequateEquipmentManpower,
            AdequateEquipmentManpowerRemark,

            DedicatedQualityFunction,
            DedicatedQualityFunctionRemark,

            SupplyCoC,
            SupplyCoCRemark,

            RetainCoCMinimum15Yrs,
            RetainCoCMinimum15YrsRemark,

            CorrectiveActionSystem,
            CorrectiveActionSystemRemark,

            PackingNoDamage,
            PackingNoDamageRemark,

            MeetShelfLife,
            MeetShelfLifeRemark,

            EthicalBehaviorAwareness,
            EthicalBehaviorAwarenessRemark,

            CounterfeitPartPrevention,
            CounterfeitPartPreventionRemark,

            NotifyOrganisationChanges,
            NotifyOrganisationChangesRemark
        )
        VALUES
        (
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?
        )
        """,
        (
            prospect_seq,
            prospect_id,
            *values,
        ),
    )


# ============================================================
# RAW BINARY ATTACHMENT HELPERS
# ============================================================

def _validate_binary_file(
    file_data: dict[str, Any],
) -> tuple[
    str,
    str,
    str,
    bytes,
]:

    file_name = Path(
        file_data.get(
            "FILE_NAME"
        )
        or ""
    ).name

    if not file_name:
        raise ValueError(
            "Uploaded file name is missing"
        )

    extension = Path(
        file_name
    ).suffix.lower()

    if (
        extension
        not in ALLOWED_FILE_EXTENSIONS
    ):
        raise ValueError(
            f"{file_name}: only PDF, JPG, JPEG "
            "and PNG files are allowed"
        )

    content_type = str(
        file_data.get(
            "CONTENT_TYPE"
        )
        or "application/octet-stream"
    ).lower()

    if (
        content_type
        not in ALLOWED_CONTENT_TYPES
    ):
        raise ValueError(
            f"{file_name}: unsupported content type "
            f"{content_type}"
        )

    file_bytes = (
        file_data.get(
            "FILE_BYTES"
        )
        or b""
    )

    if not isinstance(
        file_bytes,
        bytes,
    ):
        raise ValueError(
            f"{file_name}: invalid binary content"
        )

    if not file_bytes:
        raise ValueError(
            f"{file_name} is empty"
        )

    if (
        len(file_bytes)
        > MAX_FILE_SIZE_BYTES
    ):
        raise ValueError(
            f"{file_name} exceeds the 20 MB limit"
        )

    return (
        file_name,
        extension,
        content_type,
        file_bytes,
    )


def _resolve_attachment_parent(
    cursor: pyodbc.Cursor,
    prospect_id: str,
    metadata: AttachmentMetadata,
    oem_key_map: dict[str, int],
    certification_key_map: dict[str, int],
    legal_document_key_map: dict[str, int],
) -> int | None:

    attachment_for = _validate_attachment_for(
        metadata.ATTACHMENT_FOR
    )

    if attachment_for == "FactoryLicense":
        return None

    parent_record_id = _normalize_id(
        metadata.PARENT_RECORD_ID
    )

    parent_client_key = _clean_text(
        metadata.PARENT_CLIENT_KEY
    )

    if (
        parent_record_id is None
        and parent_client_key
    ):
        if attachment_for == "OEMCert":
            parent_record_id = oem_key_map.get(
                parent_client_key
            )

        elif attachment_for == "Certification":
            parent_record_id = certification_key_map.get(
                parent_client_key
            )

        elif attachment_for == "LegalDocument":
            parent_record_id = legal_document_key_map.get(
                parent_client_key
            )

    if parent_record_id is None:
        raise ValueError(
            f"{attachment_for} requires "
            "PARENT_RECORD_ID or PARENT_CLIENT_KEY"
        )

    if attachment_for == "OEMCert":
        table_name = OEM_TABLE
        id_column = "OEMId"

    elif attachment_for == "Certification":
        table_name = CERTIFICATION_TABLE
        id_column = "CertificationId"

    elif attachment_for == "LegalDocument":
        table_name = LEGAL_DOCUMENT_TABLE
        id_column = "LegalDocumentId"

    else:
        raise ValueError(
            "ATTACHMENT_FOR must be FactoryLicense, "
            "OEMCert, Certification or LegalDocument"
        )

    _execute(
        cursor,
        f"""
        SELECT 1
        FROM {table_name}
        WHERE [{id_column}] = ?
          AND ProspectId = ?
        """,
        (
            parent_record_id,
            prospect_id,
        ),
    )

    if cursor.fetchone() is None:
        raise ValueError(
            f"The parent record for {attachment_for} "
            f"does not belong to {prospect_id}"
        )

    return parent_record_id


def _sync_attachments(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    attachment_metadata: list[
        AttachmentMetadata
    ],
    uploaded_files: list[
        dict[str, Any]
    ],
    oem_key_map: dict[str, int],
    certification_key_map: dict[str, int],
    legal_document_key_map: dict[str, int],
) -> tuple[
    list[dict[str, Any]],
    list[int],
]:

    existing_ids = _get_existing_ids(
        cursor,
        ATTACHMENT_TABLE,
        "Id",
        prospect_id,
    )

    incoming_ids = [
        attachment_id
        for metadata
        in attachment_metadata
        if (
            attachment_id := _normalize_id(
                metadata.ATTACHMENT_ID
            )
        ) is not None
    ]

    _validate_unique_ids(
        incoming_ids,
        "ATTACHMENT_ID",
    )

    unknown_ids = (
        set(incoming_ids)
        - existing_ids
    )

    if unknown_ids:
        raise LookupError(
            "The following ATTACHMENT_ID values "
            f"were not found for {prospect_id}: "
            f"{sorted(unknown_ids)}"
        )

    deleted_ids = _delete_ids(
        cursor=cursor,
        table_name=ATTACHMENT_TABLE,
        id_column="Id",
        prospect_id=prospect_id,
        record_ids=(
            existing_ids
            - set(incoming_ids)
        ),
    )

    file_map: dict[
        int,
        dict[str, Any]
    ] = {}

    for file_data in uploaded_files:
        file_index = int(
            file_data[
                "FILE_INDEX"
            ]
        )

        if file_index in file_map:
            raise ValueError(
                "Duplicate uploaded FILE_INDEX: "
                f"{file_index}"
            )

        file_map[
            file_index
        ] = file_data

    metadata_file_indexes = [
        int(metadata.FILE_INDEX)
        for metadata
        in attachment_metadata
        if metadata.FILE_INDEX
        is not None
    ]

    _validate_unique_ids(
        metadata_file_indexes,
        "FILE_INDEX",
    )

    missing_files = (
        set(metadata_file_indexes)
        - set(file_map.keys())
    )

    if missing_files:
        raise ValueError(
            "No uploaded file was found for "
            f"FILE_INDEX: {sorted(missing_files)}"
        )

    unreferenced_files = (
        set(file_map.keys())
        - set(metadata_file_indexes)
    )

    if unreferenced_files:
        raise ValueError(
            "Uploaded files do not have metadata for "
            f"FILE_INDEX: {sorted(unreferenced_files)}"
        )

    saved: list[
        dict[str, Any]
    ] = []

    logical_slots: set[
        tuple[str, int | None]
    ] = set()

    for metadata in attachment_metadata:
        attachment_id = _normalize_id(
            metadata.ATTACHMENT_ID
        )

        parent_record_id = (
            _resolve_attachment_parent(
                cursor=cursor,
                prospect_id=prospect_id,
                metadata=metadata,
                oem_key_map=oem_key_map,
                certification_key_map=(
                    certification_key_map
                ),
                legal_document_key_map=(
                    legal_document_key_map
                ),
            )
        )

        logical_slot = (
            metadata.ATTACHMENT_FOR,
            parent_record_id,
        )

        if logical_slot in logical_slots:
            raise ValueError(
                "Only one attachment is allowed for "
                f"{metadata.ATTACHMENT_FOR} and "
                f"parent {parent_record_id}"
            )

        logical_slots.add(
            logical_slot
        )

        file_index = (
            metadata.FILE_INDEX
        )

        # Existing attachment without file replacement.
        if (
            attachment_id is not None
            and file_index is None
        ):
            _execute(
                cursor,
                f"""
                UPDATE {ATTACHMENT_TABLE}
                SET
                    AttachmentFor = ?,
                    ParentRecordId = ?,
                    VendAccount = ?,
                    ModifiedDateTime = GETDATE()
                WHERE Id = ?
                  AND ProspectId = ?
                """,
                (
                    metadata.ATTACHMENT_FOR,
                    parent_record_id,
                    _clean_text(
                        metadata.VEND_ACCOUNT
                    ),
                    attachment_id,
                    prospect_id,
                ),
            )

            saved.append(
                {
                    "ATTACHMENT_ID":
                        attachment_id,
                    "ATTACHMENT_FOR":
                        metadata.ATTACHMENT_FOR,
                    "PARENT_RECORD_ID":
                        parent_record_id,
                    "FILE_REPLACED":
                        False,
                }
            )

            continue

        if (
            attachment_id is None
            and file_index is None
        ):
            raise ValueError(
                "A new attachment requires FILE_INDEX"
            )

        file_data = file_map[
            int(file_index)
        ]

        (
            file_name,
            extension,
            content_type,
            file_bytes,
        ) = _validate_binary_file(
            file_data
        )

        # Replace existing file.
        if attachment_id is not None:
            _execute(
                cursor,
                f"""
                UPDATE {ATTACHMENT_TABLE}
                SET
                    AttachmentFor = ?,
                    ParentRecordId = ?,
                    VendAccount = ?,
                    FileName = ?,
                    FileExtension = ?,
                    ContentType = ?,
                    FileSizeBytes = ?,
                    FileContent = ?,
                    ModifiedDateTime = GETDATE()
                WHERE Id = ?
                  AND ProspectId = ?
                """,
                (
                    metadata.ATTACHMENT_FOR,
                    parent_record_id,
                    _clean_text(
                        metadata.VEND_ACCOUNT
                    ),
                    file_name,
                    extension.lstrip("."),
                    content_type,
                    len(file_bytes),
                    pyodbc.Binary(
                        file_bytes
                    ),
                    attachment_id,
                    prospect_id,
                ),
            )

            saved_id = attachment_id
            was_replaced = True

        # Insert new file.
        else:
            row = _execute(
                cursor,
                f"""
                INSERT INTO {ATTACHMENT_TABLE}
                (
                    ProspectSeq,
                    ProspectId,
                    AttachmentFor,
                    ParentRecordId,
                    DocumentId,
                    VendAccount,
                    FileName,
                    FileExtension,
                    ContentType,
                    FileSizeBytes,
                    FileContent,
                    ReceivedFromD365,
                    CreatedDateTime,
                    ModifiedDateTime
                )
                OUTPUT INSERTED.Id
                VALUES
                (
                    ?, ?, ?, ?, NEWID(), ?,
                    ?, ?, ?, ?, ?,
                    0, GETDATE(), NULL
                )
                """,
                (
                    prospect_seq,
                    prospect_id,
                    metadata.ATTACHMENT_FOR,
                    parent_record_id,
                    _clean_text(
                        metadata.VEND_ACCOUNT
                    ),
                    file_name,
                    extension.lstrip("."),
                    content_type,
                    len(file_bytes),
                    pyodbc.Binary(
                        file_bytes
                    ),
                ),
            ).fetchone()

            saved_id = int(
                row[0]
            )

            was_replaced = False

        saved.append(
            {
                "FILE_INDEX":
                    file_index,
                "ATTACHMENT_ID":
                    saved_id,
                "ATTACHMENT_FOR":
                    metadata.ATTACHMENT_FOR,
                "PARENT_RECORD_ID":
                    parent_record_id,
                "FILE_NAME":
                    file_name,
                "FILE_SIZE_BYTES":
                    len(file_bytes),
                "FILE_REPLACED":
                    was_replaced,
            }
        )

    return saved, deleted_ids


# ============================================================
# SUBMISSION VALIDATION
# ============================================================

def _validate_before_submit(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> None:

    _execute(
        cursor,
        f"""
        SELECT
            CompanyName,
            RepName,
            RepEmail,
            DeclarationAgreed,
            AuthorizedToSubmit,
            IsAuthorizedDistributor
        FROM {REGISTRATION_TABLE}
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    row = cursor.fetchone()

    if row is None:
        raise LookupError(
            f"PROSPECT_ID {prospect_id} was not found"
        )

    errors: list[str] = []

    if not _clean_text(
        row[0]
    ):
        errors.append(
            "Company name is required"
        )

    if not _clean_text(
        row[1]
    ):
        errors.append(
            "Representative name is required"
        )

    if not _clean_text(
        row[2]
    ):
        errors.append(
            "Representative email is required"
        )

    if not bool(
        row[3]
    ):
        errors.append(
            "Declaration must be accepted"
        )

    if not bool(
        row[4]
    ):
        errors.append(
            "Authorization to submit must be confirmed"
        )

    _execute(
        cursor,
        f"""
        SELECT COUNT(1)
        FROM {CONTACT_TABLE}
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    contact_count = int(
        _fetch_scalar(
            cursor
        )
        or 0
    )

    if contact_count == 0:
        errors.append(
            "At least one contact person is required"
        )

    _execute(
        cursor,
        f"""
        SELECT COUNT(1)
        FROM {QUESTIONNAIRE_TABLE}
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    questionnaire_count = int(
        _fetch_scalar(
            cursor
        )
        or 0
    )

    if questionnaire_count == 0:
        errors.append(
            "Quality questionnaire is required"
        )

    if bool(
        row[5]
    ):
        _execute(
            cursor,
            f"""
            SELECT COUNT(1)
            FROM {OEM_TABLE}
            WHERE ProspectId = ?
            """,
            (prospect_id,),
        )

        oem_count = int(
            _fetch_scalar(
                cursor
            )
            or 0
        )

        if oem_count == 0:
            errors.append(
                "At least one OEM is required for "
                "an authorized distributor"
            )

    if errors:
        raise SubmissionValidationError(
            errors
        )

def _fetch_questionnaire(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> QualityQuestionnaireInput:

    _execute(
        cursor,
        f"""
        SELECT TOP 1
            InstrumentsCalibrated,
            InstrumentsCalibratedRemark,

            AdequateEquipmentManpower,
            AdequateEquipmentManpowerRemark,

            DedicatedQualityFunction,
            DedicatedQualityFunctionRemark,

            SupplyCoC,
            SupplyCoCRemark,

            RetainCoCMinimum15Yrs,
            RetainCoCMinimum15YrsRemark,

            CorrectiveActionSystem,
            CorrectiveActionSystemRemark,

            PackingNoDamage,
            PackingNoDamageRemark,

            MeetShelfLife,
            MeetShelfLifeRemark,

            EthicalBehaviorAwareness,
            EthicalBehaviorAwarenessRemark,

            CounterfeitPartPrevention,
            CounterfeitPartPreventionRemark,

            NotifyOrganisationChanges,
            NotifyOrganisationChangesRemark
        FROM {QUESTIONNAIRE_TABLE}
        WHERE ProspectId = ?
        """,
        (
            prospect_id,
        ),
    )

    row = _fetch_one(cursor)

    if not row:
        return QualityQuestionnaireInput()

    return QualityQuestionnaireInput(
        INSTRUMENTSCALIBRATED=row.get("InstrumentsCalibrated"),
        INSTRUMENTSCALIBRATEDREMARK=row.get("InstrumentsCalibratedRemark"),

        ADEQUATEEQUIPMENT=row.get("AdequateEquipmentManpower"),
        ADEQUATEEQUIPMENTREMARK=row.get("AdequateEquipmentManpowerRemark"),

        DEDICATEDQUALITYFUNCTION=row.get("DedicatedQualityFunction"),
        DEDICATEDQUALITYFUNCTIONREMARK=row.get("DedicatedQualityFunctionRemark"),

        SUPPLYCOC=row.get("SupplyCoC"),
        SUPPLYCOCREMARK=row.get("SupplyCoCRemark"),

        RETAINCOC15YEARS=row.get("RetainCoCMinimum15Yrs"),
        RETAINCOC15YEARSREMARK=row.get("RetainCoCMinimum15YrsRemark"),

        CORRECTIVEACTIONSYSTEM=row.get("CorrectiveActionSystem"),
        CORRECTIVEACTIONSYSTEMREMARK=row.get("CorrectiveActionSystemRemark"),

        PACKINGENSURESNODAMAGE=row.get("PackingNoDamage"),
        PACKINGENSURESNODAMAGEREMARK=row.get("PackingNoDamageRemark"),

        MEETSHELFLIFEREQUIREMENTS=row.get("MeetShelfLife"),
        MEETSHELFLIFEREQUIREMENTSREMARK=row.get("MeetShelfLifeRemark"),

        EMPLOYEESAWAREETHICS=row.get("EthicalBehaviorAwareness"),
        EMPLOYEESAWAREETHICSREMARK=row.get("EthicalBehaviorAwarenessRemark"),

        PREVENTCOUNTERFEITPARTS=row.get("CounterfeitPartPrevention"),
        PREVENTCOUNTERFEITPARTSREMARK=row.get("CounterfeitPartPreventionRemark"),

        NOTIFYORGANIZATIONCHANGES=row.get("NotifyOrganisationChanges"),
        NOTIFYORGANIZATIONCHANGESREMARK=row.get("NotifyOrganisationChangesRemark"),
    )
def _fetch_latest_terms_version(
    cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any] | None:
    """
    Fetch the latest active Terms and Conditions metadata.

    The PDF binary is not included in the onboarding JSON response.
    The frontend must use TERMSVERSIONID with the Terms PDF POST API
    to fetch the exact raw PDF bytes.
    """

    _execute(
        cursor,
        f"""
        SELECT TOP (1)
            TermsVersionId,
            VersionNumber,
            FileName,
            ContentType,
            DATALENGTH(PdfFileBytes) AS FileSizeBytes,
            IsActive,
            CreatedOn
        FROM {TERMS_VERSION_TABLE}
        WHERE IsActive = 1
        ORDER BY TermsVersionId DESC
        """,
    )

    version = _fetch_one(cursor)

    if not version:
        return None

    terms_version_id = int(
        version["TermsVersionId"]
    )

    _execute(
        cursor,
        f"""
        SELECT TOP (1)
            ProspectTermsHistoryId,
            IsCurrent,
            AssignedOn,
            AcceptedOn
        FROM {PROSPECT_TERMS_HISTORY_TABLE}
        WHERE ProspectId = ?
          AND TermsVersionId = ?
        ORDER BY ProspectTermsHistoryId DESC
        """,
        (
            prospect_id,
            terms_version_id,
        ),
    )

    acceptance = _fetch_one(cursor)

    accepted_on = (
        acceptance.get("AcceptedOn")
        if acceptance
        else None
    )

    return {
        "TERMSVERSIONID": terms_version_id,
        "VERSIONNUMBER": version.get("VersionNumber"),
        "FILENAME": version.get("FileName"),
        "CONTENTTYPE": (
            version.get("ContentType")
            or "application/pdf"
        ),
        "FILESIZEBYTES": int(
            version.get("FileSizeBytes")
            or 0
        ),
        "ISACTIVE": bool(
            version.get("IsActive")
        ),
        "CREATEDON": _to_iso_string(
            version.get("CreatedOn")
        ),
        "ACCEPTED": accepted_on is not None,
        "ACCEPTEDON": _to_iso_string(
            accepted_on
        ),
    }

# ============================================================
# FETCH SERVICE
# ============================================================

def fetch_vendor_onboarding(
    payload: VendorFetchRequest,
) -> dict[str, Any]:
    prospect_id = _normalize_prospect_id(
        payload.PROSPECT_ID
    )

    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            prospect = _get_prospect(
                cursor=cursor,
                prospect_id=prospect_id,
                lock=False,
            )

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {REGISTRATION_TABLE}
                WHERE ProspectId = ?
                """,
                (prospect_id,),
            )
            registration = _fetch_one(cursor)

            if not registration:
                registration = {
                    "ProspectSeq": prospect["ProspectSeq"],
                    "ProspectId": prospect["ProspectId"],
                    "CompanyName": prospect.get("Name"),
                    "Status": "INVITED",
                    "IsDraft": 0,
                }

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {CONTACT_TABLE}
                WHERE ProspectId = ?
                ORDER BY IsPrimary DESC, ContactId
                """,
                (prospect_id,),
            )
            contacts = _fetch_all(cursor)

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {CUSTOMER_TABLE}
                WHERE ProspectId = ?
                ORDER BY CustomerId
                """,
                (prospect_id,),
            )
            customers = _fetch_all(cursor)

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {OEM_TABLE}
                WHERE ProspectId = ?
                ORDER BY OEMId
                """,
                (prospect_id,),
            )
            oems = _fetch_all(cursor)

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {CERTIFICATION_TABLE}
                WHERE ProspectId = ?
                ORDER BY CertificationId
                """,
                (prospect_id,),
            )
            certifications = _fetch_all(cursor)

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {LEGAL_DOCUMENT_TABLE}
                WHERE ProspectId = ?
                ORDER BY LegalDocumentId
                """,
                (prospect_id,),
            )
            legal_documents = _fetch_all(cursor)

            _execute(
                cursor,
                f"""
                SELECT *
                FROM {QUESTIONNAIRE_TABLE}
                WHERE ProspectId = ?
                """,
                (prospect_id,),
            )
            questionnaire = _fetch_one(cursor)

            # Newest rows are returned first. If bad historical data contains
            # duplicates for one logical attachment slot, the newest one wins.
            _execute(
                cursor,
                f"""
                SELECT
                    Id AS AttachmentId,
                    DocumentId,
                    ProspectSeq,
                    ProspectId,
                    AttachmentFor,
                    ParentRecordId,
                    VendAccount,
                    FileName,
                    FileExtension,
                    ContentType,
                    FileSizeBytes,
                    ReceivedFromD365,
                    ReceivedDateTime,
                    CreatedDateTime,
                    ModifiedDateTime
                FROM {ATTACHMENT_TABLE}
                WHERE ProspectId = ?
                ORDER BY CreatedDateTime DESC, Id DESC
                """,
                (prospect_id,),
            )
            attachments = _fetch_all(cursor)

            status_value = str(
                registration.get("Status") or "INVITED"
            ).strip().upper()

            action_value = (
                "SAVE"
                if status_value in {"INVITED", "DRAFT"}
                else "SUBMIT"
            )

            factory_license_attachment = _find_attachment(
                attachments=attachments,
                attachment_for="FactoryLicense",
                parent_record_id=None,
            )
            quality_questionnaire = _fetch_questionnaire(
                cursor=cursor,
                prospect_id=prospect_id,
            )

            oem_fetch_output: list[dict[str, Any]] = []
            for index, item in enumerate(oems):
                oem_id = item.get("OEMId")
                oem_attachment = _find_attachment(
                    attachments=attachments,
                    attachment_for="OEMCert",
                    parent_record_id=oem_id,
                )

                oem_fetch_output.append(
                    {
                        "OEMID": oem_id,
                        "CLIENTKEY": f"OEM_{index + 1}",
                        "OEMNAME": item.get("OEMName"),
                        "CERTIFICATE": None,
                        "CERTIFICATENAME": (
                            oem_attachment.get("FileName")
                            if oem_attachment
                            else None
                        ),
                        **_attachment_fetch_fields(oem_attachment),
                    }
                )

            certification_fetch_output: list[dict[str, Any]] = []
            for index, item in enumerate(certifications):
                certification_id = item.get("CertificationId")
                certification_attachment = _find_attachment(
                    attachments=attachments,
                    attachment_for="Certification",
                    parent_record_id=certification_id,
                )

                certification_fetch_output.append(
                    {
                        "CERTIFICATIONID": certification_id,
                        "CLIENTKEY": f"CERT_{index + 1}",
                        "CERTIFICATIONTYPE": item.get("CertificationType"),
                        "STATUS": item.get("CertStatus"),
                        "CERTIFICATIONNUMBER": item.get("CertificateNumber"),
                        "VALIDUNTIL": _to_iso_string(item.get("ValidUntil")),
                        "ATTACHMENT": None,
                        "ATTACHMENTNAME": (
                            certification_attachment.get("FileName")
                            if certification_attachment
                            else None
                        ),
                        **_attachment_fetch_fields(certification_attachment),
                    }
                )

            legal_document_fetch_output: list[dict[str, Any]] = []
            for index, item in enumerate(legal_documents):
                legal_document_id = item.get(
                    "LegalDocumentId"
                )
                legal_attachment = _find_attachment(
                    attachments=attachments,
                    attachment_for="LegalDocument",
                    parent_record_id=legal_document_id,
                )

                legal_document_fetch_output.append(
                    {
                        "LEGALDOCUMENTID": legal_document_id,
                        "CLIENTKEY": f"LEGAL_{index + 1}",
                        "DOCUMENTNAME": item.get("DocumentName"),
                        "LEGALFILE": None,
                        "LEGALFILENAME": (
                            legal_attachment.get("FileName")
                            if legal_attachment
                            else None
                        ),
                        **_attachment_fetch_fields(
                            legal_attachment
                        ),
                    }
                )
            latest_terms = _fetch_latest_terms_version(
                cursor=cursor,
                prospect_id=prospect_id,
            )
            accepted_terms_history = _fetch_prospect_terms_history(
                cursor=cursor,
                prospect_id=prospect_id,
            )

            return {
                "SUCCESS": True,
                "MESSAGE": "Vendor onboarding information fetched successfully",
                "ACTION": action_value,
                "STATUS": status_value,
                "ISDRAFT": int(registration.get("IsDraft") or 0),
                "PROSPECT_ID": prospect_id,
                "VENDOR_ACCOUNT": _clean_text(
                    prospect.get("VendorAccount")
                ),
                "DISTRIBUTORAUTHORIZATION": (
                    bool(registration.get("IsAuthorizedDistributor"))
                    if registration.get("IsAuthorizedDistributor") is not None
                    else None
                ),
                "GENERALINFORMATION": {
                    "COMPANYNAME": registration.get("CompanyName"),
                    "COMPANYREGISTERNUMBER": registration.get("CompanyRegNumber"),
                    "NATUREOFCOMPANY": registration.get("NatureOfCompany"),
                    "NATUREOFBUSINESS": registration.get("NatureOfBusiness"),
                    "SCOPEOFSUPPLY": registration.get("ScopeOfSupply"),
                    "YEAROFESTABLISHMENT": (
                        str(registration.get("YearOfEstablishment"))
                        if registration.get("YearOfEstablishment") is not None
                        else None
                    ),
                    "NUMBEROFEMPLOYEES": (
                        str(registration.get("NumberOfEmployees"))
                        if registration.get("NumberOfEmployees") is not None
                        else None
                    ),
                    "COUNTRYOFORIGIN": registration.get("CountryOfOrigin"),
                    "STREET": registration.get("Street"),
                    "CITY": registration.get("City"),
                    "DISTRICT": registration.get("District"),
                    "STATE": registration.get("State"),
                    "COUNTRY": registration.get("Country"),
                    "POSTALCODE": registration.get("ZipCode"),
                    "CURRENTSUPPLYLOCATION": _json_list_from_db(
                        registration.get("Location")
                    ),
                    # "CURRENTSUPPLYLOCATION": registration.get("Location"),
                    "WORKINGTIMEZONE": registration.get("WorkingTimeZone"),
                    "WEEKLYHOLIDAY": registration.get("WeeklyHoliday"),
                },
                "FINANCIALCOMMERCIAL": {
                    "SUPPLIERTYPE": registration.get("SupplierType"),
                    "CURRENCY": registration.get("Currency"),
                    "PAYMENTTERMS": registration.get("PaymentTerms"),
                    "DELIVERYTERMS": registration.get("DeliveryTerms"),
                    "BANKNAME": registration.get("BankName"),
                    "BANKADDRESS": registration.get("BankAddress"),
                    "BANKACCOUNTNUMBER": registration.get("BankAccountNumber"),
                    "IFSCCODE": registration.get("IFSCCode"),
                    "IFCCODE": registration.get("IFCCode"),
                    "BENEFICIARYNAME": registration.get("BeneficiaryName"),
                    "BENEFICIARYADDRESS": registration.get("BeneficiaryAddress"),
                    "SWIFTCODE": registration.get("SwiftCode"),
                    "IBAN": registration.get("IBAN"),
                    "BANKBRANCHCODE": registration.get("BankBranchCode"),
                    "BANKCONTACTNUMBER": registration.get("BankContactNumber"),
                    "PANNUMBER": registration.get("PANNumber"),
                    "REGISTRATIONTYPE": registration.get("RegistrationType"),
                    "REGISTRATIONNUMBER": registration.get("RegistrationNumber"),
                    "FACTORYLICENCENUMBER": registration.get("FactoryLicenseNumber"),
                    "FACTORYLICENSE": None,
                    "FACTORYLICENSEFILENAME": (
                        factory_license_attachment.get("FileName")
                        if factory_license_attachment
                        else None
                    ),
                    **_attachment_fetch_fields(factory_license_attachment),
                },
                "contacts": [
                    {
                        "CONTACTID": item.get("ContactId"),
                        "CONTACTPERSONNAME": item.get("ContactName"),
                        "DESIGNATION": item.get("Designation"),
                        "EMAIL": item.get("EmailAddress"),
                        "MOBILENUMBER": item.get("MobileNumber"),
                        "LANDLINE": item.get("LandlineNumber"),
                        "ISPRIMARY": bool(item.get("IsPrimary")),
                    }
                    for item in contacts
                ],
                "businessReference": [
                    {
                        "CUSTOMERID": item.get("CustomerId"),
                        "CUTOMERNAME": item.get("CustomerName"),
                        "INDUSTRY": item.get("Industry"),
                        "COUNTRY": item.get("Country"),
                    }
                    for item in customers
                ],
                "oemdetails": oem_fetch_output,
                "certification": certification_fetch_output,
                "legaldocument": legal_document_fetch_output,
                "qualityQuestionnaire": quality_questionnaire,
                
                # "qualityQuestionnaire": {
                #     "INSTRUMENTSCALIBRATED": questionnaire.get(
                #         "InstrumentsCalibrated"
                #     ),
                #     "ADEQUATEEQUIPMENT": questionnaire.get(
                #         "AdequateEquipmentManpower"
                #     ),
                #     "DEDICATEDQUALITYFUNCTION": questionnaire.get(
                #         "DedicatedQualityFunction"
                #     ),
                #     "SUPPLYCOC": questionnaire.get("SupplyCoC"),
                #     "RETAINCOC15YEARS": questionnaire.get(
                #         "RetainCoCMinimum15Yrs"
                #     ),
                #     "CORRECTIVEACTIONSYSTEM": questionnaire.get(
                #         "CorrectiveActionSystem"
                #     ),
                #     "PACKINGENSURESNODAMAGE": questionnaire.get(
                #         "PackingNoDamage"
                #     ),
                #     "MEETSHELFLIFEREQUIREMENTS": questionnaire.get(
                #         "MeetShelfLife"
                #     ),
                #     "EMPLOYEESAWAREETHICS": questionnaire.get(
                #         "EthicalBehaviorAwareness"
                #     ),
                #     "PREVENTCOUNTERFEITPARTS": questionnaire.get(
                #         "CounterfeitPartPrevention"
                #     ),
                #     "NOTIFYORGANIZATIONCHANGES": questionnaire.get(
                #         "NotifyOrganisationChanges"
                #     ),
                # },
                # "declaration": {
                #     "TITLE": registration.get("RepTitle"),
                #     "NAME": registration.get("RepName"),
                #     "DESIGNATION": registration.get("RepDesignation"),
                #     "EMAIL": registration.get("RepEmail"),
                #     "MOBILENUMBER": registration.get("RepMobileNumber"),
                #     "AGREEDTODECLARATION": bool(
                #         registration.get("DeclarationAgreed")
                #     ),
                #     "DATESUBMISSION": _to_iso_string(
                #         registration.get("DateOfSubmission")
                #     ),
                #     "AUTHORIZEDCONFIRM": bool(
                #         registration.get("AuthorizedToSubmit")
                #     ),
                #     "ACCEPTTERMS": (
                #         bool(
                #             registration.get("AcceptTerms")
                #         )
                #         if registration.get("AcceptTerms") is not None
                #         else None
                #     ),
                # },
                "declaration": {
                    "TITLE": registration.get("RepTitle"),
                    "NAME": registration.get("RepName"),
                    "DESIGNATION": registration.get(
                        "RepDesignation"
                    ),
                    "EMAIL": registration.get("RepEmail"),
                    "MOBILENUMBER": registration.get(
                        "RepMobileNumber"
                    ),
                    "AGREEDTODECLARATION": bool(
                        registration.get("DeclarationAgreed")
                    ),
                    "DATESUBMISSION": _to_iso_string(
                        registration.get("DateOfSubmission")
                    ),
                    "AUTHORIZEDCONFIRM": bool(
                        registration.get("AuthorizedToSubmit")
                    ),

                    # Do not use the old general AcceptTerms value.
                    # Calculate acceptance against the latest version.
                    # "ACCEPTTERMS": (
                    #     latest_terms["ACCEPTED"]
                    #     if latest_terms
                    #     else False
                    # ),
                    "ACCEPTTERMS": False,
                    "LATESTVERSION": latest_terms,
                },
                "ATTACHMENTS": attachments,
                "ACCEPTEDTERMSHISTORY": accepted_terms_history,
            }

        finally:
            cursor.close()


def _save_factory_license_base64(
    cursor: pyodbc.Cursor,
    prospect_seq: int,
    prospect_id: str,
    payload: VendorSaveRequest,
) -> None:
    """Save the factory license using the filename sent by the frontend."""

    financial = payload.financialCommercial
    base64_value = _clean_text(
        financial.FACTORYLICENSE
    )

    if not base64_value:
        return

    (
        file_name,
        file_extension,
        content_type,
        file_bytes,
    ) = _prepare_frontend_base64_file(
        base64_value=base64_value,
        frontend_file_name=_clean_text(
            financial.FACTORYLICENSEFILENAME
        ),
        content_field_name="FACTORYLICENSE",
        file_name_field_name="FACTORYLICENSEFILENAME",
    )

    _execute(
        cursor,
        f"""
        DELETE FROM {ATTACHMENT_TABLE}
        WHERE ProspectId = ?
          AND AttachmentFor = 'FactoryLicense'
        """,
        (prospect_id,),
    )

    _execute(
        cursor,
        f"""
        INSERT INTO {ATTACHMENT_TABLE}
        (
            ProspectSeq,
            ProspectId,
            AttachmentFor,
            ParentRecordId,
            DocumentId,
            FileName,
            FileExtension,
            ContentType,
            FileSizeBytes,
            FileContent,
            ReceivedFromD365,
            CreatedDateTime,
            ModifiedDateTime
        )
        VALUES
        (
            ?, ?,
            'FactoryLicense',
            NULL,
            NEWID(),
            ?, ?, ?,
            ?,
            ?,
            0,
            GETDATE(),
            GETDATE()
        )
        """,
        (
            prospect_seq,
            prospect_id,
            file_name,
            file_extension,
            content_type,
            len(file_bytes),
            pyodbc.Binary(file_bytes),
        ),
    )


# ============================================================
# SAVE SERVICE
# ============================================================
def save_vendor_onboarding(
    form_data_json: str,
    attachment_metadata_json: str | None = None,
    uploaded_files: list[dict[str, Any]] | None = None,
    sync_attachments: bool = False,
) -> dict[str, Any]:
    """
    Save the complete vendor onboarding form only in the secondary DB.

    Status flow:
        Invitation created                  -> INVITED
        INVITED + SAVE / NEXT              -> DRAFT
        DRAFT + SAVE / NEXT                -> DRAFT
        RESUBMITTED + SAVE / NEXT          -> RESUBMITTED
        INVITED / DRAFT + SUBMIT           -> TO_EVALUATE
        RESUBMITTED + SUBMIT               -> TO_EVALUATE

    Editable statuses:
        INVITED
        DRAFT
        RESUBMITTED
    """

    payload, attachment_metadata = _parse_save_request(
        form_data_json=form_data_json,
        attachment_metadata_json=attachment_metadata_json,
    )

    prospect_id = _normalize_prospect_id(
        payload.PROSPECT_ID
    )

    action = _action_value(
        payload.ACTION
    )

    allowed_actions = {
        "SAVE",
        "NEXT",
        "SUBMIT",
    }

    if action not in allowed_actions:
        raise ValueError(
            "ACTION must be SAVE, NEXT or SUBMIT"
        )

    expected_is_draft = (
        1
        if action == "SUBMIT"
        else 0
    )

    if payload.ISDRAFT != expected_is_draft:
        if action == "SUBMIT":
            raise ValueError(
                "ISDRAFT must be 1 when ACTION is SUBMIT"
            )

        raise ValueError(
            "ISDRAFT must be 0 when ACTION is SAVE or NEXT"
        )

    uploaded_files = (
        uploaded_files
        or []
    )

    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            # Confirm the prospect exists and lock it during the save.
            _get_prospect(
                cursor=cursor,
                prospect_id=prospect_id,
                lock=True,
            )

            # Fetch and lock the existing registration.
            registration = _get_registration_for_update(
                cursor=cursor,
                prospect_id=prospect_id,
            )

            current_status = str(
                registration.get("Status")
                or "INVITED"
            ).strip().upper()

            editable_statuses = {
                "INVITED",
                "DRAFT",
                "RESUBMITTED",
            }

            if current_status not in editable_statuses:
                raise ValueError(
                    "Vendor onboarding form cannot be edited while "
                    f"status is {current_status}"
                )

            # Determine the resulting status only after reading
            # the current registration status.
            if action == "SUBMIT":
                registration_status = "TO_EVALUATE"

            elif current_status == "RESUBMITTED":
                # Keep the form in RESUBMITTED state while the
                # vendor is saving changes or moving between pages.
                registration_status = "RESUBMITTED"

            else:
                registration_status = "DRAFT"

            prospect_seq = int(
                registration["ProspectSeq"]
            )

            _update_main_registration(
                cursor=cursor,
                payload=payload,
                registration=registration,
                prospect_id=prospect_id,
                registration_status=registration_status,
                is_draft=expected_is_draft,
            )

            contacts, deleted_contacts = _sync_contacts(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            (
                business_references,
                deleted_business_references,
            ) = _sync_business_references(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            (
                oems,
                oem_key_map,
                deleted_oems,
            ) = _sync_oems(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            (
                certifications,
                certification_key_map,
                deleted_certifications,
            ) = _sync_certifications(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            (
                legal_documents,
                legal_document_key_map,
                deleted_legal_documents,
            ) = _sync_legal_documents(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            _save_questionnaire(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            _save_factory_license_base64(
                cursor=cursor,
                prospect_seq=prospect_seq,
                prospect_id=prospect_id,
                payload=payload,
            )

            if sync_attachments:
                (
                    attachments,
                    deleted_attachments,
                ) = _sync_attachments(
                    cursor=cursor,
                    prospect_seq=prospect_seq,
                    prospect_id=prospect_id,
                    attachment_metadata=attachment_metadata,
                    uploaded_files=uploaded_files,
                    oem_key_map=oem_key_map,
                    certification_key_map=certification_key_map,
                    legal_document_key_map=legal_document_key_map,
                )
            else:
                attachments = []
                deleted_attachments = []

            vendor_account = _populate_attachment_vendor_account(
                cursor=cursor,
                prospect_id=prospect_id,
            )
            terms_acceptance = None

            if action == "SUBMIT":
                _validate_before_submit(
                    cursor=cursor,
                    prospect_id=prospect_id,
                )

                terms_acceptance = (
                    _save_prospect_terms_acceptance(
                        cursor=cursor,
                        prospect_id=prospect_id,
                        payload=payload,
                    )
                )

            # Validate the complete saved data before committing
            # a final submission or resubmission.
            # if action == "SUBMIT":
            #     _validate_before_submit(
            #         cursor=cursor,
            #         prospect_id=prospect_id,
            #     )

            connection.commit()

            if action == "SUBMIT":
                if current_status == "RESUBMITTED":
                    response_message = (
                        "Vendor onboarding information "
                        "resubmitted successfully"
                    )
                else:
                    response_message = (
                        "Vendor onboarding information "
                        "submitted successfully"
                    )
            else:
                response_message = (
                    "Vendor onboarding information "
                    "saved successfully"
                )

            return {
                "SUCCESS": True,
                "MESSAGE": response_message,
                "ACTION": action,
                "ISDRAFT": expected_is_draft,
                "PROSPECT_ID": prospect_id,
                "PROSPECT_SEQ": prospect_seq,
                "PREVIOUS_STATUS": current_status,
                "STATUS": registration_status,
                "VENDOR_ACCOUNT": vendor_account,
                "DATABASE": "SECONDARY",
                "ATTACHMENTS_SYNCED": sync_attachments,
                "SAVED_IDS": {
                    "contacts": contacts,
                    "businessReference": business_references,
                    "oemdetails": oems,
                    "certification": certifications,
                    "legaldocument": legal_documents,
                    "ATTACHMENTS": attachments,
                },
                "AUTO_DELETED_IDS": {
                    "contacts": deleted_contacts,
                    "businessReference": deleted_business_references,
                    "oemdetails": deleted_oems,
                    "certification": deleted_certifications,
                    "legaldocument": deleted_legal_documents,
                    "ATTACHMENTS": deleted_attachments,
                },
                "TERMS_ACCEPTANCE": terms_acceptance,
            }

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()









