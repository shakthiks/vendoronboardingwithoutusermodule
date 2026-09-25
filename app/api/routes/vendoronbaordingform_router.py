from __future__ import annotations

import json
import logging
from typing import Any

import pyodbc
from fastapi import (
    APIRouter,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import (
    UploadFile as StarletteUploadFile,
)

from app.schemas.vendoronboardingform_schema import (
    ParallelApprovalFinalizeRequest,
    ParallelApprovalFinalizeResponse,
    VendorFetchRequest,
    VendorFetchResponse,
    VendorInvitationRequest,
    VendorInvitationResponse,
    VendorSaveRequest,
    VendorStatusUpdateRequest,
    VendorStatusUpdateResponse,
)
from app.services.form_service import (
    SubmissionValidationError,
    create_invited_registration,
    fetch_vendor_onboarding,
    save_vendor_onboarding,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/vendor-onboarding",
    tags=["Vendor Onboarding"],
)


# ============================================================
# OPENAPI SCHEMA
# ============================================================

def _get_model_json_schema() -> dict[str, Any]:

    # Pydantic V2
    if hasattr(
        VendorSaveRequest,
        "model_json_schema",
    ):
        return (
            VendorSaveRequest
            .model_json_schema()
        )

    # Pydantic V1 fallback
    return VendorSaveRequest.schema()


VENDOR_SAVE_JSON_SCHEMA = (
    _get_model_json_schema()
)


SAVE_REQUEST_BODY_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": VENDOR_SAVE_JSON_SCHEMA,
                "description": (
                    "Use application/json when there "
                    "are no attachment changes."
                ),
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [
                        "FORM_DATA",
                    ],
                    "properties": {
                        "FORM_DATA": {
                            "type": "string",
                            "description": (
                                "Complete VendorSaveRequest "
                                "as a JSON string"
                            ),
                        },
                        "ATTACHMENT_METADATA": {
                            "type": "string",
                            "default": "[]",
                            "description": (
                                "Complete current attachment "
                                "metadata as a JSON array string"
                            ),
                        },
                        "FILES": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "format": "binary",
                            },
                            "description": (
                                "New or replacement PDF, JPG, "
                                "JPEG or PNG files"
                            ),
                        },
                    },
                }
            },
        },
    }
}


# ============================================================
# ERROR RESPONSE
# ============================================================

def _error_response(
    status_code: int,
    message: str,
    prospect_id: str | None = None,
    errors: list[Any] | None = None,
) -> JSONResponse:

    response_content: dict[str, Any] = {
        "SUCCESS": False,
        "MESSAGE": message,
    }

    if prospect_id:
        response_content["PROSPECT_ID"] = (
            prospect_id.strip().upper()
        )

    if errors:
        response_content["ERRORS"] = errors

    return JSONResponse(
        status_code=status_code,
        content=response_content,
    )


def _validation_errors(
    exc: ValidationError,
) -> list[dict[str, Any]]:

    result: list[dict[str, Any]] = []

    for error in exc.errors():
        result.append(
            {
                "FIELD": ".".join(
                    str(value)
                    for value in error.get(
                        "loc",
                        [],
                    )
                ),
                "MESSAGE": error.get(
                    "msg",
                    "Validation error",
                ),
                "TYPE": error.get(
                    "type",
                    "validation_error",
                ),
            }
        )

    return result


def _validate_save_payload(
    value: Any,
) -> VendorSaveRequest:

    # Pydantic V2
    if hasattr(
        VendorSaveRequest,
        "model_validate",
    ):
        return (
            VendorSaveRequest
            .model_validate(value)
        )

    # Pydantic V1 fallback
    return VendorSaveRequest.parse_obj(
        value
    )


def _validate_save_json(
    value: str,
) -> VendorSaveRequest:

    # Pydantic V2
    if hasattr(
        VendorSaveRequest,
        "model_validate_json",
    ):
        return (
            VendorSaveRequest
            .model_validate_json(value)
        )

    # Pydantic V1 fallback
    return VendorSaveRequest.parse_raw(
        value
    )


def _model_to_json(
    payload: VendorSaveRequest,
) -> str:

    # Pydantic V2
    if hasattr(
        payload,
        "model_dump_json",
    ):
        return payload.model_dump_json(by_alias=True)

    # Pydantic V1 fallback
    return payload.json()


