from fastapi import APIRouter, Depends

from app.db.base import get_secondary_connection
from app.services.vendgroupdropdown_service import get_vendor_group_dropdown

router = APIRouter(
    prefix="/vendor-groups",
    tags=["Vendor Groups"]
)

@router.get("/")
def vendor_group_dropdown():
    with get_secondary_connection() as conn:
        return get_vendor_group_dropdown(conn)