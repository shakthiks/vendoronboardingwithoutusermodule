from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from app.core.config import settings
from app.core.d365_auth import get_d365_token
from app.db.base import get_connection, get_secondary_connection


def _required_schema(*setting_names: str) -> str:
    """Return the first configured SQL schema name."""

    for setting_name in setting_names:
        value = getattr(settings, setting_name, None)

        if value is None:
            continue

        normalized = str(value).strip().strip("[]")

        if normalized:
            return normalized

    raise RuntimeError(
        "Database schema configuration is missing. Checked: "
        + ", ".join(setting_names)
    )


CLOUD_DB_SCHEMA = _required_schema(
    "DB_SCHEMA",
)

LOCAL_DB_SCHEMA = _required_schema(
    "SECONDARY_DB_SCHEMA",
    "DB_SCHEMA",
)

REFERENCE_DB_SCHEMA = _required_schema(
    "VENDOR_DB_SCHEMA",
    "DB_SCHEMA",
    "SECONDARY_DB_SCHEMA",
)

# Cloud / primary vendor-onboarding tables.
REGISTRATION_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[HIQ_VendorRegistration]"
)
PROSPECT_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[d365_VendorProspect]"
)
CONTACT_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[HIQ_VendorContact]"
)
ATTACHMENT_TABLE = (
    f"[{CLOUD_DB_SCHEMA}].[HIQ_VendorFileAttachment]"
)

# Local / secondary approval and risk tables.
RISK_HEADER_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
)
APPROVAL_BATCH_TABLE = (
    f"[{LOCAL_DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
)

# D365 address-reference tables are read through get_connection().
COUNTRY_REFERENCE_TABLE = (
    f"[{REFERENCE_DB_SCHEMA}].[d365_LogisticsAddressCountryRegion]"
)
STATE_REFERENCE_TABLE = (
    f"[{REFERENCE_DB_SCHEMA}].[d365_LogisticsAddressState]"
)
CITY_REFERENCE_TABLE = (
    f"[{REFERENCE_DB_SCHEMA}].[d365_LogisticsAddressCity]"
)
DISTRICT_REFERENCE_TABLE = (
    f"[{REFERENCE_DB_SCHEMA}].[d365_LogisticsAddressDistrict]"
)
ZIPCODE_REFERENCE_TABLE = (
    f"[{REFERENCE_DB_SCHEMA}].[d365_LogisticsAddressZipCode]"
)

D365_RESOURCE = os.getenv(
    "D365_RESOURCE",
    "",
).strip().rstrip("/")
# D365_VENDOR_CREATION_URL=settings.D365_VENDOR_CREATION_URL
D365_VENDOR_CREATION_URL = str(
    settings.D365_VENDOR_CREATION_URL
).strip().rstrip("/")

if not D365_VENDOR_CREATION_URL:
    raise RuntimeError(
        "D365_VENDOR_CREATION_URL is empty"
    )

D365_DEFAULT_CURRENCY = os.getenv(
    "D365_DEFAULT_CURRENCY",
    "INR",
).strip()
D365_DEFAULT_LANGUAGE = os.getenv(
    "D365_DEFAULT_LANGUAGE",
    "en-us",
).strip()
# D365_DEFAULT_TAX_GROUP = os.getenv(
#     "D365_DEFAULT_TAX_GROUP",
#     "GST",
# ).strip()
D365_DEFAULT_PAYMENT_TERMS = os.getenv(
    "D365_DEFAULT_PAYMENT_TERMS",
    "",
).strip()
D365_DEFAULT_PAYMENT_MODE = os.getenv(
    "D365_DEFAULT_PAYMENT_MODE",
    "",
).strip()

D365_REQUEST_TIMEOUT_SECONDS = int(
    os.getenv(
        "D365_REQUEST_TIMEOUT_SECONDS",
        "250",
    )
)
D365_VERIFY_SSL = os.getenv(
    "D365_VERIFY_SSL",
    "false",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}


class D365VendorCreationError(RuntimeError):
    pass


def _row_to_dict(
    cursor,
    row,
) -> dict[str, Any]:
    if row is None or cursor.description is None:
        return {}

    columns = [
        column[0]
        for column in cursor.description
    ]

    return dict(zip(columns, row))


def _rows_to_dicts(
    cursor,
    rows,
) -> list[dict[str, Any]]:
    if cursor.description is None:
        return []

    columns = [
        column[0]
        for column in cursor.description
    ]

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def _value(
    row: dict[str, Any],
    *column_names: str,
    default: Any = None,
) -> Any:
    """Case-insensitive lookup supporting old and new column names."""

    normalized = {
        str(key).lower(): value
        for key, value in row.items()
    }

    for column_name in column_names:
        key = column_name.lower()

        if key in normalized:
            return normalized[key]

    return default


def _text(
    value: Any,
    default: str = "",
) -> str:
    if value is None:
        return default

    normalized = str(value).strip()
    return normalized or default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    return _text(value).lower() in {
        "1",
        "true",
        "yes",
        "y",
    }


def _quote_identifier(identifier: str) -> str:
    """Safely quote a SQL Server identifier read from metadata."""

    return "[" + identifier.replace("]", "]]" ) + "]"


def _reference_table_columns(
    cursor,
    table_name: str,
) -> dict[str, str]:
    """Return actual reference-table columns keyed by uppercase name."""

    object_name = table_name.replace("[", "").replace("]", "")

    cursor.execute(
        """
        SELECT column_info.name
        FROM sys.columns AS column_info
        WHERE column_info.object_id = OBJECT_ID(?)
        ORDER BY column_info.column_id
        """,
        object_name,
    )

    columns = {
        str(row[0]).upper(): str(row[0])
        for row in cursor.fetchall()
    }

    if not columns:
        raise D365VendorCreationError(
            "Reference table was not found or has no columns: "
            f"{object_name}"
        )

    return columns


def _normalized_sql_expression(column_name: str) -> str:
    quoted_column = _quote_identifier(column_name)

    return (
        "UPPER(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), "
        f"{quoted_column}))))"
    )


def _resolved_output_expression(output_columns: list[str]) -> str:
    expressions = [
        (
            "NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), "
            f"{_quote_identifier(column_name)}))), N'')"
        )
        for column_name in output_columns
    ]

    if len(expressions) == 1:
        return expressions[0]

    return "COALESCE(" + ", ".join(expressions) + ")"


def _query_reference_candidates(
    cursor,
    *,
    table_name: str,
    requested_value: str,
    output_columns: list[str],
    match_columns: list[str],
    columns: dict[str, str],
    filters: dict[str, Any] | None,
) -> list[str]:
    """Return distinct matching D365 codes/keys for one lookup."""

    match_parts: list[str] = []
    parameters: list[Any] = []

    for column_name in match_columns:
        match_parts.append(
            f"{_normalized_sql_expression(column_name)} = UPPER(?)"
        )
        parameters.append(requested_value)

    where_parts = ["(" + " OR ".join(match_parts) + ")"]

    for filter_candidate, filter_value in (filters or {}).items():
        normalized_filter = _text(filter_value)

        if not normalized_filter:
            continue

        actual_filter_column = columns.get(filter_candidate.upper())

        # Different D365 mirror tables can have different parent columns.
        if actual_filter_column is None:
            continue

        where_parts.append(
            f"{_normalized_sql_expression(actual_filter_column)} = UPPER(?)"
        )
        parameters.append(normalized_filter)

    output_expression = _resolved_output_expression(output_columns)

    cursor.execute(
        f"""
        SELECT DISTINCT
            {output_expression} AS ResolvedCode
        FROM {table_name}
        WHERE {" AND ".join(where_parts)}
          AND {output_expression} IS NOT NULL
        """,
        *parameters,
    )

    candidates: list[str] = []

    for row in cursor.fetchall():
        candidate = _text(row[0])

        if candidate and candidate not in candidates:
            candidates.append(candidate)

    return candidates


def _lookup_reference_code(
    cursor,
    *,
    table_name: str,
    lookup_value: Any,
    output_candidates: tuple[str, ...],
    match_candidates: tuple[str, ...],
    field_name: str,
    filters: dict[str, Any] | None = None,
    allow_unique_global_fallback: bool = False,
) -> str:
    """
    Resolve a full name or existing code from a D365 reference table.

    First lookup uses the supplied parent filters. When requested, a
    second unfiltered lookup is allowed only when it produces exactly
    one distinct code. This supports generic reference rows such as an
    'Others' state without hardcoding either the label or its code.
    """

    requested_value = _text(lookup_value)

    if not requested_value:
        return ""

    columns = _reference_table_columns(cursor, table_name)

    output_columns = [
        columns[candidate.upper()]
        for candidate in output_candidates
        if candidate.upper() in columns
    ]

    if not output_columns:
        raise D365VendorCreationError(
            f"{field_name}: no supported output column exists in "
            f"{table_name}. Checked: {', '.join(output_candidates)}"
        )

    match_columns = [
        columns[candidate.upper()]
        for candidate in match_candidates
        if candidate.upper() in columns
    ]

    if not match_columns:
        raise D365VendorCreationError(
            f"{field_name}: no supported lookup column exists in "
            f"{table_name}. Checked: {', '.join(match_candidates)}"
        )

    filtered_candidates = _query_reference_candidates(
        cursor,
        table_name=table_name,
        requested_value=requested_value,
        output_columns=output_columns,
        match_columns=match_columns,
        columns=columns,
        filters=filters,
    )

    if len(filtered_candidates) == 1:
        return filtered_candidates[0]

    if len(filtered_candidates) > 1:
        raise D365VendorCreationError(
            f"{field_name} value '{requested_value}' is ambiguous in "
            f"{table_name} for filters {filters}: "
            f"{filtered_candidates}"
        )

    if allow_unique_global_fallback and filters:
        global_candidates = _query_reference_candidates(
            cursor,
            table_name=table_name,
            requested_value=requested_value,
            output_columns=output_columns,
            match_columns=match_columns,
            columns=columns,
            filters=None,
        )

        if len(global_candidates) == 1:
            logger.info(
                "[D365 ADDRESS LOOKUP FALLBACK] Field={} Value={!r} "
                "not found with filters={!r}; using unique global "
                "code={!r}",
                field_name,
                requested_value,
                filters,
                global_candidates[0],
            )
            return global_candidates[0]

        if len(global_candidates) > 1:
            raise D365VendorCreationError(
                f"{field_name} value '{requested_value}' was not found "
                f"with filters {filters}, and the global lookup is "
                f"ambiguous: {global_candidates}"
            )

    filter_description = ", ".join(
        f"{key}={_text(value)}"
        for key, value in (filters or {}).items()
        if _text(value)
    )

    suffix = f" with {filter_description}" if filter_description else ""

    raise D365VendorCreationError(
        f"{field_name} value '{requested_value}' was not found in "
        f"{table_name}{suffix}"
    )


