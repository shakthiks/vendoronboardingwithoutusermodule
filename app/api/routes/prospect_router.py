from fastapi import APIRouter, HTTPException
from app.services.prospectlistpage import get_prospect_list

router = APIRouter(
    prefix="/prospect",
    tags=["prospect"]
)


@router.get("/list")
async def prospect_list():
    try:
        return await get_prospect_list()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )