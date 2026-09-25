from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# ACTION
# ============================================================

class SaveAction(str, Enum):
    SAVE = "SAVE"
    NEXT = "NEXT"
    SUBMIT = "SUBMIT"


class VendorWorkflowStatus(str, Enum):
    INVITED = "INVITED"
    DRAFT = "DRAFT"
    TO_EVALUATE = "TO_EVALUATE"
    IN_APPROVAL = "IN_APPROVAL"
    RETURNED = "RETURNED"
    RESUBMITTED = "RESUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    VENDOR_CREATED = "VENDOR_CREATED"


# ============================================================
# FETCH REQUEST
# ============================================================

class VendorFetchRequest(BaseModel):
    PROSPECT_ID: str = Field(
        ...,
        min_length=1,
        max_length=10,
        examples=["PR0001"],
    )


class VendorInvitationRequest(BaseModel):
    PROSPECT_ID: str = Field(
        ...,
        min_length=1,
        max_length=10,
        examples=["PR0783"],
    )
    CHANGED_BY: Optional[str] = Field(
        default="INVITATION_SERVICE",
        max_length=150,
    )


class VendorInvitationResponse(BaseModel):
    SUCCESS: bool
    MESSAGE: str
    PROSPECT_ID: str
    PROSPECT_SEQ: int
    COMPANY_NAME: Optional[str] = None
    EMAIL: Optional[str] = None
    VENDOR_ACCOUNT: Optional[str] = None
    STATUS: VendorWorkflowStatus
    ISDRAFT: int
    ROW_CREATED: bool


class VendorStatusUpdateRequest(BaseModel):
    PROSPECT_ID: str = Field(
        ...,
        min_length=1,
        max_length=10,
        examples=["PR0783"],
    )
    STATUS: VendorWorkflowStatus
    EXPECTED_CURRENT_STATUS: Optional[VendorWorkflowStatus] = None
    CHANGED_BY: Optional[str] = Field(default=None, max_length=150)
    REMARKS: Optional[str] = Field(default=None, max_length=2000)


class VendorStatusUpdateResponse(BaseModel):
    SUCCESS: bool
    MESSAGE: str
    PROSPECT_ID: str
    PREVIOUS_STATUS: VendorWorkflowStatus
    STATUS: VendorWorkflowStatus


class ParallelApprovalFinalizeRequest(BaseModel):
    PROSPECT_ID: str = Field(
        ...,
        min_length=1,
        max_length=10,
        examples=["PR0783"],
    )
    APPROVAL_BATCH_ID: int = Field(..., gt=0)
    CHANGED_BY: Optional[str] = Field(default=None, max_length=150)
    REMARKS: Optional[str] = Field(default=None, max_length=2000)


class ParallelApprovalFinalizeResponse(BaseModel):
    SUCCESS: bool
    MESSAGE: str
    PROSPECT_ID: str
    APPROVAL_BATCH_ID: int
    RISK_ASSESSMENT_ID: Optional[int] = None
    STATUS: VendorWorkflowStatus
    ASSIGNED_COUNT: int
    APPROVED_COUNT: int
    REJECTED_COUNT: int
    PENDING_COUNT: int
    FINALIZED: bool


# ============================================================
# GENERAL INFORMATION
# ============================================================

class GeneralInformationInput(BaseModel):
    COMPANYNAME: Optional[str] = None
    COMPANYREGISTERNUMBER: Optional[str] = None
    NATUREOFCOMPANY: Optional[str] = None
    NATUREOFBUSINESS: Optional[str] = None
    SCOPEOFSUPPLY: Optional[str] = None
    YEAROFESTABLISHMENT: Optional[int | str] = None
    NUMBEROFEMPLOYEES: Optional[int | str] = None
    COUNTRYOFORIGIN: Optional[str] = None
    STREET: Optional[str] = None
    CITY: Optional[str] = None
    DISTRICT: Optional[str] = None
    STATE: Optional[str] = None
    COUNTRY: Optional[str] = None
    POSTALCODE: Optional[str] = None
    CURRENTSUPPLYLOCATION: Optional[list[str] | str] = None
    WORKINGTIMEZONE: Optional[str] = None
    WEEKLYHOLIDAY: Optional[str] = None