def _resolve_d365_address_codes(
    registration: dict[str, Any],
) -> dict[str, str]:
    """Resolve registration address names to D365 codes/keys."""

    country_name = _text(
        _value(registration, "Country", "CountryOfOrigin")
    )

    if not country_name:
        raise D365VendorCreationError(
            "Country is required before D365 vendor creation"
        )

    state_name = _text(_value(registration, "State"))
    city_name = _text(_value(registration, "City"))
    district_name = _text(_value(registration, "District"))
    zip_value = _text(
        _value(registration, "ZipCode", "PostalCode")
    )

    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            country_code = _lookup_reference_code(
                cursor,
                table_name=COUNTRY_REFERENCE_TABLE,
                lookup_value=country_name,
                output_candidates=("COUNTRYREGIONID", "ISOCODE"),
                match_candidates=(
                    "Country",
                    "NAME",
                    "DESCRIPTION",
                    "COUNTRYREGIONID",
                    "ISOCODE",
                ),
                field_name="Country",
            )

            state_code = ""

            if state_name:
                state_code = _lookup_reference_code(
                    cursor,
                    table_name=STATE_REFERENCE_TABLE,
                    lookup_value=state_name,
                    output_candidates=("STATEID", "STATECODE_IN"),
                    match_candidates=(
                        "NAME",
                        "DESCRIPTION",
                        "STATEID",
                        "STATECODE_IN",
                    ),
                    filters={"COUNTRYREGIONID": country_code},
                    field_name="State",
                    allow_unique_global_fallback=True,
                )

            city_code = ""

            if city_name:
                city_code = _lookup_reference_code(
                    cursor,
                    table_name=CITY_REFERENCE_TABLE,
                    lookup_value=city_name,
                    output_candidates=("CITYKEY", "CITY", "NAME"),
                    match_candidates=(
                        "NAME",
                        "DESCRIPTION",
                        "CITY",
                        "CITYKEY",
                    ),
                    filters={
                        "COUNTRYREGIONID": country_code,
                        "STATEID": state_code,
                    },
                    field_name="City",
                    allow_unique_global_fallback=True,
                )

            district_code = ""

            if district_name:
                district_code = _lookup_reference_code(
                    cursor,
                    table_name=DISTRICT_REFERENCE_TABLE,
                    lookup_value=district_name,
                    output_candidates=(
                        "DISTRICTKEY",
                        "DISTRICTID",
                        "DISTRICT",
                        "NAME",
                    ),
                    match_candidates=(
                        "NAME",
                        "DESCRIPTION",
                        "DISTRICT",
                        "DISTRICTKEY",
                        "DISTRICTID",
                    ),
                    filters={
                        "COUNTRYREGIONID": country_code,
                        "STATEID": state_code,
                        "CITYKEY": city_code,
                        "CITY": city_name,
                    },
                    field_name="District",
                    allow_unique_global_fallback=True,
                )

            zip_code = ""

            if zip_value:
                zip_code = _lookup_reference_code(
                    cursor,
                    table_name=ZIPCODE_REFERENCE_TABLE,
                    lookup_value=zip_value,
                    output_candidates=(
                        "ZIPCODE",
                        "ZIPCODEKEY",
                        "POSTALCODE",
                        "NAME",
                    ),
                    match_candidates=(
                        "ZIPCODE",
                        "ZIPCODEKEY",
                        "POSTALCODE",
                        "NAME",
                        "DESCRIPTION",
                    ),
                    filters={
                        "COUNTRYREGIONID": country_code,
                        "STATEID": state_code,
                        "CITYKEY": city_code,
                        "CITY": city_name,
                    },
                    field_name="ZIP code",
                    allow_unique_global_fallback=True,
                )

            result = {
                "COUNTRY": country_code,
                "STATE": state_code,
                "CITY": city_code,
                "DISTRICT": district_code,
                "ZIPCODE": zip_code,
            }

            logger.info(
                "[D365 ADDRESS LOOKUP] Country={!r}->{!r} "
                "State={!r}->{!r} City={!r}->{!r} "
                "District={!r}->{!r} Zip={!r}->{!r}",
                country_name,
                country_code,
                state_name,
                state_code,
                city_name,
                city_code,
                district_name,
                district_code,
                zip_value,
                zip_code,
            )

            return result

        finally:
            cursor.close()


def _file_extension(
    file_name: str,
    content_type: str,
    explicit_extension: str,
) -> str:
    if explicit_extension:
        return explicit_extension.lstrip(".").lower()

    suffix = Path(file_name).suffix.lstrip(".").lower()

    if suffix:
        return suffix

    guessed = mimetypes.guess_extension(
        content_type or ""
    )

    if guessed:
        return guessed.lstrip(".").lower()

    return "bin"


def _base64_content(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, memoryview):
        value = value.tobytes()

    if isinstance(value, bytearray):
        value = bytes(value)

    if isinstance(value, bytes):
        return base64.b64encode(value).decode(
            "ascii"
        )

    if isinstance(value, str):
        normalized = value.strip()

        if normalized.startswith("data:") and "," in normalized:
            return normalized.split(",", 1)[1]

        return normalized

    raise D365VendorCreationError(
        "Unsupported attachment content type: "
        f"{type(value).__name__}"
    )


def _parse_d365_response(
    response: requests.Response,
) -> dict[str, Any]:
    try:
        data: Any = response.json()
    except ValueError:
        raw = response.text.strip()

        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = {
                "Success": False,
                "ErrorMessage": raw,
            }

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {
                "Success": False,
                "ErrorMessage": data,
            }

    if isinstance(data, dict):
        for wrapper_name in (
            "d",
            "value",
            "response",
        ):
            wrapped = data.get(wrapper_name)

            if isinstance(wrapped, str):
                try:
                    wrapped = json.loads(wrapped)
                except ValueError:
                    pass

            if isinstance(wrapped, dict):
                data = wrapped
                break

    if not isinstance(data, dict):
        return {
            "Success": False,
            "ErrorMessage": str(data),
        }

    return data


def _fetch_vendor_source_data(
    prospect_id: str,
) -> dict[str, Any]:
    with get_connection() as cloud_connection:
        cloud_cursor = cloud_connection.cursor()

        with get_secondary_connection() as local_connection:
            local_cursor = local_connection.cursor()

            try:
                cloud_cursor.execute(
                    f"""
                    SELECT TOP 1 *
                    FROM {REGISTRATION_TABLE}
                    WHERE ProspectId = ?
                    """,
                    prospect_id,
                )

                registration = _row_to_dict(
                    cloud_cursor,
                    cloud_cursor.fetchone(),
                )

                if not registration:
                    raise D365VendorCreationError(
                        "Vendor registration was not found for "
                        f"ProspectId={prospect_id}"
                    )

                cloud_cursor.execute(
                    f"""
                    SELECT TOP 1 *
                    FROM {PROSPECT_TABLE}
                    WHERE ProspectId = ?
                    """,
                    prospect_id,
                )

                prospect = _row_to_dict(
                    cloud_cursor,
                    cloud_cursor.fetchone(),
                )

                local_cursor.execute(
                    f"""
                    SELECT TOP 1
                        risk.RiskAssessmentId,
                        risk.ProspectId,
                        risk.VendorGroup,
                        risk.AssessmentStatus,
                        batch.ApprovalBatchId,
                        batch.BatchStatus
                    FROM {RISK_HEADER_TABLE} AS risk
                    INNER JOIN {APPROVAL_BATCH_TABLE} AS batch
                        ON batch.RiskAssessmentId =
                           risk.RiskAssessmentId
                       AND batch.ProspectId =
                           risk.ProspectId
                    WHERE risk.ProspectId = ?
                      AND batch.BatchStatus = N'APPROVED'
                    ORDER BY batch.ApprovalBatchId DESC
                    """,
                    prospect_id,
                )

                risk_assessment = _row_to_dict(
                    local_cursor,
                    local_cursor.fetchone(),
                )

                if not risk_assessment:
                    raise D365VendorCreationError(
                        "An approved risk assessment was not found for "
                        f"ProspectId={prospect_id}"
                    )

                approved_vendor_group = _text(
                    _value(
                        risk_assessment,
                        "VendorGroup",
                    )
                )

                if not approved_vendor_group:
                    raise D365VendorCreationError(
                        "VendorGroup is missing in the approved "
                        "risk assessment for "
                        f"ProspectId={prospect_id}"
                    )

                cloud_cursor.execute(
                    f"""
                    SELECT *
                    FROM {CONTACT_TABLE}
                    WHERE ProspectId = ?
                    """,
                    prospect_id,
                )

                contacts = _rows_to_dicts(
                    cloud_cursor,
                    cloud_cursor.fetchall(),
                )

                cloud_cursor.execute(
                    f"""
                    SELECT *
                    FROM {ATTACHMENT_TABLE}
                    WHERE ProspectId = ?
                    """,
                    prospect_id,
                )

                attachments = _rows_to_dicts(
                    cloud_cursor,
                    cloud_cursor.fetchall(),
                )

                return {
                    "REGISTRATION": registration,
                    "PROSPECT": prospect,
                    "RISK_ASSESSMENT": risk_assessment,
                    "CONTACTS": contacts,
                    "ATTACHMENTS": attachments,
                }

            finally:
                local_cursor.close()
                cloud_cursor.close()


