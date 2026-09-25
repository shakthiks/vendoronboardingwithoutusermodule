from fastapi import APIRouter

from app.schemas.prospect_schema import ProspectInvitationSchema
from app.services.createprospect.createprospect_service import send_invitation

router = APIRouter()


router=APIRouter(prefix="/Createprospect",tags=["Onboarding"])

@router.post("/createprospect/send_invitation")
def send_invitation_api(payload: ProspectInvitationSchema):
    return send_invitation(payload)