# ============================================================
# FINANCIAL & COMMERCIAL
# ============================================================

class FinancialCommercialInput(BaseModel):
    SUPPLIERTYPE: Optional[str] = None
    CURRENCY: Optional[str] = None
    PAYMENTTERMS: Optional[str] = None
    DELIVERYTERMS: Optional[str] = None
    BANKNAME: Optional[str] = None
    BANKADDRESS: Optional[str] = None
    BANKACCOUNTNUMBER: Optional[str] = None
    IFSCCODE: Optional[str] = None
    BENEFICIARYNAME: Optional[str] = None
    BANKCONTACTNUMBER: Optional[str] = None


    PANNUMBER: Optional[str] = None
    REGISTRATIONTYPE: Optional[str] = None
    REGISTRATIONNUMBER: Optional[str] = None
    FACTORYLICENCENUMBER: Optional[str] = None

    # Base64 value is accepted during save but not returned during fetch.
    FACTORYLICENSE: Optional[str] = None
    FACTORYLICENSEFILENAME: Optional[str] = None

    # Matching FactoryLicense row returned during fetch.
    ATTACHMENTID: Optional[int] = None
    ATTACHMENTFOR: Optional[Literal["FactoryLicense"]] = None
    PARENTRECORDID: Optional[int] = None
    VENDACCOUNT: Optional[str] = None
    FILEEXTENSION: Optional[str] = None
    CONTENTTYPE: Optional[str] = None
    FILESIZEBYTES: Optional[int] = None

    BENEFICIARYADDRESS: str | None = None
    SWIFTCODE: str | None = None
    IBAN: str | None = None
    BANKBRANCHCODE: str | None = None
    IFCCODE: str | None = None


# ============================================================
# CONTACTS
# ============================================================

class ContactInput(BaseModel):
    CONTACTID: Optional[int] = None
    CONTACTPERSONNAME: Optional[str] = None
    DESIGNATION: Optional[str] = None
    EMAIL: Optional[str] = None
    MOBILENUMBER: Optional[str] = None
    ISPRIMARY: Optional[bool] = False
    LANLINE: Optional[str] = None


# ============================================================
# BUSINESS REFERENCES
# ============================================================

class BusinessReferenceInput(BaseModel):
    CUSTOMERID: Optional[int] = None
    CUTOMERNAME: Optional[str] = None
    INDUSTRY: Optional[str] = None
    COUNTRY: Optional[str] = None


# ============================================================
# OEM DETAILS
# ============================================================

class OEMInput(BaseModel):
    OEMID: Optional[int] = None
    CLIENTKEY: Optional[str] = None
    OEMNAME: Optional[str] = None

    # Base64 value is accepted during save but not returned during fetch.
    CERTIFICATE: Optional[Any] = None
    CERTIFICATENAME: Optional[str] = None

    # Matching OEMCert row returned during fetch.
    ATTACHMENTID: Optional[int] = None
    ATTACHMENTFOR: Optional[Literal["OEMCert"]] = None
    PARENTRECORDID: Optional[int] = None
    VENDACCOUNT: Optional[str] = None
    FILEEXTENSION: Optional[str] = None
    CONTENTTYPE: Optional[str] = None
    FILESIZEBYTES: Optional[int] = None


# ============================================================
# CERTIFICATIONS
# ============================================================

class CertificationInput(BaseModel):
    CERTIFICATIONID: Optional[int] = None
    CLIENTKEY: Optional[str] = None
    CERTIFICATIONTYPE: Optional[str] = None
    STATUS: Optional[str] = None
    CERTIFICATIONNUMBER: Optional[str] = None
    VALIDUNTIL: Optional[str] = None

    # Base64 value is accepted during save but not returned during fetch.
    ATTACHMENT: Optional[Any] = None
    ATTACHMENTNAME: Optional[str] = None

    # Matching Certification attachment row returned during fetch.
    ATTACHMENTID: Optional[int] = None
    ATTACHMENTFOR: Optional[Literal["Certification"]] = None
    PARENTRECORDID: Optional[int] = None
    VENDACCOUNT: Optional[str] = None
    FILEEXTENSION: Optional[str] = None
    CONTENTTYPE: Optional[str] = None
    FILESIZEBYTES: Optional[int] = None


