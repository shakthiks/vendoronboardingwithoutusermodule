from pydantic import BaseModel
from typing import Optional


class AdminCreateVendorReq(BaseModel):
    vendor_account: str        # e.g. "V0001"
    email: str                 # vendor email
    phone: str = None          # optional phone number    

class IdentifierReq(BaseModel):
    identifier: str  # email or phone

class LoginReq(BaseModel):
    identifier: str
    password: str

class OtpSendReq(BaseModel):
    identifier: str
    channel: str  # EMAIL or SMS

class OtpVerifyReq(BaseModel):
    identifier: str
    channel: str
    otp_code: str

class SetPasswordReq(BaseModel):
    identifier: str
    new_password: str