# ============================================================
# FETCH API
# ============================================================
@router.post(
    "/fetch",
    summary="Fetch complete vendor onboarding information",
    description=(
        "Fetches registration, contacts, customers, OEMs, "
        "certifications, questionnaire and attachment metadata."
    ),
    status_code=status.HTTP_200_OK,
    response_model=VendorFetchResponse,
    response_model_by_alias=True,
)
def fetch_vendor_information(
    payload: VendorFetchRequest,
):
    try:
        return fetch_vendor_onboarding(
            payload
        )

    except LookupError as exc:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc),
            prospect_id=payload.PROSPECT_ID,
        )

    except ValueError as exc:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
            prospect_id=payload.PROSPECT_ID,
        )

    except pyodbc.Error:
        logger.exception(
            "Database error while fetching vendor onboarding"
        )

        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=(
                "Database error while fetching vendor "
                "onboarding information"
            ),
            prospect_id=payload.PROSPECT_ID,
        )

    except Exception:
        logger.exception(
            "Unexpected error while fetching vendor onboarding"
        )

        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=(
                "Unexpected error while fetching vendor "
                "onboarding information"
            ),
            prospect_id=payload.PROSPECT_ID,
        )
# ============================================================
# SAVE API
# ============================================================


# ============================================================
# INVITATION REGISTRATION API
# ============================================================

# @router.post(
#     "/invitation/register",
#     summary="Create the INVITED registration shell",
#     description=(
#         "Reads the prospect only from the secondary "
#         "d365_VendorProspect table and creates or preserves the "
#         "matching HIQ_VendorRegistration row in both databases."
#     ),
#     response_model=VendorInvitationResponse,
#     status_code=status.HTTP_200_OK,
# )
# def register_vendor_invitation(
#     payload: VendorInvitationRequest,
# ):
#     try:
#         return create_invited_registration(
#             prospect_id=payload.PROSPECT_ID,
#             changed_by=payload.CHANGED_BY,
#         )

#     except LookupError as exc:
#         return _error_response(
#             status_code=status.HTTP_404_NOT_FOUND,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except ValueError as exc:
#         return _error_response(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except pyodbc.IntegrityError:
#         logger.exception(
#             "Database integrity error while creating invitation registration"
#         )
#         return _error_response(
#             status_code=status.HTTP_409_CONFLICT,
#             message="Invitation registration conflicts with existing data",
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except pyodbc.Error:
#         logger.exception(
#             "Database error while creating invitation registration"
#         )
#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message="Database error while creating invitation registration",
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except Exception:
#         logger.exception(
#             "Unexpected error while creating invitation registration"
#         )
#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message="Unexpected error while creating invitation registration",
#             prospect_id=payload.PROSPECT_ID,
#         )



# ============================================================
# ACTIVE SECONDARY-ONLY INVITATION API
# ============================================================

@router.post(
    "/invitation/register",
    summary="Create INVITED registration in secondary database",
    description=(
        "Reads the prospect from the secondary d365_VendorProspect "
        "table and creates one HIQ_VendorRegistration row in the "
        "secondary database with Status=INVITED. Existing workflow "
        "statuses are preserved."
    ),
    response_model=VendorInvitationResponse,
    status_code=status.HTTP_200_OK,
)
def register_vendor_invitation_secondary_only(
    payload: VendorInvitationRequest,
):
    try:
        return create_invited_registration(
            prospect_id=payload.PROSPECT_ID,
            changed_by=payload.CHANGED_BY,
        )

    except LookupError as exc:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc),
            prospect_id=payload.PROSPECT_ID,
        )

    except ValueError as exc:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
            prospect_id=payload.PROSPECT_ID,
        )

    except pyodbc.IntegrityError:
        logger.exception(
            "Database integrity error while creating invitation registration"
        )

        return _error_response(
            status_code=status.HTTP_409_CONFLICT,
            message="Invitation registration conflicts with existing data",
            prospect_id=payload.PROSPECT_ID,
        )

    except pyodbc.Error:
        logger.exception(
            "Database error while creating invitation registration"
        )

        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Database error while creating invitation registration",
            prospect_id=payload.PROSPECT_ID,
        )

    except Exception:
        logger.exception(
            "Unexpected error while creating invitation registration"
        )

        return _error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Unexpected error while creating invitation registration",
            prospect_id=payload.PROSPECT_ID,
        )


# ============================================================
# INTERNAL STATUS UPDATE API
# ============================================================