# ============================================================
# LEGAL DOCUMENTS
# ============================================================

class LegalDocumentInput(BaseModel):
    LEGALDOCUMENTID: Optional[int] = None
    CLIENTKEY: Optional[str] = None
    DOCUMENTNAME: Optional[str] = None

    # Base64 is accepted during create/update and omitted during fetch.
    LEGALFILE: Optional[Any] = None
    LEGALFILENAME: Optional[str] = None

    # Matching LegalDocument attachment metadata returned during fetch.
    ATTACHMENTID: Optional[int] = None
    ATTACHMENTFOR: Optional[Literal["LegalDocument"]] = None
    PARENTRECORDID: Optional[int] = None
    VENDACCOUNT: Optional[str] = None
    FILEEXTENSION: Optional[str] = None
    CONTENTTYPE: Optional[str] = None
    FILESIZEBYTES: Optional[int] = None


# ============================================================
# QUALITY QUESTIONNAIRE
# ============================================================


class QualityQuestionnaireInput(BaseModel):
    INSTRUMENTSCALIBRATED: Optional[Any] = None
    INSTRUMENTSCALIBRATEDREMARK: Optional[str] = None

    ADEQUATEEQUIPMENT: Optional[Any] = None
    ADEQUATEEQUIPMENTREMARK: Optional[str] = None

    DEDICATEDQUALITYFUNCTION: Optional[Any] = None
    DEDICATEDQUALITYFUNCTIONREMARK: Optional[str] = None

    SUPPLYCOC: Optional[Any] = None
    SUPPLYCOCREMARK: Optional[str] = None

    RETAINCOC15YEARS: Optional[Any] = None
    RETAINCOC15YEARSREMARK: Optional[str] = None

    CORRECTIVEACTIONSYSTEM: Optional[Any] = None
    CORRECTIVEACTIONSYSTEMREMARK: Optional[str] = None

    PACKINGENSURESNODAMAGE: Optional[Any] = None
    PACKINGENSURESNODAMAGEREMARK: Optional[str] = None

    MEETSHELFLIFEREQUIREMENTS: Optional[Any] = None
    MEETSHELFLIFEREQUIREMENTSREMARK: Optional[str] = None

    EMPLOYEESAWAREETHICS: Optional[Any] = None
    EMPLOYEESAWAREETHICSREMARK: Optional[str] = None

    PREVENTCOUNTERFEITPARTS: Optional[Any] = None
    PREVENTCOUNTERFEITPARTSREMARK: Optional[str] = None

    NOTIFYORGANIZATIONCHANGES: Optional[Any] = None
    NOTIFYORGANIZATIONCHANGESREMARK: Optional[str] = None
# ============================================================
# DECLARATION
# ============================================================
class LatestTermsVersionResponse(BaseModel):
    TERMSVERSIONID: int
    VERSIONNUMBER: str | None = None
    FILENAME: str | None = None
    CONTENTTYPE: str | None = None
    FILESIZEBYTES: int = 0
    ISACTIVE: bool = True
    CREATEDON: str | None = None
    ACCEPTED: bool = False
    ACCEPTEDON: str | None = None

class DeclarationInput(BaseModel):
    TITLE: Optional[str] = None
    NAME: Optional[str] = None
    DESIGNATION: Optional[str] = None
    EMAIL: Optional[str] = None
    MOBILENUMBER: Optional[str] = None
    AGREEDTODECLARATION: Optional[bool] = False
    DATESUBMISSION: Optional[str] = None
    AUTHORIZEDCONFIRM: Optional[bool] = False
    ACCEPTTERMS: Optional[bool] = False
    TERMSVERSIONID: int | None = None
    LATESTVERSION: LatestTermsVersionResponse | None = None

from pydantic import BaseModel, Field


class AcceptedTermsHistoryOutput(BaseModel):
    PROSPECTTERMSHISTORYID: int
    PROSPECTID: str
    TERMSVERSIONID: int

    VERSIONNUMBER: str | None = None
    FILENAME: str | None = None
    CONTENTTYPE: str | None = None

    ISCURRENT: bool = False
    ISVERSIONACTIVE: bool = False

    ASSIGNEDON: str | None = None
    ACCEPTEDON: str | None = None

    ASSIGNEDBYUSERID: int | None = None
    ACCEPTEDBYUSERID: int | None = None

    REMARKS: str | None = None
    CREATEDON: str | None = None
