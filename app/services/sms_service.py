from loguru import logger
from app.core.config import settings


def send_phone_otp(phone_number: str, otp_code: str) -> bool:
    if settings.is_local:
        logger.info(f"[SMS LOCAL] To={phone_number} | OTP={otp_code}")
        return True
    try:
        from twilio.rest import Client
        Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        ).messages.create(
            body=f"Your Vendor Portal OTP: {otp_code}. Valid 10 mins. Do not share.",
            from_=settings.TWILIO_FROM_NUMBER,
            to=phone_number
        )
        return True
    except Exception as e:
        logger.error(f"[SMS] Failed: {e}")
        return False