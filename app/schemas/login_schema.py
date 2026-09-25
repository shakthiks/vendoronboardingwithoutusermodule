from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    Email: str
    Password: str