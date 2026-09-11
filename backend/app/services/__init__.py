"""VisionAI services package.

All business logic is implemented in service modules that sit between
the API route layer and the database/model layer. Import individual
service modules as needed::

    from app.services import camera_service, face_service
    from app.services.stream_manager import get_stream_manager
    from app.services.rule_engine import RuleEngine
"""

from app.services import (  # noqa: F401
    alert_dispatcher,
    analytics_service,
    attendance_service,
    auth_service,
    camera_service,
    face_service,
    notification_service,
    recording_service,
    report_service,
    vehicle_service,
    zone_service,
)
from app.services.rule_engine import RuleEngine  # noqa: F401
from app.services.stream_manager import (  # noqa: F401
    CameraStreamWorker,
    StreamManager,
    get_stream_manager,
)

__all__ = [
    # Auth
    "auth_service",
    # Camera
    "camera_service",
    # Stream
    "CameraStreamWorker",
    "StreamManager",
    "get_stream_manager",
    # Zone
    "zone_service",
    # Rules
    "RuleEngine",
    # Alerts
    "alert_dispatcher",
    # Face
    "face_service",
    # Vehicle
    "vehicle_service",
    # Attendance
    "attendance_service",
    # Analytics
    "analytics_service",
    # Recording
    "recording_service",
    # Reports
    "report_service",
    # Notifications
    "notification_service",
]