def build_d365_vendor_payload(
    prospect_id: str,
) -> tuple[dict[str, Any], str | None]:
    prospect_id = _text(
        prospect_id
    ).upper()

    if not prospect_id:
        raise D365VendorCreationError(
            "ProspectId is required"
        )

    source = _fetch_vendor_source_data(
        prospect_id
    )

    registration = source["REGISTRATION"]
    prospect = source["PROSPECT"]
    risk_assessment = source["RISK_ASSESSMENT"]
    contacts = source["CONTACTS"]
    attachments = source["ATTACHMENTS"]

    address_codes = _resolve_d365_address_codes(
        registration
    )

    existing_vendor_account = _text(
        _value(
            prospect,
            "VendorAccount",
            "VendorId",
        )
    ) or None

    company_name = (
        _text(
            _value(
                registration,
                "CompanyName",
            )
        )
        or _text(
            _value(
                prospect,
                "Name",
                "VendorName",
            )
        )
    )

    vendor_group = _text(
        _value(
            risk_assessment,
            "VendorGroup",
        )
    )

    if not vendor_group:
        raise D365VendorCreationError(
            "VendorGroup is missing in the approved "
            "risk assessment for "
            f"ProspectId={prospect_id}"
        )

    country = address_codes["COUNTRY"]

    payment_terms = (
        _text(
            _value(
                registration,
                "PaymentTerms",
            )
        )
        or D365_DEFAULT_PAYMENT_TERMS
    )

    payment_mode = (
        _text(
            _value(
                registration,
                "PaymentMode",
            )
        )
        or D365_DEFAULT_PAYMENT_MODE
    )

    registration_type = _text(
        _value(
            registration,
            "RegistrationType",
        )
    )

    registration_number = _text(
        _value(
            registration,
            "RegistrationNumber",
        )
    )

    gst_numbers: list[dict[str, Any]] = []

    if registration_number and (
        not registration_type
        or "GST" in registration_type.upper()
    ):
        gst_numbers.append(
            {
                "GSTNumber": registration_number,
                "name": (
                    registration_type
                    or "Primary GST"
                ),
                "IsPrimary": True,
            }
        )

    address_values = {
        "street": _text(
            _value(registration, "Street")
        ),
        "city": address_codes["CITY"],
        "district": address_codes["DISTRICT"],
        "state": address_codes["STATE"],
        "zipCode": address_codes["ZIPCODE"],
    }

    addresses: list[dict[str, Any]] = []

    if any(address_values.values()):
        addresses.append(
            {
                "Name": "Primary Address",
                "street": address_values["street"],
                "streetNumber": "",
                "city": address_values["city"],
                "district": address_values["district"],
                "state": address_values["state"],
                "country": country,
                "zipCode": address_values["zipCode"],
                "BuildingCompliment": "",
                "PostBox": "",
                "County": "",
                "IsPrimary": True,
            }
        )

    contacts = sorted(
        contacts,
        key=lambda item: (
            not _as_bool(
                _value(item, "IsPrimary")
            ),
            int(
                _value(
                    item,
                    "ContactId",
                    default=0,
                )
                or 0
            ),
        ),
    )

    contact_payload: list[dict[str, Any]] = []

    for contact in contacts:
        contact_payload.append(
            {
                "Name": _text(
                    _value(
                        contact,
                        "ContactName",
                        "Name",
                    ),
                    "Vendor Contact",
                ),
                "email": _text(
                    _value(
                        contact,
                        "EmailAddress",
                        "Email",
                    )
                ),
                "Prevemail": "",
                "phone": _text(
                    _value(
                        contact,
                        "MobileNumber",
                        "Phone",
                    )
                ),
                "Prevphone": "",
                "IsPrimary": _as_bool(
                    _value(contact, "IsPrimary")
                ),
            }
        )

    if (
        contact_payload
        and not any(
            item["IsPrimary"]
            for item in contact_payload
        )
    ):
        contact_payload[0]["IsPrimary"] = True

    if not contact_payload:
        prospect_email = _text(
            _value(
                prospect,
                "Email",
                "EmailAddress",
            )
        )

        if prospect_email:
            contact_payload.append(
                {
                    "Name": company_name,
                    "email": prospect_email,
                    "Prevemail": "",
                    "phone": "",
                    "Prevphone": "",
                    "IsPrimary": True,
                }
            )

    attachment_payload: list[
        dict[str, Any]
    ] = []

    for attachment in attachments:
        file_name = _text(
            _value(
                attachment,
                "FileName",
            )
        )

        content_type = _text(
            _value(
                attachment,
                "ContentType",
            )
        )

        explicit_extension = _text(
            _value(
                attachment,
                "FileExtension",
                "Extension",
            )
        )

        raw_content = _value(
            attachment,
            "FileBytes",
            "FileContent",
            "Content",
        )

        content = _base64_content(
            raw_content
        )

        if not file_name or not content:
            continue

        extension = _file_extension(
            file_name=file_name,
            content_type=content_type,
            explicit_extension=explicit_extension,
        )

        attachment_payload.append(
            {
                "fileName": (
                    Path(file_name).stem
                    or file_name
                ),
                "extension": extension,
                "content": content,
            }
        )

    contract = {
        "ProspectId": prospect_id,
        "Vendorid": (
            existing_vendor_account or ""
        ),
        "vendorName": company_name,
        "searchName": company_name,
        "vendorGroup": vendor_group,
        "currency": D365_DEFAULT_CURRENCY,
        "country": country,
        "language": D365_DEFAULT_LANGUAGE,
        "panNumber": _text(
            _value(
                registration,
                "PANNumber",
            )
        ),
        "paymentTerms": payment_terms,
        "paymentMode": payment_mode,
        "taxGroup": "",
        "gstNumber": gst_numbers,
        "address": addresses,
        "contact": contact_payload,
        "attachments": attachment_payload,
    }

    mandatory_fields = {
        "vendorName": contract["vendorName"],
        "vendorGroup": contract["vendorGroup"],
        "currency": contract["currency"],
        "country": contract["country"],
    }

    missing = [
        field_name
        for field_name, field_value
        in mandatory_fields.items()
        if not _text(field_value)
    ]

    if missing:
        raise D365VendorCreationError(
            "Missing mandatory D365 vendor fields: "
            + ", ".join(missing)
        )

    return {
        "_contract": contract
    }, existing_vendor_account


def _save_vendor_account(
    prospect_id: str,
    vendor_account: str,
) -> None:
    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            cursor.execute(
                "SET XACT_ABORT ON;"
            )

            cursor.execute(
                f"""
                UPDATE {PROSPECT_TABLE}
                SET VendorAccount = ?
                WHERE ProspectId = ?
                """,
                (
                    vendor_account,
                    prospect_id,
                ),
            )

            if cursor.rowcount != 1:
                raise D365VendorCreationError(
                    "VendorAccount could not be saved because "
                    "d365_VendorProspect was not found for "
                    f"ProspectId={prospect_id}"
                )

            cursor.execute(
                f"""
                UPDATE {REGISTRATION_TABLE}
                SET
                    Status = N'VENDOR_CREATED',
                    IsDraft = 1,
                    ModifiedOn = SYSUTCDATETIME()
                WHERE ProspectId = ?
                """,
                prospect_id,
            )

            if cursor.rowcount != 1:
                raise D365VendorCreationError(
                    "VendorRegistration could not be updated "
                    "to VENDOR_CREATED"
                )

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            cursor.close()

def create_vendor_in_d365(
    prospect_id: str,
) -> dict[str, Any]:
    """
    Triggered only after all approval assignments are APPROVED.

    Approval stays APPROVED if the D365 call fails.
    Registration is changed to VENDOR_CREATED only after D365
    returns a VendorAccount.
    """

    prospect_id = _text(
        prospect_id
    ).upper()

    try:
        payload, existing_vendor_account = (
            build_d365_vendor_payload(
                prospect_id
            )
        )

        # Already created in D365 earlier.
        if existing_vendor_account:
            _save_vendor_account(
                prospect_id,
                existing_vendor_account,
            )

            return {
                "TRIGGERED": False,
                "SUCCESS": True,
                "SKIPPED": True,
                "PROSPECT_ID": prospect_id,
                "VENDOR_ACCOUNT": existing_vendor_account,
                "HTTP_STATUS": None,
                "REQUEST_URL": None,
                "CONTENT_TYPE": None,
                "ERROR": None,
            }

        token = get_d365_token()

        logger.info(
            "[D365 VENDOR CREATION] ProspectId={} URL={!r}",
            prospect_id,
            D365_VENDOR_CREATION_URL,
        )

        response = requests.post(
            url=D365_VENDOR_CREATION_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=D365_REQUEST_TIMEOUT_SECONDS,
            verify=D365_VERIFY_SSL,
        )

        logger.info(
            "[D365 VENDOR CREATION RESPONSE] "
            "ProspectId={} HTTP_STATUS={} REQUEST_URL={!r} "
            "CONTENT_TYPE={!r}",
            prospect_id,
            response.status_code,
            response.request.url,
            response.headers.get("Content-Type"),
        )

        result = _parse_d365_response(
            response
        )

        if response.status_code >= 400:
            return {
                "TRIGGERED": True,
                "SUCCESS": False,
                "SKIPPED": False,
                "PROSPECT_ID": prospect_id,
                "VENDOR_ACCOUNT": None,
                "HTTP_STATUS": response.status_code,
                "REQUEST_URL": response.request.url,
                "CONTENT_TYPE": response.headers.get(
                    "Content-Type"
                ),
                "ERROR": (
                    _text(
                        result.get(
                            "ErrorMessage"
                        )
                    )
                    or response.text[:2000]
                    or (
                        "D365 returned HTTP "
                        f"{response.status_code}"
                    )
                ),
            }

        success_value = result.get(
            "Success",
            result.get("success"),
        )

        success = (
            success_value is True
            or _text(success_value).lower()
            == "true"
        )

        if not success:
            return {
                "TRIGGERED": True,
                "SUCCESS": False,
                "SKIPPED": False,
                "PROSPECT_ID": prospect_id,
                "VENDOR_ACCOUNT": None,
                "HTTP_STATUS": response.status_code,
                "REQUEST_URL": response.request.url,
                "CONTENT_TYPE": response.headers.get(
                    "Content-Type"
                ),
                "ERROR": (
                    _text(
                        result.get(
                            "ErrorMessage"
                        )
                    )
                    or (
                        "D365 vendor creation returned "
                        "Success=false"
                    )
                ),
            }

        vendor_account = (
            _text(
                result.get("VendorAccount")
            )
            or _text(
                result.get("vendorAccount")
            )
            or _text(
                result.get("VendorId")
            )
            or _text(
                result.get("vendorId")
            )
            or _text(
                result.get("VendAccount")
            )
            or _text(
                result.get("vendAccount")
            )
        )

        if not vendor_account:
            return {
                "TRIGGERED": True,
                "SUCCESS": False,
                "SKIPPED": False,
                "PROSPECT_ID": prospect_id,
                "VENDOR_ACCOUNT": None,
                "HTTP_STATUS": response.status_code,
                "REQUEST_URL": response.request.url,
                "CONTENT_TYPE": response.headers.get(
                    "Content-Type"
                ),
                "ERROR": (
                    "D365 returned Success=true but "
                    "VendorAccount is empty"
                ),
            }

        _save_vendor_account(
            prospect_id,
            vendor_account,
        )

        return {
            "TRIGGERED": True,
            "SUCCESS": True,
            "SKIPPED": False,
            "PROSPECT_ID": prospect_id,
            "VENDOR_ACCOUNT": vendor_account,
            "HTTP_STATUS": response.status_code,
            "REQUEST_URL": response.request.url,
            "CONTENT_TYPE": response.headers.get(
                "Content-Type"
            ),
            "ERROR": None,
        }

    except requests.Timeout:
        logger.exception(
            "[D365 VENDOR CREATION] Timeout for "
            f"ProspectId={prospect_id}"
        )

        return {
            "TRIGGERED": True,
            "SUCCESS": False,
            "SKIPPED": False,
            "PROSPECT_ID": prospect_id,
            "VENDOR_ACCOUNT": None,
            "HTTP_STATUS": None,
            "REQUEST_URL": D365_VENDOR_CREATION_URL,
            "CONTENT_TYPE": None,
            "ERROR": "D365 request timed out",
        }

    except requests.RequestException as exc:
        logger.exception(
            "[D365 VENDOR CREATION] HTTP failure for "
            f"ProspectId={prospect_id}"
        )

        return {
            "TRIGGERED": True,
            "SUCCESS": False,
            "SKIPPED": False,
            "PROSPECT_ID": prospect_id,
            "VENDOR_ACCOUNT": None,
            "HTTP_STATUS": (
                exc.response.status_code
                if exc.response is not None
                else None
            ),
            "REQUEST_URL": (
                exc.request.url
                if exc.request is not None
                else D365_VENDOR_CREATION_URL
            ),
            "CONTENT_TYPE": (
                exc.response.headers.get(
                    "Content-Type"
                )
                if exc.response is not None
                else None
            ),
            "ERROR": (
                exc.response.text[:2000]
                if exc.response is not None
                else str(exc)
            ),
        }

    except Exception as exc:
        logger.exception(
            "[D365 VENDOR CREATION] Failed for "
            f"ProspectId={prospect_id}"
        )

        return {
            "TRIGGERED": True,
            "SUCCESS": False,
            "SKIPPED": False,
            "PROSPECT_ID": prospect_id,
            "VENDOR_ACCOUNT": None,
            "HTTP_STATUS": None,
            "REQUEST_URL": D365_VENDOR_CREATION_URL,
            "CONTENT_TYPE": None,
            "ERROR": str(exc),
        }

# from __future__ import annotations

# import base64
# import json
# import mimetypes
# import os
# from pathlib import Path
# from typing import Any

# import requests
# from loguru import logger

# from app.core.config import settings
# from app.core.d365_auth import get_d365_token
# from app.db.base import get_secondary_connection


