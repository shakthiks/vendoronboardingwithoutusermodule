from pydantic import BaseModel, Field


class TermsPdfFetchRequest(BaseModel):
    TERMSVERSIONID: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Provide a version ID to fetch that version. "
            "Leave empty to fetch the latest active version."
        ),
    )