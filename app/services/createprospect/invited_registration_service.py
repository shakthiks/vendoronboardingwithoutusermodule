from __future__ import annotations

from typing import Any, Iterable

import pyodbc

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_secondary_connection,
)


# ============================================================
# DATABASE SETTINGS
# ============================================================

PRIMARY_SCHEMA = settings.DB_SCHEMA

SECONDARY_SCHEMA = PRIMARY_SCHEMA

# Prospect master is in the Cloud database.
SECONDARY_PROSPECT_TABLE = (
    f"[{SECONDARY_SCHEMA}].[d365_VendorProspect]"
)

# Registration used by this active workflow is in the Cloud database.
PRIMARY_REGISTRATION_TABLE = (
    f"[{PRIMARY_SCHEMA}].[HIQ_VendorRegistration]"
)

SECONDARY_REGISTRATION_TABLE = (
    f"[{SECONDARY_SCHEMA}].[HIQ_VendorRegistration]"
)

PRIMARY_STATUS_HISTORY_TABLE = (
    f"[{PRIMARY_SCHEMA}].[HIQ_VendorStatusHistory]"
)

SECONDARY_STATUS_HISTORY_TABLE = (
    f"[{SECONDARY_SCHEMA}].[HIQ_VendorStatusHistory]"
)


VALID_WORKFLOW_STATUSES = {
    "INVITED",
    "DRAFT",
    "TO_EVALUATE",
    "IN_APPROVAL",
    "RETURNED",
    "RESUBMITTED",
    "APPROVED",
    "REJECTED",
    "VENDOR_CREATED",
}


# ============================================================
# COMMON HELPERS
# ============================================================

def _execute(
    cursor: pyodbc.Cursor,
    query: str,
    parameters: Iterable[Any] = (),
) -> pyodbc.Cursor:
    """
    Execute one parameterized SQL statement.
    """

    values = tuple(parameters)

    if values:
        return cursor.execute(
            query,
            *values,
        )

    return cursor.execute(query)


def _clean_text(
    value: Any,
) -> str | None:
    """
    Trim a text value and convert empty text to None.
    """

    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _fetch_one(
    cursor: pyodbc.Cursor,
) -> dict[str, Any]:
    """
    Convert the current cursor row into a dictionary.
    """

    row = cursor.fetchone()

    if row is None:
        return {}

    if not cursor.description:
        return {}

    columns = [
        description[0]
        for description in cursor.description
    ]

    return dict(
        zip(
            columns,
            row,
        )
    )