# DB_SCHEMA = getattr(
#     settings,
#     "SECONDARY_DB_SCHEMA",
#     getattr(settings, "DB_SCHEMA", "dev"),
# )

# REGISTRATION_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
# )
# PROSPECT_TABLE = (
#     f"[{DB_SCHEMA}].[d365_VendorProspect]"
# )
# CONTACT_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorContact]"
# )
# ATTACHMENT_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorFileAttachment]"
# )
# RISK_HEADER_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorRiskAssessment]"
# )
# APPROVAL_BATCH_TABLE = (
#     f"[{DB_SCHEMA}].[HIQ_VendorApprovalBatch]"
# )

# D365_RESOURCE = os.getenv(
#     "D365_RESOURCE",
#     "",
# ).strip().rstrip("/")
# # D365_VENDOR_CREATION_URL=settings.D365_VENDOR_CREATION_URL
# D365_VENDOR_CREATION_URL = str(
#     settings.D365_VENDOR_CREATION_URL
# ).strip().rstrip("/")

# if not D365_VENDOR_CREATION_URL:
#     raise RuntimeError(
#         "D365_VENDOR_CREATION_URL is empty"
#     )

# D365_DEFAULT_CURRENCY = os.getenv(
#     "D365_DEFAULT_CURRENCY",
#     "INR",
# ).strip()
# D365_DEFAULT_COUNTRY = os.getenv(
#     "D365_DEFAULT_COUNTRY",
#     "IND",
# ).strip().upper()
# D365_DEFAULT_LANGUAGE = os.getenv(
#     "D365_DEFAULT_LANGUAGE",
#     "en-us",
# ).strip()
# # D365_DEFAULT_TAX_GROUP = os.getenv(
# #     "D365_DEFAULT_TAX_GROUP",
# #     "GST",
# # ).strip()
# D365_DEFAULT_PAYMENT_TERMS = os.getenv(
#     "D365_DEFAULT_PAYMENT_TERMS",
#     "",
# ).strip()
# D365_DEFAULT_PAYMENT_MODE = os.getenv(
#     "D365_DEFAULT_PAYMENT_MODE",
#     "",
# ).strip()

# D365_REQUEST_TIMEOUT_SECONDS = int(
#     os.getenv(
#         "D365_REQUEST_TIMEOUT_SECONDS",
#         "250",
#     )
# )
# D365_VERIFY_SSL = os.getenv(
#     "D365_VERIFY_SSL",
#     "false",
# ).strip().lower() in {
#     "1",
#     "true",
#     "yes",
#     "y",
# }


# class D365VendorCreationError(RuntimeError):
#     pass


# def _row_to_dict(
#     cursor,
#     row,
# ) -> dict[str, Any]:
#     if row is None or cursor.description is None:
#         return {}

#     columns = [
#         column[0]
#         for column in cursor.description
#     ]

#     return dict(zip(columns, row))


# def _rows_to_dicts(
#     cursor,
#     rows,
# ) -> list[dict[str, Any]]:
#     if cursor.description is None:
#         return []

#     columns = [
#         column[0]
#         for column in cursor.description
#     ]

#     return [
#         dict(zip(columns, row))
#         for row in rows
#     ]


# def _value(
#     row: dict[str, Any],
#     *column_names: str,
#     default: Any = None,
# ) -> Any:
#     """Case-insensitive lookup supporting old and new column names."""

#     normalized = {
#         str(key).lower(): value
#         for key, value in row.items()
#     }

#     for column_name in column_names:
#         key = column_name.lower()

#         if key in normalized:
#             return normalized[key]

#     return default


# def _text(
#     value: Any,
#     default: str = "",
# ) -> str:
#     if value is None:
#         return default

#     normalized = str(value).strip()
#     return normalized or default


# def _as_bool(value: Any) -> bool:
#     if isinstance(value, bool):
#         return value

#     if isinstance(value, (int, float)):
#         return bool(value)

#     return _text(value).lower() in {
#         "1",
#         "true",
#         "yes",
#         "y",
#     }


# def _country_code(value: Any) -> str:
#     country = _text(
#         value,
#         D365_DEFAULT_COUNTRY,
#     ).upper()

#     country_map = {
#         "INDIA": "IND",
#         "IND": "IND",
#         "UNITED STATES": "USA",
#         "UNITED STATES OF AMERICA": "USA",
#         "USA": "USA",
#         "UNITED KINGDOM": "GBR",
#         "UK": "GBR",
#         "GBR": "GBR",
#         "UNITED ARAB EMIRATES": "ARE",
#         "UAE": "ARE",
#         "ARE": "ARE",
#     }

#     if country in country_map:
#         return country_map[country]

#     if len(country) == 3:
#         return country

#     return D365_DEFAULT_COUNTRY


# def _file_extension(
#     file_name: str,
#     content_type: str,
#     explicit_extension: str,
# ) -> str:
#     if explicit_extension:
#         return explicit_extension.lstrip(".").lower()

#     suffix = Path(file_name).suffix.lstrip(".").lower()

#     if suffix:
#         return suffix

#     guessed = mimetypes.guess_extension(
#         content_type or ""
#     )

#     if guessed:
#         return guessed.lstrip(".").lower()

#     return "bin"


# def _base64_content(value: Any) -> str:
#     if value is None:
#         return ""

#     if isinstance(value, memoryview):
#         value = value.tobytes()

#     if isinstance(value, bytearray):
#         value = bytes(value)

#     if isinstance(value, bytes):
#         return base64.b64encode(value).decode(
#             "ascii"
#         )

#     if isinstance(value, str):
#         normalized = value.strip()

#         if normalized.startswith("data:") and "," in normalized:
#             return normalized.split(",", 1)[1]

#         return normalized

#     raise D365VendorCreationError(
#         "Unsupported attachment content type: "
#         f"{type(value).__name__}"
#     )


# def _parse_d365_response(
#     response: requests.Response,
# ) -> dict[str, Any]:
#     try:
#         data: Any = response.json()
#     except ValueError:
#         raw = response.text.strip()

#         try:
#             data = json.loads(raw)
#         except (TypeError, ValueError):
#             data = {
#                 "Success": False,
#                 "ErrorMessage": raw,
#             }

#     if isinstance(data, str):
#         try:
#             data = json.loads(data)
#         except ValueError:
#             data = {
#                 "Success": False,
#                 "ErrorMessage": data,
#             }

#     if isinstance(data, dict):
#         for wrapper_name in (
#             "d",
#             "value",
#             "response",
#         ):
#             wrapped = data.get(wrapper_name)

#             if isinstance(wrapped, str):
#                 try:
#                     wrapped = json.loads(wrapped)
#                 except ValueError:
#                     pass

#             if isinstance(wrapped, dict):
#                 data = wrapped
#                 break

#     if not isinstance(data, dict):
#         return {
#             "Success": False,
#             "ErrorMessage": str(data),
#         }

#     return data


# def _fetch_vendor_source_data(
#     prospect_id: str,
# ) -> dict[str, Any]:
#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 f"""
#                 SELECT TOP 1 *
#                 FROM {REGISTRATION_TABLE}
#                 WHERE ProspectId = ?
#                 """,
#                 prospect_id,
#             )

#             registration = _row_to_dict(
#                 cursor,
#                 cursor.fetchone(),
#             )

#             if not registration:
#                 raise D365VendorCreationError(
#                     "Vendor registration was not found for "
#                     f"ProspectId={prospect_id}"
#                 )

#             cursor.execute(
#                 f"""
#                 SELECT TOP 1 *
#                 FROM {PROSPECT_TABLE}
#                 WHERE ProspectId = ?
#                 """,
#                 prospect_id,
#             )

#             prospect = _row_to_dict(
#                 cursor,
#                 cursor.fetchone(),
#             )

#             cursor.execute(
#                 f"""
#                 SELECT TOP 1
#                     risk.RiskAssessmentId,
#                     risk.ProspectId,
#                     risk.VendorGroup,
#                     risk.AssessmentStatus,
#                     batch.ApprovalBatchId,
#                     batch.BatchStatus
#                 FROM {RISK_HEADER_TABLE} AS risk
#                 INNER JOIN {APPROVAL_BATCH_TABLE} AS batch
#                     ON batch.RiskAssessmentId =
#                        risk.RiskAssessmentId
#                    AND batch.ProspectId =
#                        risk.ProspectId
#                 WHERE risk.ProspectId = ?
#                   AND batch.BatchStatus = N'APPROVED'
#                 ORDER BY batch.ApprovalBatchId DESC
#                 """,
#                 prospect_id,
#             )

#             risk_assessment = _row_to_dict(
#                 cursor,
#                 cursor.fetchone(),
#             )

#             if not risk_assessment:
#                 raise D365VendorCreationError(
#                     "An approved risk assessment was not found for "
#                     f"ProspectId={prospect_id}"
#                 )

#             approved_vendor_group = _text(
#                 _value(
#                     risk_assessment,
#                     "VendorGroup",
#                 )
#             )

#             if not approved_vendor_group:
#                 raise D365VendorCreationError(
#                     "VendorGroup is missing in the approved "
#                     "risk assessment for "
#                     f"ProspectId={prospect_id}"
#                 )

#             cursor.execute(
#                 f"""
#                 SELECT *
#                 FROM {CONTACT_TABLE}
#                 WHERE ProspectId = ?
#                 """,
#                 prospect_id,
#             )

#             contacts = _rows_to_dicts(
#                 cursor,
#                 cursor.fetchall(),
#             )

#             cursor.execute(
#                 f"""
#                 SELECT *
#                 FROM {ATTACHMENT_TABLE}
#                 WHERE ProspectId = ?
#                 """,
#                 prospect_id,
#             )

#             attachments = _rows_to_dicts(
#                 cursor,
#                 cursor.fetchall(),
#             )

#             return {
#                 "REGISTRATION": registration,
#                 "PROSPECT": prospect,
#                 "RISK_ASSESSMENT": risk_assessment,
#                 "CONTACTS": contacts,
#                 "ATTACHMENTS": attachments,
#             }

#         finally:
#             cursor.close()


# def build_d365_vendor_payload(
#     prospect_id: str,
# ) -> tuple[dict[str, Any], str | None]:
#     prospect_id = _text(
#         prospect_id
#     ).upper()

#     if not prospect_id:
#         raise D365VendorCreationError(
#             "ProspectId is required"
#         )

#     source = _fetch_vendor_source_data(
#         prospect_id
#     )

#     registration = source["REGISTRATION"]
#     prospect = source["PROSPECT"]
#     risk_assessment = source["RISK_ASSESSMENT"]
#     contacts = source["CONTACTS"]
#     attachments = source["ATTACHMENTS"]

#     existing_vendor_account = _text(
#         _value(
#             prospect,
#             "VendorAccount",
#             "VendorId",
#         )
#     ) or None

#     company_name = (
#         _text(
#             _value(
#                 registration,
#                 "CompanyName",
#             )
#         )
#         or _text(
#             _value(
#                 prospect,
#                 "Name",
#                 "VendorName",
#             )
#         )
#     )

#     vendor_group = _text(
#         _value(
#             risk_assessment,
#             "VendorGroup",
#         )
#     )

#     if not vendor_group:
#         raise D365VendorCreationError(
#             "VendorGroup is missing in the approved "
#             "risk assessment for "
#             f"ProspectId={prospect_id}"
#         )

#     country = _country_code(
#         _value(
#             registration,
#             "Country",
#             "CountryOfOrigin",
#             default=D365_DEFAULT_COUNTRY,
#         )
#     )

