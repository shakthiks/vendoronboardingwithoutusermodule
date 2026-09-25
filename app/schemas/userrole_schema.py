from pydantic import BaseModel
from typing import Optional

class AssignRoleRequest(BaseModel):
    UserId: int
    RoleId: int
    AssignedBy: int