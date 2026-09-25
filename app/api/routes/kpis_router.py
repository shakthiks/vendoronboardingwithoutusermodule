from fastapi import APIRouter

from app.services.createprospect.kpis_createprospect import get_dashboard_kpis

router = APIRouter(
    prefix="/kpi",
    tags=["KPI"]
)


@router.get("/dashboard")
def dashboard_kpis():
    return get_dashboard_kpis()