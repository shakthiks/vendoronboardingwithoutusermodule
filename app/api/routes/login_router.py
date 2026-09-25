from fastapi import APIRouter
from app.schemas.login_schema import LoginRequest
from app.services.loginapi_service import login_user

router = APIRouter()



router = APIRouter(
    prefix="/login",
    tags=["Login"]
)

@router.post("/login")
def login(payload: LoginRequest):
    return login_user(payload)