# @router.post(
#     "/status/update",
#     summary="Update workflow status in both databases",
#     description=(
#         "Internal endpoint for risk, approval and D365 services. "
#         "Updates HIQ_VendorRegistration.Status in both primary and "
#         "secondary databases and records status history."
#     ),
#     response_model=VendorStatusUpdateResponse,
#     status_code=status.HTTP_200_OK,
# )
# def update_vendor_status(
#     payload: VendorStatusUpdateRequest,
# ):
#     try:
#         return update_vendor_workflow_status(
#             prospect_id=payload.PROSPECT_ID,
#             new_status=payload.STATUS.value,
#             changed_by=payload.CHANGED_BY,
#             remarks=payload.REMARKS,
#             expected_current_status=(
#                 payload.EXPECTED_CURRENT_STATUS.value
#                 if payload.EXPECTED_CURRENT_STATUS is not None
#                 else None
#             ),
#         )

#     except LookupError as exc:
#         return _error_response(
#             status_code=status.HTTP_404_NOT_FOUND,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except ValueError as exc:
#         return _error_response(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except pyodbc.Error:
#         logger.exception(
#             "Database error while updating vendor workflow status"
#         )
#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message="Database error while updating vendor workflow status",
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except Exception:
#         logger.exception(
#             "Unexpected error while updating vendor workflow status"
#         )
#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message="Unexpected error while updating vendor workflow status",
#             prospect_id=payload.PROSPECT_ID,
#         )


# ============================================================
# PARALLEL APPROVAL FINALIZATION API
# ============================================================

# @router.post(
#     "/approval/finalize",
#     summary="Finalize a completed parallel approval batch",
#     description=(
#         "Reads approval decisions from the secondary database. "
#         "If any approver is still pending, status remains IN_APPROVAL. "
#         "When all decisions are complete, all approved becomes APPROVED; "
#         "one or more rejected becomes RETURNED."
#     ),
#     response_model=ParallelApprovalFinalizeResponse,
#     status_code=status.HTTP_200_OK,
# )
# def finalize_vendor_parallel_approval(
#     payload: ParallelApprovalFinalizeRequest,
# ):
#     try:
#         return finalize_parallel_approval(
#             prospect_id=payload.PROSPECT_ID,
#             approval_batch_id=payload.APPROVAL_BATCH_ID,
#             changed_by=payload.CHANGED_BY,
#             remarks=payload.REMARKS,
#         )

#     except LookupError as exc:
#         return _error_response(
#             status_code=status.HTTP_404_NOT_FOUND,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except ValueError as exc:
#         return _error_response(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except pyodbc.Error:
#         logger.exception(
#             "Database error while finalizing parallel approval"
#         )
#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message="Database error while finalizing parallel approval",
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except Exception:
#         logger.exception(
#             "Unexpected error while finalizing parallel approval"
#         )
#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message="Unexpected error while finalizing parallel approval",
#             prospect_id=payload.PROSPECT_ID,
#         )


# ============================================================
# SAVE API
# ============================================================

