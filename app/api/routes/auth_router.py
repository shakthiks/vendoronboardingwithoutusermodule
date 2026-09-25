from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.services import auth_service
from app.schemas.auth_schema import AdminCreateVendorReq,IdentifierReq,SetPasswordReq,LoginReq,OtpSendReq,OtpVerifyReq
router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/send-set-password-link")
def send_set_password_link(payload: IdentifierReq):
    return auth_service.send_set_password_link(payload.identifier)

# @router.post("/set-password")
# def set_password(payload: SetPasswordReq):
#     return auth_service.set_password(payload.token, payload.new_password)

@router.post("/forgot-password")
def forgot_password(payload: IdentifierReq):
    return auth_service.forgot_password(payload.identifier)

# @router.post("/reset-password")
# def reset_password(payload: SetPasswordReq):
#     return auth_service.reset_password(payload.token, payload.new_password)

@router.post("/login")
def login(payload: LoginReq, request: Request):
    ip = request.client.host if request.client else None
    return auth_service.login_with_password(payload.identifier, payload.password, ip)

@router.post("/otp/send")
def otp_send(payload: OtpSendReq):
    return auth_service.send_login_otp(payload.identifier, payload.channel)

@router.post("/otp/verify")
def otp_verify(payload: OtpVerifyReq, request: Request):
    ip = request.client.host if request.client else None
    return auth_service.verify_login_otp(payload.identifier, payload.channel, payload.otp_code, ip)


@router.post("/admin/create-vendor")
def admin_create_vendor(payload: AdminCreateVendorReq):
    """
    Admin adds a new vendor to the portal.
    Automatically sends 'Set Your Password' welcome email.

    Steps:
    1. POST this with vendor_account + email
    2. Vendor receives email with set-password link
    3. Vendor clicks link → sets password → can login
    """
    return auth_service.admin_create_vendor(
        payload.vendor_account,
        payload.email,
        payload.phone
    )

@router.post("/set-password/start")
def start_set_password(payload: IdentifierReq):
    return auth_service.start_set_password(payload.identifier)


@router.post("/set-password/verify-otp")
def verify_set_password_otp(payload: OtpVerifyReq):
    return auth_service.verify_set_password_otp(
        payload.identifier,
        payload.otp_code
    )


@router.post("/set-password/confirm")
def set_password(payload: SetPasswordReq):
    return auth_service.set_password_after_otp(
        payload.identifier,
        payload.new_password
    )