#     payment_terms = (
#         _text(
#             _value(
#                 registration,
#                 "PaymentTerms",
#             )
#         )
#         or D365_DEFAULT_PAYMENT_TERMS
#     )

#     payment_mode = (
#         _text(
#             _value(
#                 registration,
#                 "PaymentMode",
#             )
#         )
#         or D365_DEFAULT_PAYMENT_MODE
#     )

#     registration_type = _text(
#         _value(
#             registration,
#             "RegistrationType",
#         )
#     )

#     registration_number = _text(
#         _value(
#             registration,
#             "RegistrationNumber",
#         )
#     )

#     gst_numbers: list[dict[str, Any]] = []

#     if registration_number and (
#         not registration_type
#         or "GST" in registration_type.upper()
#     ):
#         gst_numbers.append(
#             {
#                 "GSTNumber": registration_number,
#                 "name": (
#                     registration_type
#                     or "Primary GST"
#                 ),
#                 "IsPrimary": True,
#             }
#         )

#     address_values = {
#         "street": _text(
#             _value(registration, "Street")
#         ),
#         "city": _text(
#             _value(registration, "City")
#         ),
#         "district": _text(
#             _value(registration, "District")
#         ),
#         "state": _text(
#             _value(registration, "State")
#         ),
#         "zipCode": _text(
#             _value(
#                 registration,
#                 "ZipCode",
#                 "PostalCode",
#             )
#         ),
#     }

#     addresses: list[dict[str, Any]] = []

#     if any(address_values.values()):
#         addresses.append(
#             {
#                 "Name": "Primary Address",
#                 "street": address_values["street"],
#                 "streetNumber": "",
#                 "city": address_values["city"],
#                 "district": address_values["district"],
#                 "state": address_values["state"],
#                 "country": country,
#                 "zipCode": address_values["zipCode"],
#                 "BuildingCompliment": "",
#                 "PostBox": "",
#                 "County": "",
#                 "IsPrimary": True,
#             }
#         )

#     contacts = sorted(
#         contacts,
#         key=lambda item: (
#             not _as_bool(
#                 _value(item, "IsPrimary")
#             ),
#             int(
#                 _value(
#                     item,
#                     "ContactId",
#                     default=0,
#                 )
#                 or 0
#             ),
#         ),
#     )

#     contact_payload: list[dict[str, Any]] = []

#     for contact in contacts:
#         contact_payload.append(
#             {
#                 "Name": _text(
#                     _value(
#                         contact,
#                         "ContactName",
#                         "Name",
#                     ),
#                     "Vendor Contact",
#                 ),
#                 "email": _text(
#                     _value(
#                         contact,
#                         "EmailAddress",
#                         "Email",
#                     )
#                 ),
#                 "Prevemail": "",
#                 "phone": _text(
#                     _value(
#                         contact,
#                         "MobileNumber",
#                         "Phone",
#                     )
#                 ),
#                 "Prevphone": "",
#                 "IsPrimary": _as_bool(
#                     _value(contact, "IsPrimary")
#                 ),
#             }
#         )

#     if (
#         contact_payload
#         and not any(
#             item["IsPrimary"]
#             for item in contact_payload
#         )
#     ):
#         contact_payload[0]["IsPrimary"] = True

#     if not contact_payload:
#         prospect_email = _text(
#             _value(
#                 prospect,
#                 "Email",
#                 "EmailAddress",
#             )
#         )

#         if prospect_email:
#             contact_payload.append(
#                 {
#                     "Name": company_name,
#                     "email": prospect_email,
#                     "Prevemail": "",
#                     "phone": "",
#                     "Prevphone": "",
#                     "IsPrimary": True,
#                 }
#             )

#     attachment_payload: list[
#         dict[str, Any]
#     ] = []

#     for attachment in attachments:
#         file_name = _text(
#             _value(
#                 attachment,
#                 "FileName",
#             )
#         )

#         content_type = _text(
#             _value(
#                 attachment,
#                 "ContentType",
#             )
#         )

#         explicit_extension = _text(
#             _value(
#                 attachment,
#                 "FileExtension",
#                 "Extension",
#             )
#         )

#         raw_content = _value(
#             attachment,
#             "FileBytes",
#             "FileContent",
#             "Content",
#         )

#         content = _base64_content(
#             raw_content
#         )

#         if not file_name or not content:
#             continue

#         extension = _file_extension(
#             file_name=file_name,
#             content_type=content_type,
#             explicit_extension=explicit_extension,
#         )

#         attachment_payload.append(
#             {
#                 "fileName": (
#                     Path(file_name).stem
#                     or file_name
#                 ),
#                 "extension": extension,
#                 "content": content,
#             }
#         )

#     contract = {
#         "ProspectId": prospect_id,
#         "Vendorid": (
#             existing_vendor_account or ""
#         ),
#         "vendorName": company_name,
#         "searchName": company_name,
#         "vendorGroup": vendor_group,
#         "currency": D365_DEFAULT_CURRENCY,
#         "country": country,
#         "language": D365_DEFAULT_LANGUAGE,
#         "panNumber": _text(
#             _value(
#                 registration,
#                 "PANNumber",
#             )
#         ),
#         "paymentTerms": payment_terms,
#         "paymentMode": payment_mode,
#         "taxGroup": "",
#         "gstNumber": gst_numbers,
#         "address": addresses,
#         "contact": contact_payload,
#         "attachments": attachment_payload,
#     }

#     mandatory_fields = {
#         "vendorName": contract["vendorName"],
#         "vendorGroup": contract["vendorGroup"],
#         "currency": contract["currency"],
#         "country": contract["country"],
#     }

#     missing = [
#         field_name
#         for field_name, field_value
#         in mandatory_fields.items()
#         if not _text(field_value)
#     ]

#     if missing:
#         raise D365VendorCreationError(
#             "Missing mandatory D365 vendor fields: "
#             + ", ".join(missing)
#         )

#     return {
#         "_contract": contract
#     }, existing_vendor_account


# def _save_vendor_account(
#     prospect_id: str,
#     vendor_account: str,
# ) -> None:
#     with get_secondary_connection() as connection:
#         cursor = connection.cursor()

#         try:
#             cursor.execute(
#                 "SET XACT_ABORT ON;"
#             )

#             cursor.execute(
#                 f"""
#                 UPDATE {PROSPECT_TABLE}
#                 SET VendorAccount = ?
#                 WHERE ProspectId = ?
#                 """,
#                 (
#                     vendor_account,
#                     prospect_id,
#                 ),
#             )

#             if cursor.rowcount != 1:
#                 raise D365VendorCreationError(
#                     "VendorAccount could not be saved because "
#                     "d365_VendorProspect was not found for "
#                     f"ProspectId={prospect_id}"
#                 )

#             cursor.execute(
#                 f"""
#                 UPDATE {REGISTRATION_TABLE}
#                 SET
#                     Status = N'VENDOR_CREATED',
#                     IsDraft = 1,
#                     ModifiedOn = SYSUTCDATETIME()
#                 WHERE ProspectId = ?
#                 """,
#                 prospect_id,
#             )

#             if cursor.rowcount != 1:
#                 raise D365VendorCreationError(
#                     "VendorRegistration could not be updated "
#                     "to VENDOR_CREATED"
#                 )

#             connection.commit()

#         except Exception:
#             connection.rollback()
#             raise

#         finally:
#             cursor.close()

# def create_vendor_in_d365(
#     prospect_id: str,
# ) -> dict[str, Any]:
#     """
#     Triggered only after all approval assignments are APPROVED.

#     Approval stays APPROVED if the D365 call fails.
#     Registration is changed to VENDOR_CREATED only after D365
#     returns a VendorAccount.
#     """

#     prospect_id = _text(
#         prospect_id
#     ).upper()

#     try:
#         payload, existing_vendor_account = (
#             build_d365_vendor_payload(
#                 prospect_id
#             )
#         )

#         # Already created in D365 earlier.
#         if existing_vendor_account:
#             _save_vendor_account(
#                 prospect_id,
#                 existing_vendor_account,
#             )

#             return {
#                 "TRIGGERED": False,
#                 "SUCCESS": True,
#                 "SKIPPED": True,
#                 "PROSPECT_ID": prospect_id,
#                 "VENDOR_ACCOUNT": existing_vendor_account,
#                 "HTTP_STATUS": None,
#                 "REQUEST_URL": None,
#                 "CONTENT_TYPE": None,
#                 "ERROR": None,
#             }

#         token = get_d365_token()

#         logger.info(
#             "[D365 VENDOR CREATION] ProspectId={} URL={!r}",
#             prospect_id,
#             D365_VENDOR_CREATION_URL,
#         )

#         response = requests.post(
#             url=D365_VENDOR_CREATION_URL,
#             headers={
#                 "Authorization": f"Bearer {token}",
#                 "Content-Type": "application/json",
#                 "Accept": "application/json",
#             },
#             json=payload,
#             timeout=D365_REQUEST_TIMEOUT_SECONDS,
#             verify=D365_VERIFY_SSL,
#         )

#         logger.info(
#             "[D365 VENDOR CREATION RESPONSE] "
#             "ProspectId={} HTTP_STATUS={} REQUEST_URL={!r} "
#             "CONTENT_TYPE={!r}",
#             prospect_id,
#             response.status_code,
#             response.request.url,
#             response.headers.get("Content-Type"),
#         )

#         result = _parse_d365_response(
#             response
#         )

#         if response.status_code >= 400:
#             return {
#                 "TRIGGERED": True,
#                 "SUCCESS": False,
#                 "SKIPPED": False,
#                 "PROSPECT_ID": prospect_id,
#                 "VENDOR_ACCOUNT": None,
#                 "HTTP_STATUS": response.status_code,
#                 "REQUEST_URL": response.request.url,
#                 "CONTENT_TYPE": response.headers.get(
#                     "Content-Type"
#                 ),
#                 "ERROR": (
#                     _text(
#                         result.get(
#                             "ErrorMessage"
#                         )
#                     )
#                     or response.text[:2000]
#                     or (
#                         "D365 returned HTTP "
#                         f"{response.status_code}"
#                     )
#                 ),
#             }

#         success_value = result.get(
#             "Success",
#             result.get("success"),
#         )

#         success = (
#             success_value is True
#             or _text(success_value).lower()
#             == "true"
#         )

#         if not success:
#             return {
#                 "TRIGGERED": True,
#                 "SUCCESS": False,
#                 "SKIPPED": False,
#                 "PROSPECT_ID": prospect_id,
#                 "VENDOR_ACCOUNT": None,
#                 "HTTP_STATUS": response.status_code,
#                 "REQUEST_URL": response.request.url,
#                 "CONTENT_TYPE": response.headers.get(
#                     "Content-Type"
#                 ),
#                 "ERROR": (
#                     _text(
#                         result.get(
#                             "ErrorMessage"
#                         )
#                     )
#                     or (
#                         "D365 vendor creation returned "
#                         "Success=false"
#                     )
#                 ),
#             }

#         vendor_account = (
#             _text(
#                 result.get("VendorAccount")
#             )
#             or _text(
#                 result.get("vendorAccount")
#             )
#             or _text(
#                 result.get("VendorId")
#             )
#             or _text(
#                 result.get("vendorId")
#             )
#             or _text(
#                 result.get("VendAccount")
#             )
#             or _text(
#                 result.get("vendAccount")
#             )
#         )

