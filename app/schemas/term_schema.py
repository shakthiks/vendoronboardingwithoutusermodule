from __future__ import annotations

from pydantic import BaseModel, Field


class TermsVersionUploadRequest(BaseModel):
    FILE_NAME: str = Field(
        min_length=1,
        max_length=255,
    )

    # Generic field for PDF or DOCX.
    FILE_BASE64: str | None = None

    # Backward compatibility for old PDF requests.
    PDF_BASE64: str | None = None

    CREATED_BY_USER_ID: int | None = None


class TermsFileFetchRequest(BaseModel):
    TERMSVERSIONID: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Provide a version ID to fetch that version. "
            "Leave empty to fetch the latest active version."
        ),
    )


# Compatibility for the existing router name.
class TermsPdfFetchRequest(
    TermsFileFetchRequest
):
    pass