"""
Main API v1 router that aggregates all sub-routers.

This module wires together every feature-specific router under the
``/api/v1`` prefix.  Import ``api_v1_router`` and include it in the
FastAPI application during startup.

Usage::

    from app.api.v1.router import api_v1_router
    app.include_router(api_v1_router, prefix="/api/v1")
"""

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.events import router as events_router
from app.api.v1.anomalies import router as anomalies_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.alerts import router as alerts_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cameras import router as cameras_router
from app.api.v1.faces import router as faces_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.recordings import router as recordings_router
from app.api.v1.reports import router as reports_router
from app.api.v1.rules import router as rules_router
from app.api.v1.vehicles import router as vehicles_router
from app.api.v1.websocket import router as websocket_router
from app.api.v1.reid import router as reid_router
from app.api.v1.incident_reports import router as incident_reports_router
from app.api.v1.search import router as search_router
from app.api.v1.zones import router as zones_router
from app.api.v1.copilot import router as copilot_router
from app.api.v1.predictions import router as predictions_router
from app.api.v1.federation import router as federation_router
from app.api.v1.edge import router as edge_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.departments import router as departments_router
from app.api.v1.floor_plans import router as floor_plans_router

api_v1_router = APIRouter()

api_v1_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["Authentication"],
)
api_v1_router.include_router(
    cameras_router,
    prefix="/cameras",
    tags=["Cameras"],
)
api_v1_router.include_router(
    zones_router,
    prefix="/zones",
    tags=["Zones"],
)
api_v1_router.include_router(
    rules_router,
    prefix="/rules",
    tags=["Rules"],
)
api_v1_router.include_router(
    alerts_router,
    prefix="/alerts",
    tags=["Alerts"],
)
api_v1_router.include_router(
    faces_router,
    prefix="/faces",
    tags=["Face Recognition"],
)
api_v1_router.include_router(
    vehicles_router,
    prefix="/vehicles",
    tags=["Vehicles"],
)
api_v1_router.include_router(
    analytics_router,
    prefix="/analytics",
    tags=["Analytics"],
)
api_v1_router.include_router(
    recordings_router,
    prefix="/recordings",
    tags=["Recordings"],
)
api_v1_router.include_router(
    reports_router,
    prefix="/reports",
    tags=["Reports"],
)
api_v1_router.include_router(
    notifications_router,
    prefix="/notifications",
    tags=["Notifications"],
)
api_v1_router.include_router(
    admin_router,
    prefix="/admin",
    tags=["Administration"],
)
api_v1_router.include_router(
    anomalies_router,
    prefix="/anomalies",
    tags=["Anomaly Detection"],
)
api_v1_router.include_router(
    integrations_router,
    prefix="/integrations",
    tags=["Integrations"],
)
api_v1_router.include_router(
    reid_router,
    prefix="/reid",
    tags=["Person Re-Identification"],
)
api_v1_router.include_router(
    search_router,
    prefix="/search",
    tags=["CLIP Video Search"],
)
api_v1_router.include_router(
    incident_reports_router,
    prefix="/incident-reports",
    tags=["Incident Reports"],
)
api_v1_router.include_router(
    copilot_router,
    prefix="/copilot",
    tags=["AI Copilot"],
)
api_v1_router.include_router(
    predictions_router,
    prefix="/predictions",
    tags=["Predictive Analytics"],
)
api_v1_router.include_router(
    federation_router,
    prefix="/federation",
    tags=["Multi-Site Federation"],
)
api_v1_router.include_router(
    edge_router,
    prefix="/edge",
    tags=["Edge Devices"],
)
api_v1_router.include_router(
    webhooks_router,
    prefix="/webhooks",
    tags=["Webhooks"],
)
api_v1_router.include_router(
    dashboard_router,
    prefix="/dashboard",
    tags=["Dashboard"],
)
api_v1_router.include_router(
    events_router,
    prefix="/events",
    tags=["Events"],
)
api_v1_router.include_router(
    departments_router,
    prefix="/departments",
    tags=["Departments"],
)
api_v1_router.include_router(
    floor_plans_router,
    prefix="/floor-plans",
    tags=["Floor Plans"],
)
api_v1_router.include_router(
    websocket_router,
    prefix="/ws",
    tags=["WebSocket"],
)
