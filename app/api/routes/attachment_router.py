from __future__ import annotations

import logging
from urllib.parse import quote

import pyodbc
from fastapi import (
    APIRouter,
    HTTPException,
    status,
)
from fastapi.responses import Response

from app.schemas.vendoroboardingattach_schema import (
    AttachmentContentRequest,
)
from app.services.attchmentview_services import (
    AttachmentContentMissingError,
    AttachmentMetadataMismatchError,
    AttachmentNotFoundError,
    fetch_attachment_content,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/vendor-onboarding/file-content",
    tags=["Vendor Attachment Content"],
)


@router.post(
    "/fetch",
    response_class=Response,
    summary="Fetch raw attachment binary",
    description=(
        "Returns HIQ_VendorFileAttachment.FileContent "
        "as raw binary. The successful response is not JSON."
    ),
    responses={
        200: {
            "description": "Raw file content",
            "content": {
                "application/pdf": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                },
                "image/jpeg": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                },
                "image/png": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                },
                "application/octet-stream": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                },
            },
        },
        400: {
            "description": "Attachment metadata mismatch",
        },
        404: {
            "description": "Attachment or FileContent not found",
        },
        422: {
            "description": "Invalid request payload",
        },
        500: {
            "description": "Database or server error",
        },
    },
)
def get_attachment_content(
    payload: AttachmentContentRequest,
) -> Response:
    try:
        attachment = fetch_attachment_content(payload)

        file_name = attachment["FILE_NAME"]
        file_bytes = attachment["FILE_BYTES"]
        content_type = (
            attachment["CONTENT_TYPE"]
            or "application/octet-stream"
        )

        encoded_file_name = quote(
            file_name,
            safe="",
        )

        return Response(
            content=file_bytes,
            media_type=content_type,
            headers={
                "Content-Disposition": (
                    "inline; "
                    f"filename*=UTF-8''{encoded_file_name}"
                ),
                "Content-Length": str(len(file_bytes)),
                "X-Attachment-Id": str(
                    attachment["ATTACHMENT_ID"]
                ),
                "X-Prospect-Id": attachment["PROSPECT_ID"],
                "X-Attachment-For": attachment[
                    "ATTACHMENT_FOR"
                ],
                "X-File-Extension": attachment[
                    "FILE_EXTENSION"
                ],
            },
        )

    except AttachmentMetadataMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except (
        AttachmentNotFoundError,
        AttachmentContentMissingError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except pyodbc.Error as exc:
        logger.exception(
            "Database error while fetching attachment content"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Database error while fetching "
                "attachment content"
            ),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Unexpected error while fetching attachment content"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Unexpected error while fetching "
                "attachment content"
            ),
        ) from exc


# from __future__ import annotations

# from urllib.parse import quote

# import pyodbc
# from fastapi import (
#     APIRouter,
#     HTTPException,
#     status,
# )
# from fastapi.responses import Response

# from app.schemas.vendoroboardingattach_schema import (
#     AttachmentContentRequest,
# )
# from app.services.vendoronboard.attchmentview_services import (
#     AttachmentContentMissingError,
#     AttachmentMetadataMismatchError,
#     AttachmentNotFoundError,
#     fetch_attachment_content,
# )


# router = APIRouter(
#     prefix="/api/vendor-onboarding/file-content",
#     tags=[
#         "Vendor Attachment Content"
#     ],
# )


# @router.post(
#     "/fetch",
#     response_class=Response,
#     summary="Fetch raw attachment binary",
#     description=(
#         "Returns HIQ_VendorFileAttachment.FileContent "
#         "as raw binary. The successful response is not JSON."
#     ),
#     responses={
#         200: {
#             "description": "Raw file content",
#             "content": {
#                 "application/pdf": {
#                     "schema": {
#                         "type": "string",
#                         "format": "binary",
#                     }
#                 },
#                 "image/jpeg": {
#                     "schema": {
#                         "type": "string",
#                         "format": "binary",
#                     }
#                 },
#                 "image/png": {
#                     "schema": {
#                         "type": "string",
#                         "format": "binary",
#                     }
#                 },
#                 "application/octet-stream": {
#                     "schema": {
#                         "type": "string",
#                         "format": "binary",
#                     }
#                 },
#             },
#         },
#         400: {
#             "description": (
#                 "Attachment metadata mismatch"
#             ),
#         },
#         404: {
#             "description": (
#                 "Attachment or FileContent not found"
#             ),
#         },
#         500: {
#             "description": (
#                 "Database or server error"
#             ),
#         },
#     },
# )
# def get_attachment_content(
#     payload: AttachmentContentRequest,
# ) -> Response:
#     try:
#         attachment = fetch_attachment_content(
#             payload
#         )

#         file_name = attachment[
#             "FILE_NAME"
#         ]

#         file_bytes = attachment[
#             "FILE_BYTES"
#         ]

#         content_type = (
#             attachment[
#                 "CONTENT_TYPE"
#             ]
#             or "application/octet-stream"
#         )

#         encoded_file_name = quote(
#             file_name,
#             safe="",
#         )

#         return Response(
#             content=file_bytes,
#             media_type=content_type,
#             headers={
#                 "Content-Disposition": (
#                     "inline; "
#                     f"filename*=UTF-8''"
#                     f"{encoded_file_name}"
#                 ),

#                 "Content-Length": str(
#                     len(file_bytes)
#                 ),

#                 "X-Attachment-Id": str(
#                     attachment[
#                         "ATTACHMENT_ID"
#                     ]
#                 ),

#                 "X-Prospect-Id": (
#                     attachment[
#                         "PROSPECT_ID"
#                     ]
#                 ),

#                 "X-Attachment-For": (
#                     attachment[
#                         "ATTACHMENT_FOR"
#                     ]
#                 ),

#                 "X-File-Extension": (
#                     attachment[
#                         "FILE_EXTENSION"
#                     ]
#                 ),

#                 "X-Vend-Account": (
#                     attachment[
#                         "VEND_ACCOUNT"
#                     ]
#                     or ""
#                 ),
#             },
#         )

#     except AttachmentMetadataMismatchError as exc:
#         raise HTTPException(
#             status_code=(
#                 status.HTTP_400_BAD_REQUEST
#             ),
#             detail=str(exc),
#         ) from exc

#     except (
#         AttachmentNotFoundError,
#         AttachmentContentMissingError,
#     ) as exc:
#         raise HTTPException(
#             status_code=(
#                 status.HTTP_404_NOT_FOUND
#             ),
#             detail=str(exc),
#         ) from exc

#     except pyodbc.Error as exc:
#         raise HTTPException(
#             status_code=(
#                 status.HTTP_500_INTERNAL_SERVER_ERROR
#             ),
#             detail=(
#                 "Database error while fetching "
#                 "attachment content"
#             ),
#         ) from exc

#     except Exception as exc:
#         raise HTTPException(
#             status_code=(
#                 status.HTTP_500_INTERNAL_SERVER_ERROR
#             ),
#             detail=(
#                 "Unexpected error while fetching "
#                 "attachment content"
#             ),
#         ) from exc