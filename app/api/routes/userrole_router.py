from fastapi import APIRouter
from app.schemas.userrole_schema import AssignRoleRequest
from app.services.userrole_service import assign_role

router = APIRouter(
    prefix="/user-role",
    tags=["User Role"]
)


@router.post("/assign")
def assign_user_role(payload: AssignRoleRequest):
    return assign_role(payload)