def _normalize_prospect_id(
    prospect_id: str,
) -> str:
    """
    Validate and normalize ProspectId.
    """

    value = str(
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


def _normalize_workflow_status(
    value: Any,
) -> str:
    """
    Validate one vendor workflow status.
    """

    status_value = str(
        value or ""
    ).strip().upper()

    if status_value not in VALID_WORKFLOW_STATUSES:
        raise ValueError(
            "Invalid vendor workflow status: "
            f"{status_value or value}"
        )

    return status_value


# ============================================================
# PROSPECT
# ============================================================

def _get_prospect_from_secondary(
    secondary_cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any]:
    """
    Read the prospect from the Cloud database table:

        <cloud schema>.d365_VendorProspect
    """

    normalized_prospect_id = (
        _normalize_prospect_id(
            prospect_id
        )
    )

    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            VendorAccount,
            Name,
            Email,
            VendGroup
        FROM {SECONDARY_PROSPECT_TABLE}
        WHERE ProspectId = ?
        """,
        (
            normalized_prospect_id,
        ),
    )

    prospect = _fetch_one(
        secondary_cursor
    )

    if not prospect:
        raise LookupError(
            f"PROSPECT_ID {normalized_prospect_id} "
            "was not found in the secondary database table "
            f"{SECONDARY_PROSPECT_TABLE}"
        )

    return prospect


# ============================================================
# PRIMARY REGISTRATION
# ============================================================

def _get_or_create_registration(
    primary_cursor: pyodbc.Cursor,
    prospect: dict[str, Any],
) -> tuple[dict[str, Any], bool]:

    prospect_id = _normalize_prospect_id(
        str(prospect["ProspectId"])
    )

    source_prospect_seq = int(
        prospect["ProspectSeq"]
    )

    company_name = (
        _clean_text(
            prospect.get("Name")
        ) or ""
    )

    print("=" * 80)
    print("PRIMARY REGISTRATION")
    print("ProspectId :", prospect_id)
    print("ProspectSeq:", source_prospect_seq)
    print("Company    :", company_name)

    # Check existing ProspectId
    _execute(
        primary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {PRIMARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    registration = _fetch_one(primary_cursor)

    print("Existing Registration:", registration)

    if registration:
        print("Registration already exists.")
        return registration, False

    # Check existing ProspectSeq
    _execute(
        primary_cursor,
        f"""
        SELECT ProspectId
        FROM {PRIMARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectSeq = ?
        """,
        (source_prospect_seq,),
    )

    existing_sequence = primary_cursor.fetchone()

    print("Existing Sequence:", existing_sequence)

    if existing_sequence:
        existing_id = str(existing_sequence[0]).strip().upper()

        if existing_id != prospect_id:
            raise RuntimeError(
                "Prospect sequence conflict. "
                f"ProspectSeq={source_prospect_seq}, "
                f"ExistingProspectId={existing_id}, "
                f"RequestedProspectId={prospect_id}"
            )

    identity_insert_enabled = False

    try:
        print("Turning IDENTITY_INSERT ON")

        _execute(
            primary_cursor,
            f"""
            SET IDENTITY_INSERT
            {PRIMARY_REGISTRATION_TABLE} ON
            """,
        )

        identity_insert_enabled = True

        print("IDENTITY_INSERT ON")

        print("Executing INSERT...")

        _execute(
            primary_cursor,
            f"""
            INSERT INTO {PRIMARY_REGISTRATION_TABLE}
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
                N'INVITED',
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

        print("INSERT SUCCESS")

    finally:
        if identity_insert_enabled:
            print("Turning IDENTITY_INSERT OFF")

            _execute(
                primary_cursor,
                f"""
                SET IDENTITY_INSERT
                {PRIMARY_REGISTRATION_TABLE} OFF
                """,
            )

    print("Fetching inserted row...")

    _execute(
        primary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {PRIMARY_REGISTRATION_TABLE}
        WHERE ProspectSeq = ?
        """,
        (source_prospect_seq,),
    )

    registration = _fetch_one(primary_cursor)

    print("Fetched Registration:", registration)

    if not registration:
        raise RuntimeError(
            "Failed to create the primary vendor registration"
        )

    generated_prospect_id = str(
        registration["ProspectId"]
    ).strip().upper()

    print("Generated ProspectId:", generated_prospect_id)
    print("Expected  ProspectId:", prospect_id)

    if generated_prospect_id != prospect_id:
        raise RuntimeError(
            "Primary registration ProspectId mismatch. "
            f"Requested={prospect_id}, "
            f"Generated={generated_prospect_id}"
        )

    print("Primary registration created successfully.")
    print("=" * 80)

    return registration, True

# ============================================================
# SECONDARY REGISTRATION
# ============================================================

def _ensure_secondary_registration_exists_or_match(
    primary_cursor: pyodbc.Cursor,
    secondary_cursor: pyodbc.Cursor,
    prospect_id: str,
) -> str:
    """
    Ensure the same registration row exists in the secondary database.

    Rules:
    - Primary registration must exist.
    - If secondary registration is missing, insert it.
    - If both exist, their ProspectSeq and Status must match.
    - Existing secondary workflow status is never silently overwritten.

    Returns:
        normalized current workflow status
    """

    normalized_prospect_id = (
        _normalize_prospect_id(
            prospect_id
        )
    )

    # Read primary registration.
    _execute(
        primary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            CreatedOn,
            ModifiedOn
        FROM {PRIMARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (
            normalized_prospect_id,
        ),
    )

    primary_registration = _fetch_one(
        primary_cursor
    )

    if not primary_registration:
        raise LookupError(
            "Primary registration was not found for "
            f"{normalized_prospect_id}"
        )

    primary_status = _normalize_workflow_status(
        primary_registration.get("Status")
    )

    primary_prospect_seq = int(
        primary_registration["ProspectSeq"]
    )

    # Read secondary registration.
    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft
        FROM {SECONDARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (
            normalized_prospect_id,
        ),
    )

    secondary_registration = _fetch_one(
        secondary_cursor
    )

    if secondary_registration:
        secondary_status = _normalize_workflow_status(
            secondary_registration.get("Status")
        )

        secondary_prospect_seq = int(
            secondary_registration["ProspectSeq"]
        )

        if secondary_prospect_seq != primary_prospect_seq:
            raise RuntimeError(
                "Primary and secondary ProspectSeq values "
                "do not match. "
                f"Primary={primary_prospect_seq}, "
                f"Secondary={secondary_prospect_seq}"
            )

        if secondary_status != primary_status:
            raise RuntimeError(
                "Primary and secondary registration statuses "
                "do not match. "
                f"Primary={primary_status}, "
                f"Secondary={secondary_status}"
            )

        return primary_status

    # Ensure the same sequence is not used by another
    # ProspectId in the secondary database.
    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectId
        FROM {SECONDARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectSeq = ?
        """,
        (
            primary_prospect_seq,
        ),
    )

    existing_sequence = (
        secondary_cursor.fetchone()
    )

    if existing_sequence:
        existing_id = str(
            existing_sequence[0]
        ).strip().upper()

        if existing_id != normalized_prospect_id:
            raise RuntimeError(
                "Secondary ProspectSeq conflict. "
                f"ProspectSeq={primary_prospect_seq}, "
                f"ExistingProspectId={existing_id}, "
                f"RequestedProspectId={normalized_prospect_id}"
            )

    identity_insert_enabled = False

    try:
        _execute(
            secondary_cursor,
            f"""
            SET IDENTITY_INSERT
            {SECONDARY_REGISTRATION_TABLE} ON
            """,
        )

        identity_insert_enabled = True

        _execute(
            secondary_cursor,
            f"""
            INSERT INTO {SECONDARY_REGISTRATION_TABLE}
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
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                primary_prospect_seq,
                primary_registration.get("CompanyName"),
                primary_status,
                int(
                    primary_registration.get("IsDraft")
                    or 0
                ),
                primary_registration.get("CreatedOn"),
                primary_registration.get("ModifiedOn"),
            ),
        )

    finally:
        if identity_insert_enabled:
            _execute(
                secondary_cursor,
                f"""
                SET IDENTITY_INSERT
                {SECONDARY_REGISTRATION_TABLE} OFF
                """,
            )

    # Verify the generated ProspectId and status.
    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            Status
        FROM {SECONDARY_REGISTRATION_TABLE}
        WHERE ProspectSeq = ?
        """,
        (
            primary_prospect_seq,
        ),
    )

    created_secondary_registration = _fetch_one(
        secondary_cursor
    )

    if not created_secondary_registration:
        raise RuntimeError(
            "Failed to create the secondary vendor registration"
        )

    generated_prospect_id = str(
        created_secondary_registration["ProspectId"]
    ).strip().upper()

    if generated_prospect_id != normalized_prospect_id:
        raise RuntimeError(
            "Secondary registration ProspectId mismatch. "
            f"Requested={normalized_prospect_id}, "
            f"Generated={generated_prospect_id}"
        )

    created_status = _normalize_workflow_status(
        created_secondary_registration.get("Status")
    )

    if created_status != primary_status:
        raise RuntimeError(
            "Secondary registration status mismatch after insert. "
            f"Primary={primary_status}, "
            f"Secondary={created_status}"
        )

    return primary_status


# ============================================================
# STATUS HISTORY
# ============================================================

def _insert_status_history(
    cursor: pyodbc.Cursor,
    table_name: str,
    prospect_id: str,
    previous_status: str | None,
    new_status: str,
    changed_by: str | None,
    remarks: str | None,
) -> None:
    """
    Insert one workflow status history record.
    """

    normalized_prospect_id = (
        _normalize_prospect_id(
            prospect_id
        )
    )

    normalized_new_status = (
        _normalize_workflow_status(
            new_status
        )
    )

    normalized_previous_status = (
        _normalize_workflow_status(
            previous_status
        )
        if previous_status is not None
        else None
    )

    _execute(
        cursor,
        f"""
        INSERT INTO {table_name}
        (
            ProspectId,
            PreviousStatus,
            NewStatus,
            ChangedBy,
            Remarks,
            ChangedAt
        )
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            SYSUTCDATETIME()
        )
        """,
        (
            normalized_prospect_id,
            normalized_previous_status,
            normalized_new_status,
            _clean_text(
                changed_by
            ),
            _clean_text(
                remarks
            ),
        ),
    )


# ============================================================
# CREATE INVITED REGISTRATION
# ============================================================


def create_invited_registration(
    prospect_id: str,
    changed_by: str | None = "INVITATION_EMAIL_SERVICE",
) -> dict[str, Any]:

    normalized_prospect_id = _normalize_prospect_id(
        prospect_id
    )

    with get_connection() as secondary_connection:

        secondary_cursor = secondary_connection.cursor()

        try:

            # Fetch Prospect from Cloud DB
            prospect = _get_prospect_from_secondary(
                secondary_cursor=secondary_cursor,
                prospect_id=normalized_prospect_id,
            )

            # Check whether registration already exists
            _execute(
                secondary_cursor,
                f"""
                SELECT
                    ProspectSeq,
                    ProspectId,
                    CompanyName,
                    Status,
                    IsDraft
                FROM {SECONDARY_REGISTRATION_TABLE}
                WHERE ProspectId = ?
                """,
                (normalized_prospect_id,),
            )

            registration = _fetch_one(secondary_cursor)

            row_created = False

            if registration:
                current_status = registration.get("Status")

            else:

                row_created = True

                _execute(
                    secondary_cursor,
                    f"""
                    SET IDENTITY_INSERT
                    {SECONDARY_REGISTRATION_TABLE} ON
                    """,
                )

                try:

                    _execute(
                        secondary_cursor,
                        f"""
                        INSERT INTO {SECONDARY_REGISTRATION_TABLE}
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
                            int(prospect["ProspectSeq"]),
                            prospect.get("Name") or "",
                        ),
                    )

                finally:

                    _execute(
                        secondary_cursor,
                        f"""
                        SET IDENTITY_INSERT
                        {SECONDARY_REGISTRATION_TABLE} OFF
                        """,
                    )

                _execute(
                    secondary_cursor,
                    f"""
                    SELECT
                        ProspectSeq,
                        ProspectId,
                        CompanyName,
                        Status,
                        IsDraft
                    FROM {SECONDARY_REGISTRATION_TABLE}
                    WHERE ProspectSeq = ?
                    """,
                    (
                        int(prospect["ProspectSeq"]),
                    ),
                )

                registration = _fetch_one(secondary_cursor)

                current_status = "INVITED"

                _insert_status_history(
                    cursor=secondary_cursor,
                    table_name=SECONDARY_STATUS_HISTORY_TABLE,
                    prospect_id=normalized_prospect_id,
                    previous_status=None,
                    new_status="INVITED",
                    changed_by=changed_by,
                    remarks="Vendor invitation registration created",
                )

            secondary_connection.commit()

            return {
                "SUCCESS": True,
                "MESSAGE": (
                    "Vendor invitation registration created successfully"
                    if row_created
                    else "Vendor invitation registration already exists"
                ),
                "PROSPECT_ID": normalized_prospect_id,
                "PROSPECT_SEQ": int(registration["ProspectSeq"]),
                "COMPANY_NAME": registration.get("CompanyName"),
                "EMAIL": prospect.get("Email"),
                "VENDOR_ACCOUNT": prospect.get("VendorAccount"),
                "STATUS": current_status,
                "ISDRAFT": int(registration.get("IsDraft") or 0),
                "ROW_CREATED": row_created,
            }

        except Exception:
            secondary_connection.rollback()
            raise

        finally:
            secondary_cursor.close()





















# def create_invited_registration(
#     prospect_id: str,
#     changed_by: str | None = "INVITATION_EMAIL_SERVICE",
# ) -> dict[str, Any]:
#     """
#     Invitation workflow:

#     1. Fetch prospect from secondary d365_VendorProspect.
#     2. Create INVITED registration in primary when missing.
#     3. Create matching INVITED registration in secondary when missing.
#     4. Insert status history into both databases for a new row.
#     5. Preserve the existing status when registration already exists.
#     """

#     normalized_prospect_id = (
#         _normalize_prospect_id(
#             prospect_id
#         )
#     )

#     with (
#         get_connection() as primary_connection,
#         get_secondary_connection() as secondary_connection,
#     ):
#         primary_cursor = (
#             primary_connection.cursor()
#         )

#         secondary_cursor = (
#             secondary_connection.cursor()
#         )

#         try:
#             # Prospect source is secondary DB only.
#             prospect = _get_prospect_from_secondary(
#                 secondary_cursor=secondary_cursor,
#                 prospect_id=normalized_prospect_id,
#             )

#             # Create or fetch primary registration.
#             registration, row_created = (
#                 _get_or_create_registration(
#                     primary_cursor=primary_cursor,
#                     prospect=prospect,
#                 )
#             )

#             # Create or verify secondary registration.
#             current_status = (
#                 _ensure_secondary_registration_exists_or_match(
#                     primary_cursor=primary_cursor,
#                     secondary_cursor=secondary_cursor,
#                     prospect_id=normalized_prospect_id,
#                 )
#             )

#             # Insert initial history only when primary row is new.
#             if row_created:
#                 _insert_status_history(
#                     cursor=primary_cursor,
#                     table_name=PRIMARY_STATUS_HISTORY_TABLE,
#                     prospect_id=normalized_prospect_id,
#                     previous_status=None,
#                     new_status="INVITED",
#                     changed_by=changed_by,
#                     remarks=(
#                         "Vendor invitation registration created"
#                     ),
#                 )

#                 _insert_status_history(
#                     cursor=secondary_cursor,
#                     table_name=SECONDARY_STATUS_HISTORY_TABLE,
#                     prospect_id=normalized_prospect_id,
#                     previous_status=None,
#                     new_status="INVITED",
#                     changed_by=changed_by,
#                     remarks=(
#                         "Vendor invitation registration created"
#                     ),
#                 )

#             # Both operations succeeded.
#             primary_connection.commit()
#             secondary_connection.commit()

#             return {
#                 "SUCCESS": True,
#                 "MESSAGE": (
#                     "Vendor invitation registration created successfully"
#                     if row_created
#                     else
#                     "Vendor invitation registration already exists"
#                 ),
#                 "PROSPECT_ID": normalized_prospect_id,
#                 "PROSPECT_SEQ": int(
#                     registration["ProspectSeq"]
#                 ),
#                 "COMPANY_NAME": registration.get(
#                     "CompanyName"
#                 ),
#                 "EMAIL": prospect.get(
#                     "Email"
#                 ),
#                 "VENDOR_ACCOUNT": prospect.get(
#                     "VendorAccount"
#                 ),
#                 "STATUS": current_status,
#                 "ISDRAFT": int(
#                     registration.get("IsDraft")
#                     or 0
#                 ),
#                 "ROW_CREATED": row_created,
#             }

#         except Exception:
#             primary_connection.rollback()
#             secondary_connection.rollback()
#             raise

#         finally:
#             primary_cursor.close()
#             secondary_cursor.close()from __future__ import annotations

from typing import Any, Iterable

import pyodbc

from app.core.config import settings
from app.db.base import (
    get_connection,
    get_secondary_connection,
)


# ============================================================
# DATABASE SETTINGS
# ============================================================

PRIMARY_SCHEMA = settings.DB_SCHEMA

SECONDARY_SCHEMA = PRIMARY_SCHEMA

# Prospect master is in the Cloud database.
SECONDARY_PROSPECT_TABLE = (
    f"[{SECONDARY_SCHEMA}].[d365_VendorProspect]"
)

# Registration used by this active workflow is in the Cloud database.
PRIMARY_REGISTRATION_TABLE = (
    f"[{PRIMARY_SCHEMA}].[HIQ_VendorRegistration]"
)

SECONDARY_REGISTRATION_TABLE = (
    f"[{SECONDARY_SCHEMA}].[HIQ_VendorRegistration]"
)

PRIMARY_STATUS_HISTORY_TABLE = (
    f"[{PRIMARY_SCHEMA}].[HIQ_VendorStatusHistory]"
)

SECONDARY_STATUS_HISTORY_TABLE = (
    f"[{SECONDARY_SCHEMA}].[HIQ_VendorStatusHistory]"
)


VALID_WORKFLOW_STATUSES = {
    "INVITED",
    "DRAFT",
    "TO_EVALUATE",
    "IN_APPROVAL",
    "RETURNED",
    "RESUBMITTED",
    "APPROVED",
    "REJECTED",
    "VENDOR_CREATED",
}


# ============================================================
# COMMON HELPERS
# ============================================================

def _execute(
    cursor: pyodbc.Cursor,
    query: str,
    parameters: Iterable[Any] = (),
) -> pyodbc.Cursor:
    """
    Execute one parameterized SQL statement.
    """

    values = tuple(parameters)

    if values:
        return cursor.execute(
            query,
            *values,
        )

    return cursor.execute(query)


def _clean_text(
    value: Any,
) -> str | None:
    """
    Trim a text value and convert empty text to None.
    """

    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _fetch_one(
    cursor: pyodbc.Cursor,
) -> dict[str, Any]:
    """
    Convert the current cursor row into a dictionary.
    """

    row = cursor.fetchone()

    if row is None:
        return {}

    if not cursor.description:
        return {}

    columns = [
        description[0]
        for description in cursor.description
    ]

    return dict(
        zip(
            columns,
            row,
        )
    )


def _normalize_prospect_id(
    prospect_id: str,
) -> str:
    """
    Validate and normalize ProspectId.
    """

    value = str(
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


def _normalize_workflow_status(
    value: Any,
) -> str:
    """
    Validate one vendor workflow status.
    """

    status_value = str(
        value or ""
    ).strip().upper()

    if status_value not in VALID_WORKFLOW_STATUSES:
        raise ValueError(
            "Invalid vendor workflow status: "
            f"{status_value or value}"
        )

    return status_value


# ============================================================
# PROSPECT
# ============================================================

def _get_prospect_from_secondary(
    secondary_cursor: pyodbc.Cursor,
    prospect_id: str,
) -> dict[str, Any]:
    """
    Read the prospect from the Cloud database table:

        <cloud schema>.d365_VendorProspect
    """

    normalized_prospect_id = (
        _normalize_prospect_id(
            prospect_id
        )
    )

    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            VendorAccount,
            Name,
            Email,
            VendGroup
        FROM {SECONDARY_PROSPECT_TABLE}
        WHERE ProspectId = ?
        """,
        (
            normalized_prospect_id,
        ),
    )

    prospect = _fetch_one(
        secondary_cursor
    )

    if not prospect:
        raise LookupError(
            f"PROSPECT_ID {normalized_prospect_id} "
            "was not found in the secondary database table "
            f"{SECONDARY_PROSPECT_TABLE}"
        )

    return prospect


# ============================================================
# PRIMARY REGISTRATION
# ============================================================

def _get_or_create_registration(
    primary_cursor: pyodbc.Cursor,
    prospect: dict[str, Any],
) -> tuple[dict[str, Any], bool]:

    prospect_id = _normalize_prospect_id(
        str(prospect["ProspectId"])
    )

    source_prospect_seq = int(
        prospect["ProspectSeq"]
    )

    company_name = (
        _clean_text(
            prospect.get("Name")
        ) or ""
    )

    print("=" * 80)
    print("PRIMARY REGISTRATION")
    print("ProspectId :", prospect_id)
    print("ProspectSeq:", source_prospect_seq)
    print("Company    :", company_name)

    # Check existing ProspectId
    _execute(
        primary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {PRIMARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (prospect_id,),
    )

    registration = _fetch_one(primary_cursor)

    print("Existing Registration:", registration)

    if registration:
        print("Registration already exists.")
        return registration, False

    # Check existing ProspectSeq
    _execute(
        primary_cursor,
        f"""
        SELECT ProspectId
        FROM {PRIMARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectSeq = ?
        """,
        (source_prospect_seq,),
    )

    existing_sequence = primary_cursor.fetchone()

    print("Existing Sequence:", existing_sequence)

    if existing_sequence:
        existing_id = str(existing_sequence[0]).strip().upper()

        if existing_id != prospect_id:
            raise RuntimeError(
                "Prospect sequence conflict. "
                f"ProspectSeq={source_prospect_seq}, "
                f"ExistingProspectId={existing_id}, "
                f"RequestedProspectId={prospect_id}"
            )

    identity_insert_enabled = False

    try:
        print("Turning IDENTITY_INSERT ON")

        _execute(
            primary_cursor,
            f"""
            SET IDENTITY_INSERT
            {PRIMARY_REGISTRATION_TABLE} ON
            """,
        )

        identity_insert_enabled = True

        print("IDENTITY_INSERT ON")

        print("Executing INSERT...")

        _execute(
            primary_cursor,
            f"""
            INSERT INTO {PRIMARY_REGISTRATION_TABLE}
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
                N'INVITED',
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

        print("INSERT SUCCESS")

    finally:
        if identity_insert_enabled:
            print("Turning IDENTITY_INSERT OFF")

            _execute(
                primary_cursor,
                f"""
                SET IDENTITY_INSERT
                {PRIMARY_REGISTRATION_TABLE} OFF
                """,
            )

    print("Fetching inserted row...")

    _execute(
        primary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            IsAuthorizedDistributor
        FROM {PRIMARY_REGISTRATION_TABLE}
        WHERE ProspectSeq = ?
        """,
        (source_prospect_seq,),
    )

    registration = _fetch_one(primary_cursor)

    print("Fetched Registration:", registration)

    if not registration:
        raise RuntimeError(
            "Failed to create the primary vendor registration"
        )

    generated_prospect_id = str(
        registration["ProspectId"]
    ).strip().upper()

    print("Generated ProspectId:", generated_prospect_id)
    print("Expected  ProspectId:", prospect_id)

    if generated_prospect_id != prospect_id:
        raise RuntimeError(
            "Primary registration ProspectId mismatch. "
            f"Requested={prospect_id}, "
            f"Generated={generated_prospect_id}"
        )

    print("Primary registration created successfully.")
    print("=" * 80)

    return registration, True

# ============================================================
# SECONDARY REGISTRATION
# ============================================================

def _ensure_secondary_registration_exists_or_match(
    primary_cursor: pyodbc.Cursor,
    secondary_cursor: pyodbc.Cursor,
    prospect_id: str,
) -> str:
    """
    Ensure the same registration row exists in the secondary database.

    Rules:
    - Primary registration must exist.
    - If secondary registration is missing, insert it.
    - If both exist, their ProspectSeq and Status must match.
    - Existing secondary workflow status is never silently overwritten.

    Returns:
        normalized current workflow status
    """

    normalized_prospect_id = (
        _normalize_prospect_id(
            prospect_id
        )
    )

    # Read primary registration.
    _execute(
        primary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft,
            CreatedOn,
            ModifiedOn
        FROM {PRIMARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (
            normalized_prospect_id,
        ),
    )

    primary_registration = _fetch_one(
        primary_cursor
    )

    if not primary_registration:
        raise LookupError(
            "Primary registration was not found for "
            f"{normalized_prospect_id}"
        )

    primary_status = _normalize_workflow_status(
        primary_registration.get("Status")
    )

    primary_prospect_seq = int(
        primary_registration["ProspectSeq"]
    )

    # Read secondary registration.
    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            CompanyName,
            Status,
            IsDraft
        FROM {SECONDARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectId = ?
        """,
        (
            normalized_prospect_id,
        ),
    )

    secondary_registration = _fetch_one(
        secondary_cursor
    )

    if secondary_registration:
        secondary_status = _normalize_workflow_status(
            secondary_registration.get("Status")
        )

        secondary_prospect_seq = int(
            secondary_registration["ProspectSeq"]
        )

        if secondary_prospect_seq != primary_prospect_seq:
            raise RuntimeError(
                "Primary and secondary ProspectSeq values "
                "do not match. "
                f"Primary={primary_prospect_seq}, "
                f"Secondary={secondary_prospect_seq}"
            )

        if secondary_status != primary_status:
            raise RuntimeError(
                "Primary and secondary registration statuses "
                "do not match. "
                f"Primary={primary_status}, "
                f"Secondary={secondary_status}"
            )

        return primary_status

    # Ensure the same sequence is not used by another
    # ProspectId in the secondary database.
    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectId
        FROM {SECONDARY_REGISTRATION_TABLE}
        WITH (UPDLOCK, HOLDLOCK)
        WHERE ProspectSeq = ?
        """,
        (
            primary_prospect_seq,
        ),
    )

    existing_sequence = (
        secondary_cursor.fetchone()
    )

    if existing_sequence:
        existing_id = str(
            existing_sequence[0]
        ).strip().upper()

        if existing_id != normalized_prospect_id:
            raise RuntimeError(
                "Secondary ProspectSeq conflict. "
                f"ProspectSeq={primary_prospect_seq}, "
                f"ExistingProspectId={existing_id}, "
                f"RequestedProspectId={normalized_prospect_id}"
            )

    identity_insert_enabled = False

    try:
        _execute(
            secondary_cursor,
            f"""
            SET IDENTITY_INSERT
            {SECONDARY_REGISTRATION_TABLE} ON
            """,
        )

        identity_insert_enabled = True

        _execute(
            secondary_cursor,
            f"""
            INSERT INTO {SECONDARY_REGISTRATION_TABLE}
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
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                primary_prospect_seq,
                primary_registration.get("CompanyName"),
                primary_status,
                int(
                    primary_registration.get("IsDraft")
                    or 0
                ),
                primary_registration.get("CreatedOn"),
                primary_registration.get("ModifiedOn"),
            ),
        )

    finally:
        if identity_insert_enabled:
            _execute(
                secondary_cursor,
                f"""
                SET IDENTITY_INSERT
                {SECONDARY_REGISTRATION_TABLE} OFF
                """,
            )

    # Verify the generated ProspectId and status.
    _execute(
        secondary_cursor,
        f"""
        SELECT
            ProspectSeq,
            ProspectId,
            Status
        FROM {SECONDARY_REGISTRATION_TABLE}
        WHERE ProspectSeq = ?
        """,
        (
            primary_prospect_seq,
        ),
    )

    created_secondary_registration = _fetch_one(
        secondary_cursor
    )

    if not created_secondary_registration:
        raise RuntimeError(
            "Failed to create the secondary vendor registration"
        )

    generated_prospect_id = str(
        created_secondary_registration["ProspectId"]
    ).strip().upper()

    if generated_prospect_id != normalized_prospect_id:
        raise RuntimeError(
            "Secondary registration ProspectId mismatch. "
            f"Requested={normalized_prospect_id}, "
            f"Generated={generated_prospect_id}"
        )

    created_status = _normalize_workflow_status(
        created_secondary_registration.get("Status")
    )

    if created_status != primary_status:
        raise RuntimeError(
            "Secondary registration status mismatch after insert. "
            f"Primary={primary_status}, "
            f"Secondary={created_status}"
        )

    return primary_status


# ============================================================
# STATUS HISTORY
# ============================================================

def _insert_status_history(
    cursor: pyodbc.Cursor,
    table_name: str,
    prospect_id: str,
    previous_status: str | None,
    new_status: str,
    changed_by: str | None,
    remarks: str | None,
) -> None:
    """
    Insert one workflow status history record.
    """

    normalized_prospect_id = (
        _normalize_prospect_id(
            prospect_id
        )
    )

    normalized_new_status = (
        _normalize_workflow_status(
            new_status
        )
    )

    normalized_previous_status = (
        _normalize_workflow_status(
            previous_status
        )
        if previous_status is not None
        else None
    )

    _execute(
        cursor,
        f"""
        INSERT INTO {table_name}
        (
            ProspectId,
            PreviousStatus,
            NewStatus,
            ChangedBy,
            Remarks,
            ChangedAt
        )
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?,
            SYSUTCDATETIME()
        )
        """,
        (
            normalized_prospect_id,
            normalized_previous_status,
            normalized_new_status,
            _clean_text(
                changed_by
            ),
            _clean_text(
                remarks
            ),
        ),
    )


# ============================================================
# CREATE INVITED REGISTRATION
# ============================================================


def create_invited_registration(
    prospect_id: str,
    changed_by: str | None = "INVITATION_EMAIL_SERVICE",
) -> dict[str, Any]:

    normalized_prospect_id = _normalize_prospect_id(
        prospect_id
    )

    with get_connection() as secondary_connection:

        secondary_cursor = secondary_connection.cursor()

        try:

            # Fetch Prospect from Cloud DB
            prospect = _get_prospect_from_secondary(
                secondary_cursor=secondary_cursor,
                prospect_id=normalized_prospect_id,
            )

            # Check whether registration already exists
            _execute(
                secondary_cursor,
                f"""
                SELECT
                    ProspectSeq,
                    ProspectId,
                    CompanyName,
                    Status,
                    IsDraft
                FROM {SECONDARY_REGISTRATION_TABLE}
                WHERE ProspectId = ?
                """,
                (normalized_prospect_id,),
            )

            registration = _fetch_one(secondary_cursor)

            row_created = False

            if registration:
                current_status = registration.get("Status")

            else:

                row_created = True

                _execute(
                    secondary_cursor,
                    f"""
                    SET IDENTITY_INSERT
                    {SECONDARY_REGISTRATION_TABLE} ON
                    """,
                )

                try:

                    _execute(
                        secondary_cursor,
                        f"""
                        INSERT INTO {SECONDARY_REGISTRATION_TABLE}
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
                            int(prospect["ProspectSeq"]),
                            prospect.get("Name") or "",
                        ),
                    )

                finally:

                    _execute(
                        secondary_cursor,
                        f"""
                        SET IDENTITY_INSERT
                        {SECONDARY_REGISTRATION_TABLE} OFF
                        """,
                    )

                _execute(
                    secondary_cursor,
                    f"""
                    SELECT
                        ProspectSeq,
                        ProspectId,
                        CompanyName,
                        Status,
                        IsDraft
                    FROM {SECONDARY_REGISTRATION_TABLE}
                    WHERE ProspectSeq = ?
                    """,
                    (
                        int(prospect["ProspectSeq"]),
                    ),
                )

                registration = _fetch_one(secondary_cursor)

                current_status = "INVITED"

                _insert_status_history(
                    cursor=secondary_cursor,
                    table_name=SECONDARY_STATUS_HISTORY_TABLE,
                    prospect_id=normalized_prospect_id,
                    previous_status=None,
                    new_status="INVITED",
                    changed_by=changed_by,
                    remarks="Vendor invitation registration created",
                )

            secondary_connection.commit()

            return {
                "SUCCESS": True,
                "MESSAGE": (
                    "Vendor invitation registration created successfully"
                    if row_created
                    else "Vendor invitation registration already exists"
                ),
                "PROSPECT_ID": normalized_prospect_id,
                "PROSPECT_SEQ": int(registration["ProspectSeq"]),
                "COMPANY_NAME": registration.get("CompanyName"),
                "EMAIL": prospect.get("Email"),
                "VENDOR_ACCOUNT": prospect.get("VendorAccount"),
                "STATUS": current_status,
                "ISDRAFT": int(registration.get("IsDraft") or 0),
                "ROW_CREATED": row_created,
            }

        except Exception:
            secondary_connection.rollback()
            raise

        finally:
            secondary_cursor.close()





















# def create_invited_registration(
#     prospect_id: str,
#     changed_by: str | None = "INVITATION_EMAIL_SERVICE",
# ) -> dict[str, Any]:
#     """
#     Invitation workflow:

#     1. Fetch prospect from secondary d365_VendorProspect.
#     2. Create INVITED registration in primary when missing.
#     3. Create matching INVITED registration in secondary when missing.
#     4. Insert status history into both databases for a new row.
#     5. Preserve the existing status when registration already exists.
#     """

#     normalized_prospect_id = (
#         _normalize_prospect_id(
#             prospect_id
#         )
#     )

#     with (
#         get_connection() as primary_connection,
#         get_secondary_connection() as secondary_connection,
#     ):
#         primary_cursor = (
#             primary_connection.cursor()
#         )

#         secondary_cursor = (
#             secondary_connection.cursor()
#         )

#         try:
#             # Prospect source is secondary DB only.
#             prospect = _get_prospect_from_secondary(
#                 secondary_cursor=secondary_cursor,
#                 prospect_id=normalized_prospect_id,
#             )

#             # Create or fetch primary registration.
#             registration, row_created = (
#                 _get_or_create_registration(
#                     primary_cursor=primary_cursor,
#                     prospect=prospect,
#                 )
#             )

#             # Create or verify secondary registration.
#             current_status = (
#                 _ensure_secondary_registration_exists_or_match(
#                     primary_cursor=primary_cursor,
#                     secondary_cursor=secondary_cursor,
#                     prospect_id=normalized_prospect_id,
#                 )
#             )

#             # Insert initial history only when primary row is new.
#             if row_created:
#                 _insert_status_history(
#                     cursor=primary_cursor,
#                     table_name=PRIMARY_STATUS_HISTORY_TABLE,
#                     prospect_id=normalized_prospect_id,
#                     previous_status=None,
#                     new_status="INVITED",
#                     changed_by=changed_by,
#                     remarks=(
#                         "Vendor invitation registration created"
#                     ),
#                 )

#                 _insert_status_history(
#                     cursor=secondary_cursor,
#                     table_name=SECONDARY_STATUS_HISTORY_TABLE,
#                     prospect_id=normalized_prospect_id,
#                     previous_status=None,
#                     new_status="INVITED",
#                     changed_by=changed_by,
#                     remarks=(
#                         "Vendor invitation registration created"
#                     ),
#                 )

#             # Both operations succeeded.
#             primary_connection.commit()
#             secondary_connection.commit()

#             return {
#                 "SUCCESS": True,
#                 "MESSAGE": (
#                     "Vendor invitation registration created successfully"
#                     if row_created
#                     else
#                     "Vendor invitation registration already exists"
#                 ),
#                 "PROSPECT_ID": normalized_prospect_id,
#                 "PROSPECT_SEQ": int(
#                     registration["ProspectSeq"]
#                 ),
#                 "COMPANY_NAME": registration.get(
#                     "CompanyName"
#                 ),
#                 "EMAIL": prospect.get(
#                     "Email"
#                 ),
#                 "VENDOR_ACCOUNT": prospect.get(
#                     "VendorAccount"
#                 ),
#                 "STATUS": current_status,
#                 "ISDRAFT": int(
#                     registration.get("IsDraft")
#                     or 0
#                 ),
#                 "ROW_CREATED": row_created,
#             }

#         except Exception:
#             primary_connection.rollback()
#             secondary_connection.rollback()
#             raise

#         finally:
#             primary_cursor.close()
#             secondary_cursor.close()