#         if not vendor_account:
#             return {
#                 "TRIGGERED": True,
#                 "SUCCESS": False,
#                 "SKIPPED": False,
#                 "PROSPECT_ID": prospect_id,
#                 "VENDOR_ACCOUNT": None,
#                 "HTTP_STATUS": response.status_code,
#                 "REQUEST_URL": response.request.url,
#                 "CONTENT_TYPE": response.headers.get(
#                     "Content-Type"
#                 ),
#                 "ERROR": (
#                     "D365 returned Success=true but "
#                     "VendorAccount is empty"
#                 ),
#             }

#         _save_vendor_account(
#             prospect_id,
#             vendor_account,
#         )

#         return {
#             "TRIGGERED": True,
#             "SUCCESS": True,
#             "SKIPPED": False,
#             "PROSPECT_ID": prospect_id,
#             "VENDOR_ACCOUNT": vendor_account,
#             "HTTP_STATUS": response.status_code,
#             "REQUEST_URL": response.request.url,
#             "CONTENT_TYPE": response.headers.get(
#                 "Content-Type"
#             ),
#             "ERROR": None,
#         }

#     except requests.Timeout:
#         logger.exception(
#             "[D365 VENDOR CREATION] Timeout for "
#             f"ProspectId={prospect_id}"
#         )

#         return {
#             "TRIGGERED": True,
#             "SUCCESS": False,
#             "SKIPPED": False,
#             "PROSPECT_ID": prospect_id,
#             "VENDOR_ACCOUNT": None,
#             "HTTP_STATUS": None,
#             "REQUEST_URL": D365_VENDOR_CREATION_URL,
#             "CONTENT_TYPE": None,
#             "ERROR": "D365 request timed out",
#         }

#     except requests.RequestException as exc:
#         logger.exception(
#             "[D365 VENDOR CREATION] HTTP failure for "
#             f"ProspectId={prospect_id}"
#         )

#         return {
#             "TRIGGERED": True,
#             "SUCCESS": False,
#             "SKIPPED": False,
#             "PROSPECT_ID": prospect_id,
#             "VENDOR_ACCOUNT": None,
#             "HTTP_STATUS": (
#                 exc.response.status_code
#                 if exc.response is not None
#                 else None
#             ),
#             "REQUEST_URL": (
#                 exc.request.url
#                 if exc.request is not None
#                 else D365_VENDOR_CREATION_URL
#             ),
#             "CONTENT_TYPE": (
#                 exc.response.headers.get(
#                     "Content-Type"
#                 )
#                 if exc.response is not None
#                 else None
#             ),
#             "ERROR": (
#                 exc.response.text[:2000]
#                 if exc.response is not None
#                 else str(exc)
#             ),
#         }

#     except Exception as exc:
#         logger.exception(
#             "[D365 VENDOR CREATION] Failed for "
#             f"ProspectId={prospect_id}"
#         )

#         return {
#             "TRIGGERED": True,
#             "SUCCESS": False,
#             "SKIPPED": False,
#             "PROSPECT_ID": prospect_id,
#             "VENDOR_ACCOUNT": None,
#             "HTTP_STATUS": None,
#             "REQUEST_URL": D365_VENDOR_CREATION_URL,
#             "CONTENT_TYPE": None,
#             "ERROR": str(exc),
#         }
# # def create_vendor_in_d365(
# #     prospect_id: str,
# # ) -> dict[str, Any]:
# #     """
# #     Triggered only after all approval assignments are APPROVED.

# #     Approval stays APPROVED if the D365 call fails. Registration is
# #     changed to VENDOR_CREATED only after D365 returns a VendorAccount.
# #     """

# #     prospect_id = _text(
# #         prospect_id
# #     ).upper()

# #     try:
# #         payload, existing_vendor_account = (
# #             build_d365_vendor_payload(
# #                 prospect_id
# #             )
# #         )

# #         if existing_vendor_account:
# #             _save_vendor_account(
# #                 prospect_id,
# #                 existing_vendor_account,
# #             )

# #             return {
# #                 "TRIGGERED": False,
# #                 "SUCCESS": True,
# #                 "SKIPPED": True,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": (
# #                     existing_vendor_account
# #                 ),
# #                 "HTTP_STATUS": None,
# #                 "ERROR": None,
# #             }

# #         token = get_d365_token()


# #         response = requests.post(
# #             D365_VENDOR_CREATION_URL,
# #             headers={
# #                 "Authorization": f"Bearer {token}",
# #                 "Content-Type": "application/json",
# #                 "Accept": "application/json",
# #             },
# #             json=payload,
# #             timeout=(
# #                 D365_REQUEST_TIMEOUT_SECONDS
# #             ),
# #             verify=False,
# #         )

# #         result = _parse_d365_response(
# #             response
# #         )

# #         if response.status_code >= 400:
# #             return {
# #                 "TRIGGERED": True,
# #                 "SUCCESS": False,
# #                 "SKIPPED": False,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": None,
# #                 "HTTP_STATUS": response.status_code,
# #                 "ERROR": (
# #                     _text(
# #                         result.get(
# #                             "ErrorMessage"
# #                         )
# #                     )
# #                     or response.text[:2000]
# #                     or (
# #                         "D365 returned HTTP "
# #                         f"{response.status_code}"
# #                     )
# #                 ),
# #             }

# #         success_value = result.get(
# #             "Success",
# #             result.get("success"),
# #         )

# #         success = (
# #             success_value is True
# #             or _text(success_value).lower()
# #             == "true"
# #         )

# #         if not success:
# #             return {
# #                 "TRIGGERED": True,
# #                 "SUCCESS": False,
# #                 "SKIPPED": False,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": None,
# #                 "HTTP_STATUS": response.status_code,
# #                 "ERROR": (
# #                     _text(
# #                         result.get(
# #                             "ErrorMessage"
# #                         )
# #                     )
# #                     or (
# #                         "D365 vendor creation returned "
# #                         "Success=false"
# #                     )
# #                 ),
# #             }

# #         vendor_account = (
# #             _text(
# #                 result.get("VendorAccount")
# #             )
# #             or _text(
# #                 result.get("vendorAccount")
# #             )
# #         )

# #         if not vendor_account:
# #             return {
# #                 "TRIGGERED": True,
# #                 "SUCCESS": False,
# #                 "SKIPPED": False,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": None,
# #                 "HTTP_STATUS": response.status_code,
# #                 "ERROR": (
# #                     "D365 returned Success=true but "
# #                     "VendorAccount is empty"
# #                 ),
# #             }

# #         _save_vendor_account(
# #             prospect_id,
# #             vendor_account,
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": True,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": vendor_account,
# #             "HTTP_STATUS": response.status_code,
# #             "ERROR": None,
# #         }

# #     except requests.Timeout:
# #         logger.exception(
# #             "[D365 VENDOR CREATION] Timeout for "
# #             f"ProspectId={prospect_id}"
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": False,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": None,
# #             "HTTP_STATUS": None,
# #             "ERROR": "D365 request timed out",
# #         }

# #     except requests.RequestException as exc:
# #         logger.exception(
# #             "[D365 VENDOR CREATION] HTTP failure for "
# #             f"ProspectId={prospect_id}"
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": False,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": None,
# #             "HTTP_STATUS": (
# #                 exc.response.status_code
# #                 if exc.response is not None
# #                 else None
# #             ),
# #             "ERROR": (
# #                 exc.response.text[:2000]
# #                 if exc.response is not None
# #                 else str(exc)
# #             ),
# #         }

# #     except Exception as exc:
# #         logger.exception(
# #             "[D365 VENDOR CREATION] Failed for "
# #             f"ProspectId={prospect_id}"
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": False,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": None,
# #             "HTTP_STATUS": None,
# #             "ERROR": str(exc),
# #         }



# # from __future__ import annotations
# # import base64
# # import json
# # import mimetypes
# # import os
# # from pathlib import Path
# # from typing import Any

# # import requests
# # from loguru import logger

# # from app.core.config import settings
# # from app.core.d365_auth import get_d365_token
# # from app.db.base import get_secondary_connection


# # DB_SCHEMA = getattr(
# #     settings,
# #     "SECONDARY_DB_SCHEMA",
# #     getattr(settings, "DB_SCHEMA", "dev"),
# # )

# # REGISTRATION_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorRegistration]"
# # )
# # PROSPECT_TABLE = (
# #     f"[{DB_SCHEMA}].[d365_VendorProspect]"
# # )
# # CONTACT_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorContact]"
# # )
# # ATTACHMENT_TABLE = (
# #     f"[{DB_SCHEMA}].[HIQ_VendorFileAttachment]"
# # )

# # D365_RESOURCE = os.getenv(
# #     "D365_RESOURCE",
# #     "",
# # ).strip().rstrip("/")
# # # D365_VENDOR_CREATION_URL=settings.D365_VENDOR_CREATION_URL
# # D365_VENDOR_CREATION_URL = str(
# #     settings.D365_VENDOR_CREATION_URL
# # ).strip().rstrip("/")

# # if not D365_VENDOR_CREATION_URL:
# #     raise RuntimeError(
# #         "D365_VENDOR_CREATION_URL is empty"
# #     )

# # D365_DEFAULT_VENDOR_GROUP = os.getenv(
# #     "D365_DEFAULT_VENDOR_GROUP",
# #     "LOCAL",
# # ).strip()
# # D365_DEFAULT_CURRENCY = os.getenv(
# #     "D365_DEFAULT_CURRENCY",
# #     "INR",
# # ).strip()
# # D365_DEFAULT_COUNTRY = os.getenv(
# #     "D365_DEFAULT_COUNTRY",
# #     "IND",
# # ).strip().upper()
# # D365_DEFAULT_LANGUAGE = os.getenv(
# #     "D365_DEFAULT_LANGUAGE",
# #     "en-us",
# # ).strip()
# # D365_DEFAULT_TAX_GROUP = os.getenv(
# #     "D365_DEFAULT_TAX_GROUP",
# #     "GST",
# # ).strip()
# # D365_DEFAULT_PAYMENT_TERMS = os.getenv(
# #     "D365_DEFAULT_PAYMENT_TERMS",
# #     "",
# # ).strip()
# # D365_DEFAULT_PAYMENT_MODE = os.getenv(
# #     "D365_DEFAULT_PAYMENT_MODE",
# #     "",
# # ).strip()

# # D365_REQUEST_TIMEOUT_SECONDS = int(
# #     os.getenv(
# #         "D365_REQUEST_TIMEOUT_SECONDS",
# #         "120",
# #     )
# # )
# # D365_VERIFY_SSL = os.getenv(
# #     "D365_VERIFY_SSL",
# #     "false",
# # ).strip().lower() in {
# #     "1",
# #     "true",
# #     "yes",
# #     "y",
# # }


# # class D365VendorCreationError(RuntimeError):
# #     pass


# # def _row_to_dict(
# #     cursor,
# #     row,
# # ) -> dict[str, Any]:
# #     if row is None or cursor.description is None:
# #         return {}

# #     columns = [
# #         column[0]
# #         for column in cursor.description
# #     ]

# #     return dict(zip(columns, row))


# # def _rows_to_dicts(
# #     cursor,
# #     rows,
# # ) -> list[dict[str, Any]]:
# #     if cursor.description is None:
# #         return []

# #     columns = [
# #         column[0]
# #         for column in cursor.description
# #     ]

