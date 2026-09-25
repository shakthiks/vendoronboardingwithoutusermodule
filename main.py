from fastapi import APIRouter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware 
from app.api.routes.vendordetail_routes import router as vendor_detail
from app.api.routes.vendorlist_routes import router as vendorrouter
from app.api.routes.materilas_routes import router as materialrouter
from app.api.routes.rfqcase_routes import router as rfqcase_routes
from app.api.routes.onbid_routes import router as onbid_routes
from app.api.routes.underreview_routes import router as underreview_routes
from app.api.routes.closedrfq_routes import router as closed_routes
from app.api.routes.po_allrouter import router as po_allrouter
from app.api.routes.bidpo_routes import router as bidpo_routes
from app.api.routes.directpo_routes import router as directpo_routes
from app.api.routes.invite_routes import router as invite_routes
from app.api.routes.dashbaord_routes import router as das_routes
from app.api.routes.vendor_dashkpiroutes import router as vednor_kpiroutes
from app.api.routes.auth_router import router as auth_router 
from app.api.routes.historyrfq_router import router as history_router 
from app.api.routes.expiry_routes import router as expiry_router

from app.api.routes.createprospect_router import router as createprospect_router
from app.api.routes.createroles_router import router as createroles_router
from app.api.routes.createusers_router import router as createusers_router
from app.api.routes.userrole_router import router as userrole_router

from app.api.routes.prospect_router import router as prospect_router
from app.api.routes.login_router import router as login_router
from app.api.routes.vendoronbaordingform_router import router as vendoronbaordingform_router
from app.api.routes.attachment_router import router as attachment_router
from app.api.routes.kpis_router import router as kpis_router
from app.api.routes.vendorapproval_router import router as vendorapproval_router
from app.api.routes.approvalreview_router import router as approvalreview_router
from app.api.routes.riskassesment_router import router as riskassesment_router
from app.api.routes.vendgroupdropdown_router import router as vendgroupdropdown_router
from app.api.routes.term_router import router as term_router
from app.api.routes.termslastest_router import router as termlastest_router
app=FastAPI(title="Welcome to vendor Admin portal")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(vendorrouter)
app.include_router(vendor_detail)
app.include_router(materialrouter)
app.include_router(rfqcase_routes)
app.include_router(onbid_routes)
app.include_router(underreview_routes)
app.include_router(closed_routes)
app.include_router(po_allrouter)
app.include_router(directpo_routes)
app.include_router(bidpo_routes)
app.include_router(invite_routes)
app.include_router(das_routes)
app.include_router(vednor_kpiroutes)
app.include_router(auth_router)
app.include_router(history_router)
app.include_router(expiry_router)

app.include_router(createroles_router)
app.include_router(createprospect_router)
app.include_router(createusers_router)
app.include_router(userrole_router)

app.include_router(prospect_router) 
app.include_router(login_router)
app.include_router(vendoronbaordingform_router)
app.include_router(attachment_router)
app.include_router(kpis_router)
app.include_router(riskassesment_router)
app.include_router(vendorapproval_router)
app.include_router(approvalreview_router)
app.include_router(vendgroupdropdown_router)
app.include_router(term_router)
app.include_router(termlastest_router)
@app.get("/health")
def health():
    return {"status": "Running"}
 