@router.post(
    "/save",
    summary=(
        "Save vendor onboarding information"
    ),
    description="""
This endpoint stores vendor onboarding data only in the secondary database.

This endpoint supports two request types.

1. application/json

Use this when there are no attachment changes.

Existing attachments remain unchanged.

2. multipart/form-data

Use this when adding, replacing or deleting attachments.

Status rules:

SAVE or NEXT:
Status = DRAFT
IsDraft = 0

SUBMIT:
Status = TO_EVALUATE
IsDraft = 1
""",
    status_code=status.HTTP_200_OK,
    response_model=None,
    openapi_extra=SAVE_REQUEST_BODY_SCHEMA,
)
async def save_vendor_information(
    request: Request,
):
    prospect_id: str | None = None
    uploaded_files: list[dict[str, Any]] = []

    try:
        content_type = (
            request.headers
            .get(
                "content-type",
                "",
            )
            .split(
                ";",
                1,
            )[0]
            .strip()
            .lower()
        )

        # ====================================================
        # APPLICATION/JSON
        # ====================================================

        if content_type == "application/json":
            request_json = await request.json()

            payload = _validate_save_payload(
                request_json
            )

            prospect_id = payload.PROSPECT_ID

            return save_vendor_onboarding(
                form_data_json=(
                    _model_to_json(payload)
                ),
                attachment_metadata_json=None,
                uploaded_files=[],
                sync_attachments=False,
            )

        # ====================================================
        # MULTIPART/FORM-DATA
        # ====================================================

        if content_type == "multipart/form-data":
            form = await request.form()

            form_data = form.get(
                "FORM_DATA"
            )

            if (
                not isinstance(
                    form_data,
                    str,
                )
                or not form_data.strip()
            ):
                raise ValueError(
                    "FORM_DATA is required"
                )

            # Validate the JSON and obtain PROSPECT_ID.
            payload = _validate_save_json(
                form_data
            )

            prospect_id = payload.PROSPECT_ID

            attachment_metadata = form.get(
                "ATTACHMENT_METADATA",
                "[]",
            )

            if attachment_metadata is None:
                attachment_metadata = "[]"

            if not isinstance(
                attachment_metadata,
                str,
            ):
                raise ValueError(
                    "ATTACHMENT_METADATA must be "
                    "a JSON string"
                )

            raw_files = form.getlist(
                "FILES"
            )

            for file_item in raw_files:

                # Ignore an empty Swagger/Postman file field.
                if file_item in {
                    None,
                    "",
                }:
                    continue

                if not isinstance(
                    file_item,
                    StarletteUploadFile,
                ):
                    raise ValueError(
                        "FILES must contain uploaded files"
                    )

                try:
                    file_bytes = (
                        await file_item.read()
                    )

                    file_index = len(
                        uploaded_files
                    )

                    uploaded_files.append(
                        {
                            "FILE_INDEX":
                                file_index,
                            "FILE_NAME": (
                                file_item.filename
                                or f"file_{file_index}"
                            ),
                            "CONTENT_TYPE": (
                                file_item.content_type
                                or
                                "application/octet-stream"
                            ),
                            "FILE_BYTES":
                                file_bytes,
                        }
                    )

                finally:
                    await file_item.close()

            return save_vendor_onboarding(
                form_data_json=form_data,
                attachment_metadata_json=(
                    attachment_metadata
                ),
                uploaded_files=uploaded_files,
                sync_attachments=True,
            )

        return _error_response(
            status_code=(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            ),
            message=(
                "Content-Type must be application/json "
                "or multipart/form-data"
            ),
            prospect_id=prospect_id,
        )

    except json.JSONDecodeError as exc:
        return _error_response(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            message="Invalid JSON request",
            prospect_id=prospect_id,
            errors=[str(exc)],
        )

    except ValidationError as exc:
        return _error_response(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            message="Request validation failed",
            prospect_id=prospect_id,
            errors=_validation_errors(exc),
        )

    except SubmissionValidationError as exc:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message="Submission validation failed",
            prospect_id=prospect_id,
            errors=exc.errors,
        )

    except LookupError as exc:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc),
            prospect_id=prospect_id,
        )

    except ValueError as exc:
        return _error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
            prospect_id=prospect_id,
        )

    except pyodbc.IntegrityError:
        logger.exception(
            "Database integrity error while saving "
            "vendor onboarding"
        )

        return _error_response(
            status_code=status.HTTP_409_CONFLICT,
            message=(
                "The request conflicts with existing "
                "vendor onboarding data"
            ),
            prospect_id=prospect_id,
        )

    except pyodbc.Error:
        logger.exception(
            "Database error while saving vendor onboarding"
        )

        return _error_response(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            message=(
                "Database error while saving vendor "
                "onboarding information"
            ),
            prospect_id=prospect_id,
        )

    except Exception:
        logger.exception(
            "Unexpected error while saving vendor onboarding"
        )

        return _error_response(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            message=(
                "Unexpected error while saving vendor "
                "onboarding information"
            ),
            prospect_id=prospect_id,
        )


# from __future__ import annotations

# import json
# import logging
# from typing import Any

# import pyodbc
# from fastapi import (
#     APIRouter,
#     Request,
#     status,
# )
# from fastapi.responses import JSONResponse
# from pydantic import ValidationError
# from starlette.datastructures import (
#     UploadFile as StarletteUploadFile,
# )