# #     return [
# #         dict(zip(columns, row))
# #         for row in rows
# #     ]


# # def _value(
# #     row: dict[str, Any],
# #     *column_names: str,
# #     default: Any = None,
# # ) -> Any:
# #     """Case-insensitive lookup supporting old and new column names."""

# #     normalized = {
# #         str(key).lower(): value
# #         for key, value in row.items()
# #     }

# #     for column_name in column_names:
# #         key = column_name.lower()

# #         if key in normalized:
# #             return normalized[key]

# #     return default


# # def _text(
# #     value: Any,
# #     default: str = "",
# # ) -> str:
# #     if value is None:
# #         return default

# #     normalized = str(value).strip()
# #     return normalized or default


# # def _as_bool(value: Any) -> bool:
# #     if isinstance(value, bool):
# #         return value

# #     if isinstance(value, (int, float)):
# #         return bool(value)

# #     return _text(value).lower() in {
# #         "1",
# #         "true",
# #         "yes",
# #         "y",
# #     }


# # def _country_code(value: Any) -> str:
# #     country = _text(
# #         value,
# #         D365_DEFAULT_COUNTRY,
# #     ).upper()

# #     country_map = {
# #         "INDIA": "IND",
# #         "IND": "IND",
# #         "UNITED STATES": "USA",
# #         "UNITED STATES OF AMERICA": "USA",
# #         "USA": "USA",
# #         "UNITED KINGDOM": "GBR",
# #         "UK": "GBR",
# #         "GBR": "GBR",
# #         "UNITED ARAB EMIRATES": "ARE",
# #         "UAE": "ARE",
# #         "ARE": "ARE",
# #     }

# #     if country in country_map:
# #         return country_map[country]

# #     if len(country) == 3:
# #         return country

# #     return D365_DEFAULT_COUNTRY


# # def _file_extension(
# #     file_name: str,
# #     content_type: str,
# #     explicit_extension: str,
# # ) -> str:
# #     if explicit_extension:
# #         return explicit_extension.lstrip(".").lower()

# #     suffix = Path(file_name).suffix.lstrip(".").lower()

# #     if suffix:
# #         return suffix

# #     guessed = mimetypes.guess_extension(
# #         content_type or ""
# #     )

# #     if guessed:
# #         return guessed.lstrip(".").lower()

# #     return "bin"


# # def _base64_content(value: Any) -> str:
# #     if value is None:
# #         return ""

# #     if isinstance(value, memoryview):
# #         value = value.tobytes()

# #     if isinstance(value, bytearray):
# #         value = bytes(value)

# #     if isinstance(value, bytes):
# #         return base64.b64encode(value).decode(
# #             "ascii"
# #         )

# #     if isinstance(value, str):
# #         normalized = value.strip()

# #         if normalized.startswith("data:") and "," in normalized:
# #             return normalized.split(",", 1)[1]

# #         return normalized

# #     raise D365VendorCreationError(
# #         "Unsupported attachment content type: "
# #         f"{type(value).__name__}"
# #     )


# # def _parse_d365_response(
# #     response: requests.Response,
# # ) -> dict[str, Any]:
# #     try:
# #         data: Any = response.json()
# #     except ValueError:
# #         raw = response.text.strip()

# #         try:
# #             data = json.loads(raw)
# #         except (TypeError, ValueError):
# #             data = {
# #                 "Success": False,
# #                 "ErrorMessage": raw,
# #             }

# #     if isinstance(data, str):
# #         try:
# #             data = json.loads(data)
# #         except ValueError:
# #             data = {
# #                 "Success": False,
# #                 "ErrorMessage": data,
# #             }

# #     if isinstance(data, dict):
# #         for wrapper_name in (
# #             "d",
# #             "value",
# #             "response",
# #         ):
# #             wrapped = data.get(wrapper_name)

# #             if isinstance(wrapped, str):
# #                 try:
# #                     wrapped = json.loads(wrapped)
# #                 except ValueError:
# #                     pass

# #             if isinstance(wrapped, dict):
# #                 data = wrapped
# #                 break

# #     if not isinstance(data, dict):
# #         return {
# #             "Success": False,
# #             "ErrorMessage": str(data),
# #         }

# #     return data


# # def _fetch_vendor_source_data(
# #     prospect_id: str,
# # ) -> dict[str, Any]:
# #     with get_secondary_connection() as connection:
# #         cursor = connection.cursor()

# #         try:
# #             cursor.execute(
# #                 f"""
# #                 SELECT TOP 1 *
# #                 FROM {REGISTRATION_TABLE}
# #                 WHERE ProspectId = ?
# #                 """,
# #                 prospect_id,
# #             )

# #             registration = _row_to_dict(
# #                 cursor,
# #                 cursor.fetchone(),
# #             )

# #             if not registration:
# #                 raise D365VendorCreationError(
# #                     "Vendor registration was not found for "
# #                     f"ProspectId={prospect_id}"
# #                 )

# #             cursor.execute(
# #                 f"""
# #                 SELECT TOP 1 *
# #                 FROM {PROSPECT_TABLE}
# #                 WHERE ProspectId = ?
# #                 """,
# #                 prospect_id,
# #             )

# #             prospect = _row_to_dict(
# #                 cursor,
# #                 cursor.fetchone(),
# #             )

# #             cursor.execute(
# #                 f"""
# #                 SELECT *
# #                 FROM {CONTACT_TABLE}
# #                 WHERE ProspectId = ?
# #                 """,
# #                 prospect_id,
# #             )

# #             contacts = _rows_to_dicts(
# #                 cursor,
# #                 cursor.fetchall(),
# #             )

# #             cursor.execute(
# #                 f"""
# #                 SELECT *
# #                 FROM {ATTACHMENT_TABLE}
# #                 WHERE ProspectId = ?
# #                 """,
# #                 prospect_id,
# #             )

# #             attachments = _rows_to_dicts(
# #                 cursor,
# #                 cursor.fetchall(),
# #             )

# #             return {
# #                 "REGISTRATION": registration,
# #                 "PROSPECT": prospect,
# #                 "CONTACTS": contacts,
# #                 "ATTACHMENTS": attachments,
# #             }

# #         finally:
# #             cursor.close()


# # def build_d365_vendor_payload(
# #     prospect_id: str,
# # ) -> tuple[dict[str, Any], str | None]:
# #     prospect_id = _text(
# #         prospect_id
# #     ).upper()

# #     if not prospect_id:
# #         raise D365VendorCreationError(
# #             "ProspectId is required"
# #         )

# #     source = _fetch_vendor_source_data(
# #         prospect_id
# #     )

# #     registration = source["REGISTRATION"]
# #     prospect = source["PROSPECT"]
# #     contacts = source["CONTACTS"]
# #     attachments = source["ATTACHMENTS"]

# #     existing_vendor_account = _text(
# #         _value(
# #             prospect,
# #             "VendorAccount",
# #             "VendorId",
# #         )
# #     ) or None

# #     company_name = (
# #         _text(
# #             _value(
# #                 registration,
# #                 "CompanyName",
# #             )
# #         )
# #         or _text(
# #             _value(
# #                 prospect,
# #                 "Name",
# #                 "VendorName",
# #             )
# #         )
# #     )

# #     vendor_group = (
# #         _text(
# #             _value(
# #                 prospect,
# #                 "VendGroup",
# #                 "VendorGroup",
# #             )
# #         )
# #         or _text(
# #             _value(
# #                 registration,
# #                 "SupplierType",
# #             )
# #         )
# #         or D365_DEFAULT_VENDOR_GROUP
# #     )

# #     country = _country_code(
# #         _value(
# #             registration,
# #             "Country",
# #             "CountryOfOrigin",
# #             default=D365_DEFAULT_COUNTRY,
# #         )
# #     )

# #     payment_terms = (
# #         _text(
# #             _value(
# #                 registration,
# #                 "PaymentTerms",
# #             )
# #         )
# #         or D365_DEFAULT_PAYMENT_TERMS
# #     )

# #     payment_mode = (
# #         _text(
# #             _value(
# #                 registration,
# #                 "PaymentMode",
# #             )
# #         )
# #         or D365_DEFAULT_PAYMENT_MODE
# #     )

# #     registration_type = _text(
# #         _value(
# #             registration,
# #             "RegistrationType",
# #         )
# #     )

# #     registration_number = _text(
# #         _value(
# #             registration,
# #             "RegistrationNumber",
# #         )
# #     )

# #     gst_numbers: list[dict[str, Any]] = []

# #     if registration_number and (
# #         not registration_type
# #         or "GST" in registration_type.upper()
# #     ):
# #         gst_numbers.append(
# #             {
# #                 "GSTNumber": registration_number,
# #                 "name": (
# #                     registration_type
# #                     or "Primary GST"
# #                 ),
# #                 "IsPrimary": True,
# #             }
# #         )

# #     address_values = {
# #         "street": _text(
# #             _value(registration, "Street")
# #         ),
# #         "city": _text(
# #             _value(registration, "City")
# #         ),
# #         "district": _text(
# #             _value(registration, "District")
# #         ),
# #         "state": _text(
# #             _value(registration, "State")
# #         ),
# #         "zipCode": _text(
# #             _value(
# #                 registration,
# #                 "ZipCode",
# #                 "PostalCode",
# #             )
# #         ),
# #     }

# #     addresses: list[dict[str, Any]] = []

# #     if any(address_values.values()):
# #         addresses.append(
# #             {
# #                 "Name": "Primary Address",
# #                 "street": address_values["street"],
# #                 "streetNumber": "",
# #                 "city": address_values["city"],
# #                 "district": address_values["district"],
# #                 "state": address_values["state"],
# #                 "country": country,
# #                 "zipCode": address_values["zipCode"],
# #                 "BuildingCompliment": "",
# #                 "PostBox": "",
# #                 "County": "",
# #                 "IsPrimary": True,
# #             }
# #         )

# #     contacts = sorted(
# #         contacts,
# #         key=lambda item: (
# #             not _as_bool(
# #                 _value(item, "IsPrimary")
# #             ),
# #             int(
# #                 _value(
# #                     item,
# #                     "ContactId",
# #                     default=0,
# #                 )
# #                 or 0
# #             ),
# #         ),
# #     )

# #     contact_payload: list[dict[str, Any]] = []

# #     for contact in contacts:
# #         contact_payload.append(
# #             {
# #                 "Name": _text(
# #                     _value(
# #                         contact,
# #                         "ContactName",
# #                         "Name",
# #                     ),
# #                     "Vendor Contact",
# #                 ),
# #                 "email": _text(
# #                     _value(
# #                         contact,
# #                         "EmailAddress",
# #                         "Email",
# #                     )
# #                 ),
# #                 "Prevemail": "",
# #                 "phone": _text(
# #                     _value(
# #                         contact,
# #                         "MobileNumber",
# #                         "Phone",
# #                     )
# #                 ),
# #                 "Prevphone": "",
# #                 "IsPrimary": _as_bool(
# #                     _value(contact, "IsPrimary")
# #                 ),
# #             }
# #         )

# #     if (
# #         contact_payload
# #         and not any(
# #             item["IsPrimary"]
# #             for item in contact_payload
# #         )
# #     ):
# #         contact_payload[0]["IsPrimary"] = True

# #     if not contact_payload:
# #         prospect_email = _text(
# #             _value(
# #                 prospect,
# #                 "Email",
# #                 "EmailAddress",
# #             )
# #         )

