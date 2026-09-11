"""VisionAI SQLAlchemy models package.

All models are imported here so that Alembic's ``target_metadata = Base.metadata``
picks up every table when generating migrations. Import this package (or any
individual model) to ensure the full schema is registered with the Base.
"""

# ── Core entities ──────────────────────────────────────────────────────
from app.models.organization import Organization, SubscriptionTier  # noqa: F401
from app.models.user import APIKey, User, UserRole  # noqa: F401

# ── Camera subsystem ──────────────────────────────────────────────────
from app.models.camera import (  # noqa: F401
    Camera,
    CameraGroup,
    CameraHealth,
    CameraHealthStatus,
    RecordingMode,
    StreamProtocol,
    camera_group_members,
)
from app.models.zone import Zone, ZoneType  # noqa: F401

# ── Rules & alerts ────────────────────────────────────────────────────
from app.models.rule import Rule, RuleSeverity, RuleType  # noqa: F401
from app.models.alert import Alert, AlertEscalation, AlertStatus  # noqa: F401

# ── People & face recognition ────────────────────────────────────────
from app.models.person import Person, PersonType  # noqa: F401
from app.models.face import FaceEnrollment, FaceEvent  # noqa: F401

# ── Vehicles & ANPR ──────────────────────────────────────────────────
from app.models.vehicle import (  # noqa: F401
    Vehicle,
    VehicleCategory,
    VehicleDirection,
    VehicleEvent,
    VehicleLog,
)

# ── Attendance ────────────────────────────────────────────────────────
from app.models.attendance import AttendanceLog, AttendanceStatus  # noqa: F401

# ── Analytics ─────────────────────────────────────────────────────────
from app.models.analytics import (  # noqa: F401
    AggregationPeriod,
    DwellRecord,
    FootfallRecord,
    HeatmapRecord,
)

# ── Anomaly Detection ────────────────────────────────────────────────
from app.models.anomaly import (  # noqa: F401
    AnomalyBaseline,
    AnomalyEvent as AnomalyEventModel,
    AnomalySeverity,
    AnomalyType,
)

# ── Recordings ────────────────────────────────────────────────────────
from app.models.recording import (  # noqa: F401
    Recording,
    RecordingSegment,
    RecordingType,
)

# ── CLIP Search ──────────────────────────────────────────────────────
from app.models.clip_search import FrameEmbedding, SearchQuery  # noqa: F401

# ── Incident Reports ─────────────────────────────────────────────────
from app.models.incident_report import (  # noqa: F401
    IncidentReport,
    ReportStatus,
    ReportTemplate,
    ReportType,
)

# ── Predictions & Scheduling ─────────────────────────────────────────
from app.models.prediction import (  # noqa: F401
    Prediction,
    PredictionType,
    RiskForecast,
    RiskLevel,
    StaffSchedule,
)

# ── Multi-Site Federation ────────────────────────────────────────────
from app.models.federation import (  # noqa: F401
    FederatedAlert,
    Site,
    SiteSync,
    SyncStatus,
    SyncType,
)

# ── Floor Plan / Digital Twin ────────────────────────────────────────
from app.models.floor_plan import (  # noqa: F401
    CameraPlacement,
    FloorPlan,
    FloorPlanOverlay,
    OverlayType,
    ZonePlacement,
)

# ── Integrations & Webhooks ──────────────────────────────────────────
from app.models.integration import (  # noqa: F401
    DeliveryStatus,
    EventType,
    IntegrationConfig,
    IntegrationType,
    WebhookDelivery,
    WebhookEndpoint,
)

# ── Edge Devices ────────────────────────────────────────────────────
from app.models.edge_device import (  # noqa: F401
    DeploymentStatus,
    EdgeDeployment,
    EdgeDevice,
    EdgeDeviceType,
    EdgeMetrics,
    ModelFormat,
)

# ── Standalone Webhooks (v2) ───────────────────────────────────────
from app.models.webhook import (  # noqa: F401
    WebhookDeliveryStatus,
    WebhookDeliveryV2,
    WebhookEndpointV2,
    WebhookEventType,
)

__all__ = [
    # Organization
    "Organization",
    "SubscriptionTier",
    # User & Auth
    "User",
    "UserRole",
    "APIKey",
    # Camera
    "Camera",
    "CameraGroup",
    "CameraHealth",
    "CameraHealthStatus",
    "StreamProtocol",
    "RecordingMode",
    "camera_group_members",
    # Zone
    "Zone",
    "ZoneType",
    # Rule
    "Rule",
    "RuleType",
    "RuleSeverity",
    # Alert
    "Alert",
    "AlertEscalation",
    "AlertStatus",
    # Person
    "Person",
    "PersonType",
    # Face
    "FaceEnrollment",
    "FaceEvent",
    # Vehicle
    "Vehicle",
    "VehicleCategory",
    "VehicleDirection",
    "VehicleEvent",
    "VehicleLog",
    # Attendance
    "AttendanceLog",
    "AttendanceStatus",
    # Analytics
    "FootfallRecord",
    "DwellRecord",
    "HeatmapRecord",
    "AggregationPeriod",
    # Anomaly Detection
    "AnomalyEventModel",
    "AnomalyBaseline",
    "AnomalyType",
    "AnomalySeverity",
    # Recording
    "Recording",
    "RecordingSegment",
    "RecordingType",
    # CLIP Search
    "FrameEmbedding",
    "SearchQuery",
    # Incident Reports
    "IncidentReport",
    "ReportTemplate",
    "ReportType",
    "ReportStatus",
    # Predictions & Scheduling
    "Prediction",
    "PredictionType",
    "RiskForecast",
    "RiskLevel",
    "StaffSchedule",
    # Multi-Site Federation
    "Site",
    "SiteSync",
    "FederatedAlert",
    "SyncType",
    "SyncStatus",
    # Integration & Webhooks
    "WebhookEndpoint",
    "WebhookDelivery",
    "IntegrationConfig",
    "EventType",
    "DeliveryStatus",
    "IntegrationType",
    # Floor Plan / Digital Twin
    "FloorPlan",
    "CameraPlacement",
    "ZonePlacement",
    "FloorPlanOverlay",
    "OverlayType",
    # Edge Devices
    "EdgeDevice",
    "EdgeDeployment",
    "EdgeMetrics",
    "EdgeDeviceType",
    "DeploymentStatus",
    "ModelFormat",
    # Standalone Webhooks (v2)
    "WebhookEndpointV2",
    "WebhookDeliveryV2",
    "WebhookDeliveryStatus",
    "WebhookEventType",
]