# from app.schemas.vendoronboardingform_schema import (
#     ParallelApprovalFinalizeRequest,
#     ParallelApprovalFinalizeResponse,
#     VendorFetchRequest,
#     VendorFetchResponse,
#     VendorInvitationRequest,
#     VendorInvitationResponse,
#     VendorSaveRequest,
#     VendorStatusUpdateRequest,
#     VendorStatusUpdateResponse,
# )
# from app.services.vendoronboard.form_service import (
#     SubmissionValidationError,
#     fetch_vendor_onboarding,
#     save_vendor_onboarding,
# )


# logger = logging.getLogger(__name__)


# router = APIRouter(
#     prefix="/api/vendor-onboarding",
#     tags=["Vendor Onboarding"],
# )


# # ============================================================
# # OPENAPI SCHEMA
# # ============================================================

# def _get_model_json_schema() -> dict[str, Any]:

#     # Pydantic V2
#     if hasattr(
#         VendorSaveRequest,
#         "model_json_schema",
#     ):
#         return (
#             VendorSaveRequest
#             .model_json_schema()
#         )

#     # Pydantic V1 fallback
#     return VendorSaveRequest.schema()


# VENDOR_SAVE_JSON_SCHEMA = (
#     _get_model_json_schema()
# )


# SAVE_REQUEST_BODY_SCHEMA = {
#     "requestBody": {
#         "required": True,
#         "content": {
#             "application/json": {
#                 "schema": VENDOR_SAVE_JSON_SCHEMA,
#                 "description": (
#                     "Use application/json when there "
#                     "are no attachment changes."
#                 ),
#             },
#             "multipart/form-data": {
#                 "schema": {
#                     "type": "object",
#                     "required": [
#                         "FORM_DATA",
#                     ],
#                     "properties": {
#                         "FORM_DATA": {
#                             "type": "string",
#                             "description": (
#                                 "Complete VendorSaveRequest "
#                                 "as a JSON string"
#                             ),
#                         },
#                         "ATTACHMENT_METADATA": {
#                             "type": "string",
#                             "default": "[]",
#                             "description": (
#                                 "Complete current attachment "
#                                 "metadata as a JSON array string"
#                             ),
#                         },
#                         "FILES": {
#                             "type": "array",
#                             "items": {
#                                 "type": "string",
#                                 "format": "binary",
#                             },
#                             "description": (
#                                 "New or replacement PDF, JPG, "
#                                 "JPEG or PNG files"
#                             ),
#                         },
#                     },
#                 }
#             },
#         },
#     }
# }


# # ============================================================
# # ERROR RESPONSE
# # ============================================================

# def _error_response(
#     status_code: int,
#     message: str,
#     prospect_id: str | None = None,
#     errors: list[Any] | None = None,
# ) -> JSONResponse:

#     response_content: dict[str, Any] = {
#         "SUCCESS": False,
#         "MESSAGE": message,
#     }

#     if prospect_id:
#         response_content["PROSPECT_ID"] = (
#             prospect_id.strip().upper()
#         )

#     if errors:
#         response_content["ERRORS"] = errors

#     return JSONResponse(
#         status_code=status_code,
#         content=response_content,
#     )


# def _validation_errors(
#     exc: ValidationError,
# ) -> list[dict[str, Any]]:

#     result: list[dict[str, Any]] = []

#     for error in exc.errors():
#         result.append(
#             {
#                 "FIELD": ".".join(
#                     str(value)
#                     for value in error.get(
#                         "loc",
#                         [],
#                     )
#                 ),
#                 "MESSAGE": error.get(
#                     "msg",
#                     "Validation error",
#                 ),
#                 "TYPE": error.get(
#                     "type",
#                     "validation_error",
#                 ),
#             }
#         )

#     return result


# def _validate_save_payload(
#     value: Any,
# ) -> VendorSaveRequest:

#     # Pydantic V2
#     if hasattr(
#         VendorSaveRequest,
#         "model_validate",
#     ):
#         return (
#             VendorSaveRequest
#             .model_validate(value)
#         )

#     # Pydantic V1 fallback
#     return VendorSaveRequest.parse_obj(
#         value
#     )


# def _validate_save_json(
#     value: str,
# ) -> VendorSaveRequest:

#     # Pydantic V2
#     if hasattr(
#         VendorSaveRequest,
#         "model_validate_json",
#     ):
#         return (
#             VendorSaveRequest
#             .model_validate_json(value)
#         )

#     # Pydantic V1 fallback
#     return VendorSaveRequest.parse_raw(
#         value
#     )


# def _model_to_json(
#     payload: VendorSaveRequest,
# ) -> str:

