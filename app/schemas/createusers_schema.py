from typing import Optional
from pydantic import BaseModel, EmailStr, EmailStr

class UserCreate(BaseModel):
    UserId: Optional[int] = None
    Username: str
    Email: str
    hashed_password: str
    FullName: Optional[str] = None
    IsActive: bool = True
    FailedLoginCount: int = 0
    IsLocked: bool = False