# #         if prospect_email:
# #             contact_payload.append(
# #                 {
# #                     "Name": company_name,
# #                     "email": prospect_email,
# #                     "Prevemail": "",
# #                     "phone": "",
# #                     "Prevphone": "",
# #                     "IsPrimary": True,
# #                 }
# #             )

# #     attachment_payload: list[
# #         dict[str, Any]
# #     ] = []

# #     for attachment in attachments:
# #         file_name = _text(
# #             _value(
# #                 attachment,
# #                 "FileName",
# #             )
# #         )

# #         content_type = _text(
# #             _value(
# #                 attachment,
# #                 "ContentType",
# #             )
# #         )

# #         explicit_extension = _text(
# #             _value(
# #                 attachment,
# #                 "FileExtension",
# #                 "Extension",
# #             )
# #         )

# #         raw_content = _value(
# #             attachment,
# #             "FileBytes",
# #             "FileContent",
# #             "Content",
# #         )

# #         content = _base64_content(
# #             raw_content
# #         )

# #         if not file_name or not content:
# #             continue

# #         extension = _file_extension(
# #             file_name=file_name,
# #             content_type=content_type,
# #             explicit_extension=explicit_extension,
# #         )

# #         attachment_payload.append(
# #             {
# #                 "fileName": (
# #                     Path(file_name).stem
# #                     or file_name
# #                 ),
# #                 "extension": extension,
# #                 "content": content,
# #             }
# #         )

# #     contract = {
# #         "ProspectId": prospect_id,
# #         "Vendorid": (
# #             existing_vendor_account or ""
# #         ),
# #         "vendorName": company_name,
# #         "searchName": company_name,
# #         "vendorGroup": vendor_group,
# #         "currency": D365_DEFAULT_CURRENCY,
# #         "country": country,
# #         "language": D365_DEFAULT_LANGUAGE,
# #         "panNumber": _text(
# #             _value(
# #                 registration,
# #                 "PANNumber",
# #             )
# #         ),
# #         "paymentTerms": payment_terms,
# #         "paymentMode": payment_mode,
# #         "taxGroup": D365_DEFAULT_TAX_GROUP,
# #         "gstNumber": gst_numbers,
# #         "address": addresses,
# #         "contact": contact_payload,
# #         "attachments": attachment_payload,
# #     }

# #     mandatory_fields = {
# #         "vendorName": contract["vendorName"],
# #         "vendorGroup": contract["vendorGroup"],
# #         "currency": contract["currency"],
# #         "country": contract["country"],
# #     }

# #     missing = [
# #         field_name
# #         for field_name, field_value
# #         in mandatory_fields.items()
# #         if not _text(field_value)
# #     ]

# #     if missing:
# #         raise D365VendorCreationError(
# #             "Missing mandatory D365 vendor fields: "
# #             + ", ".join(missing)
# #         )

# #     return {
# #         "_contract": contract
# #     }, existing_vendor_account


# # def _save_vendor_account(
# #     prospect_id: str,
# #     vendor_account: str,
# # ) -> None:
# #     with get_secondary_connection() as connection:
# #         cursor = connection.cursor()

# #         try:
# #             cursor.execute(
# #                 "SET XACT_ABORT ON;"
# #             )

# #             cursor.execute(
# #                 f"""
# #                 UPDATE {PROSPECT_TABLE}
# #                 SET VendorAccount = ?
# #                 WHERE ProspectId = ?
# #                 """,
# #                 (
# #                     vendor_account,
# #                     prospect_id,
# #                 ),
# #             )

# #             if cursor.rowcount != 1:
# #                 raise D365VendorCreationError(
# #                     "VendorAccount could not be saved because "
# #                     "d365_VendorProspect was not found for "
# #                     f"ProspectId={prospect_id}"
# #                 )

# #             cursor.execute(
# #                 f"""
# #                 UPDATE {REGISTRATION_TABLE}
# #                 SET
# #                     Status = N'VENDOR_CREATED',
# #                     IsDraft = 1,
# #                     ModifiedOn = SYSUTCDATETIME()
# #                 WHERE ProspectId = ?
# #                 """,
# #                 prospect_id,
# #             )

# #             if cursor.rowcount != 1:
# #                 raise D365VendorCreationError(
# #                     "VendorRegistration could not be updated "
# #                     "to VENDOR_CREATED"
# #                 )

# #             connection.commit()

# #         except Exception:
# #             connection.rollback()
# #             raise

# #         finally:
# #             cursor.close()

# # def create_vendor_in_d365(
# #     prospect_id: str,
# # ) -> dict[str, Any]:
# #     """
# #     Triggered only after all approval assignments are APPROVED.

# #     Approval stays APPROVED if the D365 call fails.
# #     Registration is changed to VENDOR_CREATED only after D365
# #     returns a VendorAccount.
# #     """

# #     prospect_id = _text(
# #         prospect_id
# #     ).upper()

# #     try:
# #         payload, existing_vendor_account = (
# #             build_d365_vendor_payload(
# #                 prospect_id
# #             )
# #         )

# #         # Already created in D365 earlier.
# #         if existing_vendor_account:
# #             _save_vendor_account(
# #                 prospect_id,
# #                 existing_vendor_account,
# #             )

# #             return {
# #                 "TRIGGERED": False,
# #                 "SUCCESS": True,
# #                 "SKIPPED": True,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": existing_vendor_account,
# #                 "HTTP_STATUS": None,
# #                 "REQUEST_URL": None,
# #                 "CONTENT_TYPE": None,
# #                 "ERROR": None,
# #             }

# #         token = get_d365_token()

# #         logger.info(
# #             "[D365 VENDOR CREATION] ProspectId={} URL={!r}",
# #             prospect_id,
# #             D365_VENDOR_CREATION_URL,
# #         )

# #         response = requests.post(
# #             url=D365_VENDOR_CREATION_URL,
# #             headers={
# #                 "Authorization": f"Bearer {token}",
# #                 "Content-Type": "application/json",
# #                 "Accept": "application/json",
# #             },
# #             json=payload,
# #             timeout=D365_REQUEST_TIMEOUT_SECONDS,
# #             verify=D365_VERIFY_SSL,
# #         )

# #         logger.info(
# #             "[D365 VENDOR CREATION RESPONSE] "
# #             "ProspectId={} HTTP_STATUS={} REQUEST_URL={!r} "
# #             "CONTENT_TYPE={!r}",
# #             prospect_id,
# #             response.status_code,
# #             response.request.url,
# #             response.headers.get("Content-Type"),
# #         )

# #         result = _parse_d365_response(
# #             response
# #         )

# #         if response.status_code >= 400:
# #             return {
# #                 "TRIGGERED": True,
# #                 "SUCCESS": False,
# #                 "SKIPPED": False,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": None,
# #                 "HTTP_STATUS": response.status_code,
# #                 "REQUEST_URL": response.request.url,
# #                 "CONTENT_TYPE": response.headers.get(
# #                     "Content-Type"
# #                 ),
# #                 "ERROR": (
# #                     _text(
# #                         result.get(
# #                             "ErrorMessage"
# #                         )
# #                     )
# #                     or response.text[:2000]
# #                     or (
# #                         "D365 returned HTTP "
# #                         f"{response.status_code}"
# #                     )
# #                 ),
# #             }

# #         success_value = result.get(
# #             "Success",
# #             result.get("success"),
# #         )

# #         success = (
# #             success_value is True
# #             or _text(success_value).lower()
# #             == "true"
# #         )

# #         if not success:
# #             return {
# #                 "TRIGGERED": True,
# #                 "SUCCESS": False,
# #                 "SKIPPED": False,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": None,
# #                 "HTTP_STATUS": response.status_code,
# #                 "REQUEST_URL": response.request.url,
# #                 "CONTENT_TYPE": response.headers.get(
# #                     "Content-Type"
# #                 ),
# #                 "ERROR": (
# #                     _text(
# #                         result.get(
# #                             "ErrorMessage"
# #                         )
# #                     )
# #                     or (
# #                         "D365 vendor creation returned "
# #                         "Success=false"
# #                     )
# #                 ),
# #             }

# #         vendor_account = (
# #             _text(
# #                 result.get("VendorAccount")
# #             )
# #             or _text(
# #                 result.get("vendorAccount")
# #             )
# #             or _text(
# #                 result.get("VendorId")
# #             )
# #             or _text(
# #                 result.get("vendorId")
# #             )
# #             or _text(
# #                 result.get("VendAccount")
# #             )
# #             or _text(
# #                 result.get("vendAccount")
# #             )
# #         )

# #         if not vendor_account:
# #             return {
# #                 "TRIGGERED": True,
# #                 "SUCCESS": False,
# #                 "SKIPPED": False,
# #                 "PROSPECT_ID": prospect_id,
# #                 "VENDOR_ACCOUNT": None,
# #                 "HTTP_STATUS": response.status_code,
# #                 "REQUEST_URL": response.request.url,
# #                 "CONTENT_TYPE": response.headers.get(
# #                     "Content-Type"
# #                 ),
# #                 "ERROR": (
# #                     "D365 returned Success=true but "
# #                     "VendorAccount is empty"
# #                 ),
# #             }

# #         _save_vendor_account(
# #             prospect_id,
# #             vendor_account,
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": True,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": vendor_account,
# #             "HTTP_STATUS": response.status_code,
# #             "REQUEST_URL": response.request.url,
# #             "CONTENT_TYPE": response.headers.get(
# #                 "Content-Type"
# #             ),
# #             "ERROR": None,
# #         }

# #     except requests.Timeout:
# #         logger.exception(
# #             "[D365 VENDOR CREATION] Timeout for "
# #             f"ProspectId={prospect_id}"
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": False,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": None,
# #             "HTTP_STATUS": None,
# #             "REQUEST_URL": D365_VENDOR_CREATION_URL,
# #             "CONTENT_TYPE": None,
# #             "ERROR": "D365 request timed out",
# #         }

# #     except requests.RequestException as exc:
# #         logger.exception(
# #             "[D365 VENDOR CREATION] HTTP failure for "
# #             f"ProspectId={prospect_id}"
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": False,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": None,
# #             "HTTP_STATUS": (
# #                 exc.response.status_code
# #                 if exc.response is not None
# #                 else None
# #             ),
# #             "REQUEST_URL": (
# #                 exc.request.url
# #                 if exc.request is not None
# #                 else D365_VENDOR_CREATION_URL
# #             ),
# #             "CONTENT_TYPE": (
# #                 exc.response.headers.get(
# #                     "Content-Type"
# #                 )
# #                 if exc.response is not None
# #                 else None
# #             ),
# #             "ERROR": (
# #                 exc.response.text[:2000]
# #                 if exc.response is not None
# #                 else str(exc)
# #             ),
# #         }

# #     except Exception as exc:
# #         logger.exception(
# #             "[D365 VENDOR CREATION] Failed for "
# #             f"ProspectId={prospect_id}"
# #         )

# #         return {
# #             "TRIGGERED": True,
# #             "SUCCESS": False,
# #             "SKIPPED": False,
# #             "PROSPECT_ID": prospect_id,
# #             "VENDOR_ACCOUNT": None,
# #             "HTTP_STATUS": None,
# #             "REQUEST_URL": D365_VENDOR_CREATION_URL,
# #             "CONTENT_TYPE": None,
# #             "ERROR": str(exc),
# #         }