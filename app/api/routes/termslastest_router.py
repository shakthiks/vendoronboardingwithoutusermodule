import re
from urllib.parse import quote

from fastapi import (
    APIRouter,
    HTTPException,
    Response,
    status,
)

from app.schemas.termslatest_schema import TermsPdfFetchRequest
from app.services.termlatest_service import get_latest_terms_pdf


router = APIRouter(
    prefix="/api/terms-and-conditions",
    tags=["Terms and Conditions"],
)


def build_content_disposition(
    file_name: str | None,
    disposition: str = "inline",
) -> str:
    """
    Build a safe Content-Disposition header.

    This prevents UnicodeEncodeError when the PDF filename
    contains characters such as –, — or non-English text.
    """

    original_name = (
        file_name
        or "terms-and-conditions.pdf"
    ).strip()

    # Prevent HTTP header injection.
    original_name = (
        original_name
        .replace("\r", "")
        .replace("\n", "")
        .replace('"', "")
    )

    # ASCII-safe fallback filename.
    ascii_name = (
        original_name
        .encode("ascii", errors="ignore")
        .decode("ascii")
    )

    ascii_name = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        ascii_name,
    ).strip("._")

    if not ascii_name:
        ascii_name = "terms-and-conditions.pdf"

    if not ascii_name.lower().endswith(".pdf"):
        ascii_name += ".pdf"

    encoded_name = quote(
        original_name,
        safe="",
    )

    return (
        f'{disposition}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded_name}"
    )


@router.post(
    "/file",
    response_class=Response,
    summary="Fetch Terms and Conditions PDF",
)
def fetch_terms_pdf(
    payload: TermsPdfFetchRequest,
) -> Response:
    """
    TERMSVERSIONID supplied:
        Fetch that exact version.

    TERMSVERSIONID omitted or null:
        Fetch the latest active version.
    """

    try:
        result = get_latest_terms_pdf(
            terms_version_id=payload.TERMSVERSIONID,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if result is None:
        message = (
            f"Terms version {payload.TERMSVERSIONID} was not found."
            if payload.TERMSVERSIONID is not None
            else "No active Terms and Conditions version was found."
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=message,
        )

    file_bytes = result.get("file_bytes")

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The selected Terms version does not contain a PDF.",
        )

    return Response(
        content=file_bytes,
        media_type=(
            result.get("content_type")
            or "application/pdf"
        ),
        headers={
            "Content-Disposition": build_content_disposition(
                result.get("file_name"),
                disposition="inline",
            ),
            "X-Terms-Version-Id": str(
                result["terms_version_id"]
            ),
            "X-Terms-Version": str(
                result.get("version_number")
                or ""
            ),
            "Content-Length": str(
                len(file_bytes)
            ),
        },
    )