#     # Pydantic V2
#     if hasattr(
#         payload,
#         "model_dump_json",
#     ):
#         return payload.model_dump_json(by_alias=True)

#     # Pydantic V1 fallback
#     return payload.json()


# # ============================================================
# # FETCH API
# # ============================================================
# @router.post(
#     "/fetch",
#     summary="Fetch complete vendor onboarding information",
#     description=(
#         "Fetches registration, contacts, customers, OEMs, "
#         "certifications, questionnaire and attachment metadata."
#     ),
#     status_code=status.HTTP_200_OK,
#     response_model=VendorFetchResponse,
#     response_model_by_alias=True,
# )
# def fetch_vendor_information(
#     payload: VendorFetchRequest,
# ):
#     try:
#         return fetch_vendor_onboarding(
#             payload
#         )

#     except LookupError as exc:
#         return _error_response(
#             status_code=status.HTTP_404_NOT_FOUND,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except ValueError as exc:
#         return _error_response(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             message=str(exc),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except pyodbc.Error:
#         logger.exception(
#             "Database error while fetching vendor onboarding"
#         )

#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message=(
#                 "Database error while fetching vendor "
#                 "onboarding information"
#             ),
#             prospect_id=payload.PROSPECT_ID,
#         )

#     except Exception:
#         logger.exception(
#             "Unexpected error while fetching vendor onboarding"
#         )

#         return _error_response(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             message=(
#                 "Unexpected error while fetching vendor "
#                 "onboarding information"
#             ),
#             prospect_id=payload.PROSPECT_ID,
#         )
# # ============================================================
# # SAVE API
# # ============================================================


# # ============================================================
# # INVITATION REGISTRATION API
# # ============================================================

# # @router.post(
# #     "/invitation/register",
# #     summary="Create the INVITED registration shell",
# #     description=(
# #         "Reads the prospect only from the secondary "
# #         "d365_VendorProspect table and creates or preserves the "
# #         "matching HIQ_VendorRegistration row in both databases."
# #     ),
# #     response_model=VendorInvitationResponse,
# #     status_code=status.HTTP_200_OK,
# # )
# # def register_vendor_invitation(
# #     payload: VendorInvitationRequest,
# # ):
# #     try:
# #         return create_invited_registration(
# #             prospect_id=payload.PROSPECT_ID,
# #             changed_by=payload.CHANGED_BY,
# #         )

# #     except LookupError as exc:
# #         return _error_response(
# #             status_code=status.HTTP_404_NOT_FOUND,
# #             message=str(exc),
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except ValueError as exc:
# #         return _error_response(
# #             status_code=status.HTTP_400_BAD_REQUEST,
# #             message=str(exc),
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except pyodbc.IntegrityError:
# #         logger.exception(
# #             "Database integrity error while creating invitation registration"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_409_CONFLICT,
# #             message="Invitation registration conflicts with existing data",
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except pyodbc.Error:
# #         logger.exception(
# #             "Database error while creating invitation registration"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             message="Database error while creating invitation registration",
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except Exception:
# #         logger.exception(
# #             "Unexpected error while creating invitation registration"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             message="Unexpected error while creating invitation registration",
# #             prospect_id=payload.PROSPECT_ID,
# #         )


# # ============================================================
# # INTERNAL STATUS UPDATE API
# # ============================================================

# # @router.post(
# #     "/status/update",
# #     summary="Update workflow status in both databases",
# #     description=(
# #         "Internal endpoint for risk, approval and D365 services. "
# #         "Updates HIQ_VendorRegistration.Status in both primary and "
# #         "secondary databases and records status history."
# #     ),
# #     response_model=VendorStatusUpdateResponse,
# #     status_code=status.HTTP_200_OK,
# # )
# # def update_vendor_status(
# #     payload: VendorStatusUpdateRequest,
# # ):
# #     try:
# #         return update_vendor_workflow_status(
# #             prospect_id=payload.PROSPECT_ID,
# #             new_status=payload.STATUS.value,
# #             changed_by=payload.CHANGED_BY,
# #             remarks=payload.REMARKS,
# #             expected_current_status=(
# #                 payload.EXPECTED_CURRENT_STATUS.value
# #                 if payload.EXPECTED_CURRENT_STATUS is not None
# #                 else None
# #             ),
# #         )

