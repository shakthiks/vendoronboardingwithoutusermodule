from pydantic import BaseModel

class RoleCreate(BaseModel):
    RoleName: str
    RoleDescription: str
    IsActive: bool = True