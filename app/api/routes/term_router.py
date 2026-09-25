from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import (
    APIRouter,
    HTTPException,
    Response,
    status,
)

from app.schemas.term_schema import (
    TermsFileFetchRequest,
    TermsVersionUploadRequest,
)
from app.services.term_service import (
    create_terms_version,
    get_terms_file,
)


router = APIRouter(
    prefix="/api/terms-and-conditions",
    tags=["Terms and Conditions"],
)


def _content_disposition(
    file_name: str | None,
) -> str:
    original_name = (
        file_name
        or "terms-and-conditions-file"
    ).strip()

    original_name = (
        original_name
        .replace("\r", "")
        .replace("\n", "")
        .replace('"', "")
    )

    ascii_name = (
        original_name
        .encode(
            "ascii",
            errors="ignore",
        )
        .decode("ascii")
    )

    ascii_name = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        ascii_name,
    ).strip("._")

    if not ascii_name:
        ascii_name = (
            "terms-and-conditions-file"
        )

    encoded_name = quote(
        original_name,
        safe="",
    )

    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded_name}"
    )


@router.post(
    "/version",
    status_code=status.HTTP_201_CREATED,
    summary="Upload PDF or DOCX Terms version",
)
def upload_terms_version(
    payload: TermsVersionUploadRequest,
) -> dict:
    try:
        return create_terms_version(payload)

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/file",
    response_class=Response,
    summary="Download Terms PDF or DOCX",
)
def fetch_terms_file(
    payload: TermsFileFetchRequest,
) -> Response:
    try:
        result = get_terms_file(
            terms_version_id=(
                payload.TERMSVERSIONID
            ),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Terms and Conditions file "
                "was not found."
            ),
        )

    file_bytes = result["file_bytes"]

    return Response(
        content=file_bytes,
        media_type=result["content_type"],
        headers={
            "Content-Disposition":
                _content_disposition(
                    result["file_name"]
                ),
            "Content-Length": str(
                len(file_bytes)
            ),
            "X-Terms-Version-Id": str(
                result["terms_version_id"]
            ),
            "X-Terms-Version": str(
                result["version_number"]
            ),
        },
    )