# #     except LookupError as exc:
# #         return _error_response(
# #             status_code=status.HTTP_404_NOT_FOUND,
# #             message=str(exc),
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except ValueError as exc:
# #         return _error_response(
# #             status_code=status.HTTP_400_BAD_REQUEST,
# #             message=str(exc),
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except pyodbc.Error:
# #         logger.exception(
# #             "Database error while updating vendor workflow status"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             message="Database error while updating vendor workflow status",
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except Exception:
# #         logger.exception(
# #             "Unexpected error while updating vendor workflow status"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             message="Unexpected error while updating vendor workflow status",
# #             prospect_id=payload.PROSPECT_ID,
# #         )


# # ============================================================
# # PARALLEL APPROVAL FINALIZATION API
# # ============================================================

# # @router.post(
# #     "/approval/finalize",
# #     summary="Finalize a completed parallel approval batch",
# #     description=(
# #         "Reads approval decisions from the secondary database. "
# #         "If any approver is still pending, status remains IN_APPROVAL. "
# #         "When all decisions are complete, all approved becomes APPROVED; "
# #         "one or more rejected becomes RETURNED."
# #     ),
# #     response_model=ParallelApprovalFinalizeResponse,
# #     status_code=status.HTTP_200_OK,
# # )
# # def finalize_vendor_parallel_approval(
# #     payload: ParallelApprovalFinalizeRequest,
# # ):
# #     try:
# #         return finalize_parallel_approval(
# #             prospect_id=payload.PROSPECT_ID,
# #             approval_batch_id=payload.APPROVAL_BATCH_ID,
# #             changed_by=payload.CHANGED_BY,
# #             remarks=payload.REMARKS,
# #         )

# #     except LookupError as exc:
# #         return _error_response(
# #             status_code=status.HTTP_404_NOT_FOUND,
# #             message=str(exc),
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except ValueError as exc:
# #         return _error_response(
# #             status_code=status.HTTP_400_BAD_REQUEST,
# #             message=str(exc),
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except pyodbc.Error:
# #         logger.exception(
# #             "Database error while finalizing parallel approval"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             message="Database error while finalizing parallel approval",
# #             prospect_id=payload.PROSPECT_ID,
# #         )

# #     except Exception:
# #         logger.exception(
# #             "Unexpected error while finalizing parallel approval"
# #         )
# #         return _error_response(
# #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
# #             message="Unexpected error while finalizing parallel approval",
# #             prospect_id=payload.PROSPECT_ID,
# #         )


# # ============================================================
# # SAVE API
# # ============================================================

# @router.post(
#     "/save",
#     summary=(
#         "Save vendor onboarding information"
#     ),
#     description="""
# This endpoint supports two request types.

# 1. application/json

# Use this when there are no attachment changes.

# Existing attachments remain unchanged.

# 2. multipart/form-data

# Use this when adding, replacing or deleting attachments.

# Status rules:

# SAVE or NEXT:
# Status = DRAFT
# IsDraft = 0

# SUBMIT:
# Status = TO_EVALUATE
# IsDraft = 1
# """,
#     status_code=status.HTTP_200_OK,
#     response_model=None,
#     openapi_extra=SAVE_REQUEST_BODY_SCHEMA,
# )
# async def save_vendor_information(
#     request: Request,
# ):
#     prospect_id: str | None = None
#     uploaded_files: list[dict[str, Any]] = []

#     try:
#         content_type = (
#             request.headers
#             .get(
#                 "content-type",
#                 "",
#             )
#             .split(
#                 ";",
#                 1,
#             )[0]
#             .strip()
#             .lower()
#         )

#         # ====================================================
#         # APPLICATION/JSON
#         # ====================================================

#         if content_type == "application/json":
#             request_json = await request.json()

#             payload = _validate_save_payload(
#                 request_json
#             )

#             prospect_id = payload.PROSPECT_ID

#             return save_vendor_onboarding(
#                 form_data_json=(
#                     _model_to_json(payload)
#                 ),
#                 attachment_metadata_json=None,
#                 uploaded_files=[],
#                 sync_attachments=False,
#             )

#         # ====================================================
#         # MULTIPART/FORM-DATA
#         # ====================================================

#         if content_type == "multipart/form-data":
#             form = await request.form()

#             form_data = form.get(
#                 "FORM_DATA"
#             )

#             if (
#                 not isinstance(
#                     form_data,
#                     str,
#                 )
#                 or not form_data.strip()
#             ):
#                 raise ValueError(
#                     "FORM_DATA is required"
#                 )