# ============================================================
# COMPLETE SAVE REQUEST
# ============================================================

class VendorSaveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ACTION: SaveAction
    ISDRAFT: int = Field(..., ge=0, le=1)
    PROSPECT_ID: str = Field(..., min_length=1, max_length=10)
    DISTRIBUTORAUTHORIZATION: Optional[bool] = None

    generalInformation: GeneralInformationInput = Field(
        default_factory=GeneralInformationInput,
        alias="GENERALINFORMATION",
    )
    financialCommercial: FinancialCommercialInput = Field(
        default_factory=FinancialCommercialInput,
        alias="FINANCIALCOMMERCIAL",
    )
    contacts: list[ContactInput] = Field(default_factory=list)
    businessReference: list[BusinessReferenceInput] = Field(default_factory=list)
    oemdetails: list[OEMInput] = Field(default_factory=list)
    certification: list[CertificationInput] = Field(default_factory=list)
    legaldocument: list[LegalDocumentInput] = Field(default_factory=list)
    qualityQuestionnaire: QualityQuestionnaireInput = Field(
        default_factory=QualityQuestionnaireInput
    )
    declaration: DeclarationInput = Field(default_factory=DeclarationInput)


# ============================================================
# MULTIPART ATTACHMENT METADATA
# ============================================================

class AttachmentMetadata(BaseModel):
    ATTACHMENT_ID: Optional[int] = None
    FILE_INDEX: Optional[int] = Field(default=None, ge=0)
    ATTACHMENT_FOR: Literal[
        "FactoryLicense",
        "OEMCert",
        "Certification",
        "LegalDocument",
    ]
    PARENT_RECORD_ID: Optional[int] = None
    PARENT_CLIENT_KEY: Optional[str] = None
    VEND_ACCOUNT: Optional[str] = None


# ============================================================
# FETCH RESPONSE
# ============================================================

class AttachmentFetchOutput(BaseModel):
    AttachmentId: Optional[int] = None
    DocumentId: Optional[Any] = None
    ProspectSeq: Optional[int] = None
    ProspectId: Optional[str] = None
    AttachmentFor: Optional[str] = None
    ParentRecordId: Optional[int] = None
    VendAccount: Optional[str] = None
    FileName: Optional[str] = None
    FileExtension: Optional[str] = None
    ContentType: Optional[str] = None
    FileSizeBytes: Optional[int] = None
    ReceivedFromD365: Optional[bool] = None
    ReceivedDateTime: Optional[Any] = None
    CreatedDateTime: Optional[Any] = None
    ModifiedDateTime: Optional[Any] = None


class VendorFetchResponse(BaseModel):
    SUCCESS: bool
    MESSAGE: str
    ACTION: SaveAction
    STATUS: VendorWorkflowStatus
    ISDRAFT: int
    PROSPECT_ID: str
    VENDOR_ACCOUNT: Optional[str] = None
    DISTRIBUTORAUTHORIZATION: Optional[bool] = None

    GENERALINFORMATION: GeneralInformationInput = Field(
        default_factory=GeneralInformationInput
    )
    FINANCIALCOMMERCIAL: FinancialCommercialInput = Field(
        default_factory=FinancialCommercialInput
    )
    contacts: list[ContactInput] = Field(default_factory=list)
    businessReference: list[BusinessReferenceInput] = Field(default_factory=list)
    oemdetails: list[OEMInput] = Field(default_factory=list)
    certification: list[CertificationInput] = Field(default_factory=list)
    legaldocument: list[LegalDocumentInput] = Field(default_factory=list)
    qualityQuestionnaire: QualityQuestionnaireInput = Field(
        default_factory=QualityQuestionnaireInput
    )
    declaration: DeclarationInput = Field(default_factory=DeclarationInput)
    ATTACHMENTS: list[AttachmentFetchOutput] = Field(default_factory=list)
    ACCEPTEDTERMSHISTORY: list[AcceptedTermsHistoryOutput] = Field(default_factory=list)