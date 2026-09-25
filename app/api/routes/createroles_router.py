from fastapi import APIRouter
from app.schemas.createroles_schema import RoleCreate
from app.services.createroles_service import create_role

router = APIRouter(
    prefix="/roles",
    tags=["Roles"]
)

@router.post("/create")
def create_role_api(payload: RoleCreate):
    return create_role(payload)