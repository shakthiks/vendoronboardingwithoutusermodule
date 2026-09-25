from fastapi import APIRouter,HTTPException
from app.services.approvalreview import approval_listpage,approvalreview_update
from app.schemas.approvalreview_schemas import ApprovalListPageRequest,ApprovalReviewUpdateRequest

router = APIRouter(prefix="/review",tags=["ApprovalReview"])

@router.post("/approvalist")
def approval_listpage_route(payload:ApprovalListPageRequest):
    result = approval_listpage(payload.ProspectId)
    return result


@router.post("/approvalreviewupdate")
def approvalreviewupdate_route(payload:ApprovalReviewUpdateRequest):
    result = approvalreview_update(payload)
    return result
