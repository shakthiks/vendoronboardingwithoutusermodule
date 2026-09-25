from __future__ import annotations

import traceback

import pyodbc
from fastapi import status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.base import get_connection
from app.services.email_service import send_email
from app.services.createprospect.invited_registration_service import (
    create_invited_registration,
)


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_SCHEMA = settings.DB_SCHEMA

PROSPECT_TABLE = (
    f"[{DB_SCHEMA}].[d365_VendorProspect]"
)


# ============================================================
# HELPERS
# ============================================================

def _normalize_email(
    email: str | None,
) -> str:
    value = str(
        email or ""
    ).strip().lower()

    if not value:
        raise ValueError(
            "Email is required"
        )

    return value


def _find_existing_prospect_by_email(
    cursor,
    email: str,
):
    normalized_email = _normalize_email(
        email
    )

    cursor.execute(
        f"""
        SELECT TOP 1
            ProspectSeq,
            ProspectId,
            Name,
            Email,
            VendGroup
        FROM {PROSPECT_TABLE}
        WHERE LOWER(LTRIM(RTRIM(Email))) = ?
        """,
        normalized_email,
    )

    return cursor.fetchone()


# ============================================================
# SEND INVITATION
# ============================================================

def send_invitation(payload):

    normalized_email = _normalize_email(
        payload.Email
    )

    # ========================================================
    # CLOUD DATABASE
    # d365_VendorProspect is stored in Cloud
    # ========================================================

    with get_connection() as connection:
        cursor = connection.cursor()

        try:
            # ------------------------------------------------
            # 1. Check duplicate email first
            # ------------------------------------------------

            existing_row = _find_existing_prospect_by_email(
                cursor,
                normalized_email,
            )

            if existing_row:
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "status": False,
                        "message": (
                            "This email is already used "
                            "for another prospect."
                        ),
                        "ExistingProspectSeq": existing_row[0],
                        "ExistingProspectId": existing_row[1],
                        "ExistingCompanyName": existing_row[2],
                        "ExistingEmail": existing_row[3],
                    },
                )

            # ------------------------------------------------
            # 2. Insert new prospect
            # ------------------------------------------------

            cursor.execute(
                f"""
                INSERT INTO {PROSPECT_TABLE}
                (
                    Name,
                    SupplierCategory,
                    Email,
                    VendGroup
                )
                OUTPUT
                    INSERTED.ProspectSeq,
                    INSERTED.ProspectId
                VALUES
                (
                    ?, ?, ?, ?
                )
                """,
                (
                    payload.CompanyName,
                    payload.SupplierCategory,
                    normalized_email,
                    payload.VendGroup,
                ),
            )

            row = cursor.fetchone()

            if not row:
                raise Exception(
                    "Failed to create Prospect in Cloud DB"
                )

            prospect_seq = row[0]
            prospect_id = row[1]

            print(
                f"Cloud ProspectSeq : {prospect_seq}"
            )

            print(
                f"Cloud ProspectId  : {prospect_id}"
            )

            connection.commit()

        except pyodbc.IntegrityError as e:
            connection.rollback()

            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "status": False,
                    "message": (
                        "This email is already used "
                        "for another prospect."
                    ),
                    "error": str(e),
                },
            )

        except Exception as e:
            print("\n" + "=" * 70)
            print(
                "EXCEPTION OCCURRED WHILE CREATING PROSPECT"
            )
            traceback.print_exc()
            print("=" * 70)

            try:
                connection.rollback()
                print("CLOUD ROLLBACK SUCCESS")

            except Exception:
                print("CLOUD ROLLBACK FAILED")

            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": False,
                    "message": str(e),
                },
            )

        finally:
            cursor.close()

            print(
                "\nDATABASE CONNECTION CLOSED"
            )

    try:
        # ------------------------------------------------
        # 3. Create vendor registration after prospect insert
        # ------------------------------------------------

        print(
            "\nCreating Vendor Registration..."
        )

        registration_result = create_invited_registration(
            prospect_id=prospect_id,
            changed_by="INVITATION_EMAIL_SERVICE",
        )

        print(
            "REGISTRATION CREATED SUCCESSFULLY"
        )

        # ------------------------------------------------
        # 4. Send invitation email
        # ------------------------------------------------

        link = settings.ONBOARDING_FRONTEND_URL

        print(
            "\nSending Invitation Email..."
        )

        send_email(
            normalized_email,
            "Invitation to Join HIQ Vendor Portal",
            f"""
            <div style="font-family:Arial;padding:20px">

                <h2>Welcome to HIQ Vendor Onboarding</h2>

                <p>
                    Dear <b>{payload.CompanyName}</b>,
                </p>

                <p>
                    Greetings from HIQ.
                </p>

                <p>
                    You have been invited to register on the
                    HIQ Vendor Portal.
                </p>

                <p>
                    Please click the button below to continue
                    your registration.
                </p>

                <a
                    href="{link}"
                    style="
                        background:#1F3864;
                        color:white;
                        padding:10px 20px;
                        text-decoration:none;
                        border-radius:5px;
                        display:inline-block
                    "
                >
                    Accept Invitation
                </a>

                <br>

                <p>
                    Please ensure the evaluation is completed
                    accurately and submitted within the specified
                    timeline. Delayed evaluations may impact supplier
                    performance monitoring and procurement decisions.
                </p>

                <p>
                    Regards,<br>
                    <strong>HIQ Team</strong>
                </p>

            </div>
            """,
        )

        print(
            "EMAIL SENT SUCCESSFULLY"
        )

        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "status": True,
                "message": (
                    "Prospect created successfully. "
                    "Invitation email sent."
                ),
                "ProspectId": prospect_id,
                "ProspectSeq": prospect_seq,
                "Registration": registration_result,
            },
        )

    except Exception as e:
        print("\n" + "=" * 70)
        print(
            "EXCEPTION OCCURRED AFTER PROSPECT CREATION"
        )
        traceback.print_exc()
        print("=" * 70)

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": False,
                "message": str(e),
                "ProspectId": prospect_id,
            },
        )