# app/core/security.py
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

# Fix bcrypt __about__ warning
try:
    import bcrypt
    if not hasattr(bcrypt, '__about__'):
        bcrypt.__about__ = type('about', (), {'__version__': bcrypt.__version__})()
except Exception:
    pass

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    # Truncate to 72 bytes — bcrypt limit
    password = password[:72]
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    # Truncate to 72 bytes — bcrypt limit
    plain = plain[:72]
    return pwd_context.verify(plain, hashed)


def create_access_token(payload: dict) -> str:
    data = payload.copy()
    # data["exp"] = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    data["exp"] = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    data["type"] = "access"
    return jwt.encode(data, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def create_refresh_token(payload: dict) -> str:
    data = payload.copy()
    data["exp"] = datetime.utcnow() + timedelta(days=7)
    data["type"] = "refresh"
    return jwt.encode(data, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM]
    )


def get_current_vendor(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> dict:
    try:
        return decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

# from datetime import datetime, timedelta
# from jose import JWTError, jwt
# from passlib.context import CryptContext
# from fastapi import Depends, HTTPException, status
# from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
# from app.core.config import settings

# # Fix bcrypt __about__ warning
# try:
#     import bcrypt
#     if not hasattr(bcrypt, '__about__'):
#         bcrypt.__about__ = type('about', (), {'__version__': bcrypt.__version__})()
# except Exception:
#     pass

# pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
# bearer_scheme = HTTPBearer()


# def hash_password(password: str) -> str:
#     # Truncate to 72 bytes — bcrypt limit
#     password = password[:72]
#     return pwd_context.hash(password)


# def verify_password(plain: str, hashed: str) -> bool:
#     # Truncate to 72 bytes — bcrypt limit
#     plain = plain[:72]
#     return pwd_context.verify(plain, hashed)


# def create_access_token(payload: dict) -> str:
#     data = payload.copy()
#     data["exp"] = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
#     return jwt.encode(data, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# def decode_token(token: str) -> dict:
#     return jwt.decode(
#         token,
#         settings.JWT_SECRET_KEY,
#         algorithms=[settings.JWT_ALGORITHM]
#     )


# def get_current_vendor(
#     credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
# ) -> dict:
#     try:
#         return decode_token(credentials.credentials)
#     except JWTError:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid or expired token"
#         )

# # import os
# # from datetime import datetime, timedelta, timezone
# # from passlib.context import CryptContext
# # from jose import JWTError, jwt

# # pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# # JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change")
# # JWT_ALGO = os.getenv("JWT_ALGO", "HS256")
# # JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MIN", "60"))

# # def hash_password(password: str) -> str:
# #     return pwd_context.hash(password)

# # def verify_password(password: str, password_hash: str) -> bool:
# #     return pwd_context.verify(password, password_hash)

# # def create_access_token(payload: dict) -> str:
# #     now = datetime.now(timezone.utc)
# #     exp = now + timedelta(minutes=JWT_EXPIRE_MIN)
# #     to_encode = {**payload, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
# #     return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGO)


# # # from datetime import datetime, timedelta
# # # from jose import JWTError, jwt
# # # from passlib.context import CryptContext
# # # from fastapi import Depends, HTTPException, status
# # # from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
# # # from app.core.config import settings

# # # pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
# # # bearer_scheme = HTTPBearer()


# # # def hash_password(password: str) -> str:
# # #     return pwd_context.hash(password)


# # # def verify_password(plain: str, hashed: str) -> bool:
# # #     return pwd_context.verify(plain, hashed)


# # # def create_access_token(vendor_id: int, vendor_account: str) -> str:
# # #     expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
# # #     return jwt.encode(
# # #         {"sub": str(vendor_id), "account": vendor_account, "exp": expire},
# # #         settings.JWT_SECRET_KEY,
# # #         algorithm=settings.JWT_ALGORITHM
# # #     )


# # # def get_current_vendor(
# # #     credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
# # # ) -> dict:
# # #     try:
# # #         payload = jwt.decode(
# # #             credentials.credentials,
# # #             settings.JWT_SECRET_KEY,
# # #             algorithms=[settings.JWT_ALGORITHM]
# # #         )
# # #         return payload
# # #     except JWTError:
# # #         raise HTTPException(
# # #             status_code=status.HTTP_401_UNAUTHORIZED,
# # #             detail="Invalid or expired token"
# # #         )