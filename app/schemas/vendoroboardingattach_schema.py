from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


NULL_TEXT_VALUES = {
    "",
    "null",
    "none",
    "undefined",
    "n/a",
}

class AttachmentContentRequest(BaseModel):
    AttachmentId: int
    ProspectId: str
    AttachmentFor: Optional[str] = None
    FileName: Optional[str] = None


class AttachmentContentRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    AttachmentId: int = Field(..., gt=0)

    ProspectId: str = Field(
        ...,
        min_length=1,
        max_length=20,
    )

    AttachmentFor: Optional[str] = None

    FileName: Optional[str] = None

    @field_validator("ProspectId")
    @classmethod
    def normalize_prospect_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip().upper()

        if not normalized:
            raise ValueError("ProspectId is required")

        return normalized

    @field_validator("FileName")
    @classmethod
    def normalize_file_name(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        file_name = Path(value).name.strip()

        return file_name or None
    

class AttachmentContentRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    AttachmentId: int = Field(
        ...,
        gt=0,
    )

    ProspectId: str = Field(
        ...,
        min_length=1,
        max_length=20,
    )

    AttachmentFor: Optional[str] = None

    FileName: Optional[str] = None

    @field_validator("ProspectId")
    @classmethod
    def normalize_prospect_id(
        cls,
        value: str,
    ) -> str:
        normalized = value.strip().upper()

        if not normalized:
            raise ValueError(
                "ProspectId is required"
            )

        return normalized

    @field_validator("FileName")
    @classmethod
    def normalize_file_name(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        file_name = Path(value).name.strip()

        return file_name or None



# from __future__ import annotations

# from pathlib import Path
# from typing import Literal, Optional

# from pydantic import (
#     BaseModel,
#     ConfigDict,
#     Field,
#     field_validator,
# )


# class AttachmentContentRequest(BaseModel):
#     """
#     Request payload for fetching raw attachment content.
#     """

#     model_config = ConfigDict(
#         extra="forbid",
#         str_strip_whitespace=True,
#     )

#     AttachmentId: int = Field(
#         ...,
#         gt=0,
#         examples=[21],
#     )

#     ProspectId: str = Field(
#         ...,
#         min_length=1,
#         max_length=20,
#         examples=["PR0783"],
#     )

#     AttachmentFor: Literal[
#         "FactoryLicense",
#         "OEMCert",
#         "Certification",
#     ]

#     VendAccount: Optional[str] = Field(
#         default=None,
#         examples=["V0001"],
#     )

#     FileName: str = Field(
#         ...,
#         min_length=1,
#         max_length=255,
#         examples=["HiQ-00012526.pdf"],
#     )

#     @field_validator("ProspectId")
#     @classmethod
#     def normalize_prospect_id(
#         cls,
#         value: str,
#     ) -> str:
#         normalized = value.strip().upper()

#         if not normalized:
#             raise ValueError(
#                 "ProspectId is required"
#             )

#         return normalized

#     @field_validator("VendAccount")
#     @classmethod
#     def normalize_vend_account(
#         cls,
#         value: Optional[str],
#     ) -> Optional[str]:
#         if value is None:
#             return None

#         normalized = value.strip().upper()

#         return normalized or None

#     @field_validator("FileName")
#     @classmethod
#     def normalize_file_name(
#         cls,
#         value: str,
#     ) -> str:
#         file_name = Path(value).name.strip()

#         if not file_name:
#             raise ValueError(
#                 "FileName is required"
#             )

#         return file_name