#             # Validate the JSON and obtain PROSPECT_ID.
#             payload = _validate_save_json(
#                 form_data
#             )

#             prospect_id = payload.PROSPECT_ID

#             attachment_metadata = form.get(
#                 "ATTACHMENT_METADATA",
#                 "[]",
#             )

#             if attachment_metadata is None:
#                 attachment_metadata = "[]"

#             if not isinstance(
#                 attachment_metadata,
#                 str,
#             ):
#                 raise ValueError(
#                     "ATTACHMENT_METADATA must be "
#                     "a JSON string"
#                 )

#             raw_files = form.getlist(
#                 "FILES"
#             )

#             for file_item in raw_files:

#                 # Ignore an empty Swagger/Postman file field.
#                 if file_item in {
#                     None,
#                     "",
#                 }:
#                     continue

#                 if not isinstance(
#                     file_item,
#                     StarletteUploadFile,
#                 ):
#                     raise ValueError(
#                         "FILES must contain uploaded files"
#                     )

#                 try:
#                     file_bytes = (
#                         await file_item.read()
#                     )

#                     file_index = len(
#                         uploaded_files
#                     )

#                     uploaded_files.append(
#                         {
#                             "FILE_INDEX":
#                                 file_index,
#                             "FILE_NAME": (
#                                 file_item.filename
#                                 or f"file_{file_index}"
#                             ),
#                             "CONTENT_TYPE": (
#                                 file_item.content_type
#                                 or
#                                 "application/octet-stream"
#                             ),
#                             "FILE_BYTES":
#                                 file_bytes,
#                         }
#                     )

#                 finally:
#                     await file_item.close()

#             return save_vendor_onboarding(
#                 form_data_json=form_data,
#                 attachment_metadata_json=(
#                     attachment_metadata
#                 ),
#                 uploaded_files=uploaded_files,
#                 sync_attachments=True,
#             )

#         return _error_response(
#             status_code=(
#                 status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
#             ),
#             message=(
#                 "Content-Type must be application/json "
#                 "or multipart/form-data"
#             ),
#             prospect_id=prospect_id,
#         )

#     except json.JSONDecodeError as exc:
#         return _error_response(
#             status_code=(
#                 status.HTTP_422_UNPROCESSABLE_ENTITY
#             ),
#             message="Invalid JSON request",
#             prospect_id=prospect_id,
#             errors=[str(exc)],
#         )

#     except ValidationError as exc:
#         return _error_response(
#             status_code=(
#                 status.HTTP_422_UNPROCESSABLE_ENTITY
#             ),
#             message="Request validation failed",
#             prospect_id=prospect_id,
#             errors=_validation_errors(exc),
#         )

#     except SubmissionValidationError as exc:
#         return _error_response(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             message="Submission validation failed",
#             prospect_id=prospect_id,
#             errors=exc.errors,
#         )

#     except LookupError as exc:
#         return _error_response(
#             status_code=status.HTTP_404_NOT_FOUND,
#             message=str(exc),
#             prospect_id=prospect_id,
#         )

#     except ValueError as exc:
#         return _error_response(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             message=str(exc),
#             prospect_id=prospect_id,
#         )

#     except pyodbc.IntegrityError:
#         logger.exception(
#             "Database integrity error while saving "
#             "vendor onboarding"
#         )

#         return _error_response(
#             status_code=status.HTTP_409_CONFLICT,
#             message=(
#                 "The request conflicts with existing "
#                 "vendor onboarding data"
#             ),
#             prospect_id=prospect_id,
#         )

#     except pyodbc.Error:
#         logger.exception(
#             "Database error while saving vendor onboarding"
#         )

#         return _error_response(
#             status_code=(
#                 status.HTTP_500_INTERNAL_SERVER_ERROR
#             ),
#             message=(
#                 "Database error while saving vendor "
#                 "onboarding information"
#             ),
#             prospect_id=prospect_id,
#         )

#     except Exception:
#         logger.exception(
#             "Unexpected error while saving vendor onboarding"
#         )

#         return _error_response(
#             status_code=(
#                 status.HTTP_500_INTERNAL_SERVER_ERROR
#             ),
#             message=(
#                 "Unexpected error while saving vendor "
#                 "onboarding information"
#             ),
#             prospect_id=prospect_id,
#         )