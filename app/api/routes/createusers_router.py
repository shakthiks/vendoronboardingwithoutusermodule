from fastapi import APIRouter
from app.schemas.createusers_schema import UserCreate
from app.services.createusers_service import create_users

router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


@router.post("/create")
def create_update_user(payload: UserCreate):
    return create_users(payload)