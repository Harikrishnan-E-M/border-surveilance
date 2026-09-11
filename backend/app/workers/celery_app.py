"""
VisionAI Celery Application Configuration.

Defines the Celery application instance, task routing, beat schedule,
retry policies, and worker concurrency settings. All configuration
values are sourced from the central Settings object.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab, schedule

from app.config import get_settings

settings = get_settings()

# ── Create the Celery application ────────────────────────────────────────────

celery_app = Celery(
    "visionai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# ── Serialization ────────────────────────────────────────────────────────────

celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True

# ── Result backend settings ──────────────────────────────────────────────────

celery_app.conf.result_expires = 3600  # Results expire after 1 hour
celery_app.conf.result_backend_transport_options = {
    "global_keyprefix": "visionai:",
    "result_chord_ordered": True,
}

# ── Task discovery ───────────────────────────────────────────────────────────

celery_app.conf.include = [
    "app.workers.video_tasks",
    "app.workers.alert_tasks",
    "app.workers.analytics_tasks",
    "app.workers.recording_tasks",
    "app.workers.report_tasks",
    "app.workers.incident_report_tasks",
    "app.workers.maintenance_tasks",
    "app.workers.webhook_tasks",
    "app.workers.anomaly_tasks",
    "app.workers.clip_tasks",
    "app.workers.prediction_tasks",
    "app.workers.federation_tasks",
    "app.workers.edge_tasks",
    "app.workers.reid_tasks",
]

# ── Task routing ─────────────────────────────────────────────────────────────
# Route task types to dedicated queues so that slow video processing does not
# starve lightweight alert dispatch or analytics aggregation.

celery_app.conf.task_routes = {
    # Video processing queue (heavy, long-running)
    "app.workers.video_tasks.*": {"queue": "video"},
    # Alert dispatch queue (latency-sensitive)
    "app.workers.alert_tasks.*": {"queue": "alerts"},
    # Analytics aggregation queue
    "app.workers.analytics_tasks.*": {"queue": "analytics"},
    # Recording management queue
    "app.workers.recording_tasks.*": {"queue": "video"},
    # Report generation queue
    "app.workers.report_tasks.*": {"queue": "reports"},
    # Incident report generation queue
    "app.workers.incident_report_tasks.*": {"queue": "reports"},
    # Maintenance tasks queue
    "app.workers.maintenance_tasks.*": {"queue": "maintenance"},
    # Webhook & integration tasks queue
    "app.workers.webhook_tasks.*": {"queue": "alerts"},
    # Anomaly detection tasks queue
    "app.workers.anomaly_tasks.*": {"queue": "analytics"},
    # CLIP indexing tasks queue (heavy, long-running)
    "app.workers.clip_tasks.*": {"queue": "video"},
    # Prediction tasks queue
    "app.workers.prediction_tasks.*": {"queue": "analytics"},
    # Federation tasks queue
    "app.workers.federation_tasks.*": {"queue": "federation"},
    # Edge device tasks queue
    "app.workers.edge_tasks.*": {"queue": "edge"},
    # ReID tasks queue (heavy, long-running)
    "app.workers.reid_tasks.*": {"queue": "video"},
}

celery_app.conf.task_default_queue = "default"
celery_app.conf.task_default_exchange = "visionai"
celery_app.conf.task_default_routing_key = "default"

# ── Task queues declaration ──────────────────────────────────────────────────

from kombu import Exchange, Queue

default_exchange = Exchange("visionai", type="direct")

celery_app.conf.task_queues = (
    Queue("default", default_exchange, routing_key="default"),
    Queue("video", default_exchange, routing_key="video"),
    Queue("alerts", default_exchange, routing_key="alerts"),
    Queue("analytics", default_exchange, routing_key="analytics"),
    Queue("reports", default_exchange, routing_key="reports"),
    Queue("maintenance", default_exchange, routing_key="maintenance"),
    Queue("federation", default_exchange, routing_key="federation"),
    Queue("edge", default_exchange, routing_key="edge"),
)

# ── Celery Beat schedule ─────────────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    "analytics-aggregation-hourly": {
        "task": "app.workers.analytics_tasks.aggregate_footfall_hourly",
        "schedule": crontab(minute=0),  # Every hour at minute 0
        "options": {"queue": "analytics"},
    },
    "health-check-periodic": {
        "task": "app.workers.maintenance_tasks.check_camera_health",
        "schedule": 30.0,  # Every 30 seconds
        "options": {"queue": "maintenance"},
    },
    "retention-cleanup-daily": {
        "task": "app.workers.maintenance_tasks.cleanup_old_events",
        "schedule": crontab(hour=2, minute=0),  # Daily at 2:00 AM UTC
        "args": (90,),
        "options": {"queue": "maintenance"},
    },
    "scheduled-reports-daily": {
        "task": "app.workers.report_tasks.generate_scheduled_reports",
        "schedule": crontab(hour=6, minute=0),  # Daily at 6:00 AM UTC
        "options": {"queue": "reports"},
    },
    "restart-failed-streams": {
        "task": "app.workers.video_tasks.restart_failed_streams",
        "schedule": 60.0,  # Every 60 seconds
        "options": {"queue": "video"},
    },
    "update-analytics-cache": {
        "task": "app.workers.maintenance_tasks.update_analytics_cache",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
        "options": {"queue": "maintenance"},
    },
    "cleanup-temp-files": {
        "task": "app.workers.maintenance_tasks.cleanup_temp_files",
        "schedule": crontab(hour="*/6", minute=30),  # Every 6 hours at :30
        "options": {"queue": "maintenance"},
    },
    "attendance-aggregation-daily": {
        "task": "app.workers.analytics_tasks.aggregate_attendance_daily",
        "schedule": crontab(hour=0, minute=15),  # Daily at 00:15 UTC
        "options": {"queue": "analytics"},
    },
    "database-vacuum-weekly": {
        "task": "app.workers.maintenance_tasks.database_vacuum",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),  # Sunday 3 AM
        "options": {"queue": "maintenance"},
    },
    "anomaly-detection-periodic": {
        "task": "app.workers.anomaly_tasks.run_periodic_anomaly_check",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
        "options": {"queue": "analytics"},
    },
    "anomaly-cleanup-daily": {
        "task": "app.workers.anomaly_tasks.cleanup_old_anomalies",
        "schedule": crontab(hour=3, minute=30),  # Daily at 3:30 AM UTC
        "args": (90,),
        "options": {"queue": "maintenance"},
    },
    "retry-failed-webhooks": {
        "task": "app.workers.webhook_tasks.retry_failed_webhooks",
        "schedule": 60.0,  # Every 60 seconds
        "options": {"queue": "alerts"},
    },
    "cleanup-old-webhook-deliveries": {
        "task": "app.workers.webhook_tasks.cleanup_old_deliveries",
        "schedule": crontab(hour=3, minute=30),  # Daily at 3:30 AM UTC
        "args": (30,),
        "options": {"queue": "maintenance"},
    },
    "clip-orphan-cleanup-daily": {
        "task": "app.workers.clip_tasks.cleanup_orphaned_embeddings",
        "schedule": crontab(hour=4, minute=0),  # Daily at 4:00 AM UTC
        "options": {"queue": "maintenance"},
    },
    "incident-daily-summary": {
        "task": "incident_reports.scheduled_daily_summaries",
        "schedule": crontab(hour=6, minute=0),  # Daily at 6:00 AM UTC
        "options": {"queue": "reports"},
    },
    "incident-weekly-report": {
        "task": "incident_reports.scheduled_weekly_reports",
        "schedule": crontab(hour=7, minute=0, day_of_week=1),  # Monday 7:00 AM UTC
        "options": {"queue": "reports"},
    },
    "incident-report-cleanup": {
        "task": "incident_reports.cleanup_old_reports",
        "schedule": crontab(hour=4, minute=30),  # Daily at 4:30 AM UTC
        "args": (180,),
        "options": {"queue": "maintenance"},
    },
    "prediction-accuracy-evaluation": {
        "task": "app.workers.prediction_tasks.evaluate_prediction_accuracy",
        "schedule": crontab(minute=30),  # Every hour at :30
        "options": {"queue": "analytics"},
    },
    "risk-forecast-daily": {
        "task": "app.workers.prediction_tasks.generate_risk_forecast_task",
        "schedule": crontab(hour=5, minute=0),  # Daily at 5:00 AM UTC
        "options": {"queue": "analytics"},
    },
    "federation-heartbeat-all": {
        "task": "app.workers.federation_tasks.heartbeat_all_sites",
        "schedule": 60.0,  # Every 60 seconds
        "options": {"queue": "federation"},
    },
    "federation-sync-all": {
        "task": "app.workers.federation_tasks.sync_all_sites",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
        "options": {"queue": "federation"},
    },
    "federation-mark-stale-offline": {
        "task": "app.workers.federation_tasks.mark_stale_sites_offline",
        "schedule": 120.0,  # Every 2 minutes
        "args": (5,),
        "options": {"queue": "federation"},
    },
    # ── Edge device tasks ─────────────────────────────────────────────
    "edge-health-check-periodic": {
        "task": "app.workers.edge_tasks.check_edge_health_task",
        "schedule": 30.0,  # Every 30 seconds
        "options": {"queue": "edge"},
    },
    "edge-metrics-cleanup-daily": {
        "task": "app.workers.edge_tasks.cleanup_old_metrics",
        "schedule": crontab(hour=3, minute=15),  # Daily at 3:15 AM UTC
        "args": (7,),
        "options": {"queue": "edge"},
    },
    # ── ReID tasks ────────────────────────────────────────────────────
    "reid-gallery-rebuild-hourly": {
        "task": "app.workers.reid_tasks.build_reid_gallery_task",
        "schedule": crontab(minute=15),  # Every hour at :15
        "options": {"queue": "video"},
    },
    "reid-stale-tracks-cleanup": {
        "task": "app.workers.reid_tasks.cleanup_stale_tracks",
        "schedule": crontab(hour="*/4", minute=45),  # Every 4 hours at :45
        "args": (24,),
        "options": {"queue": "maintenance"},
    },
}

# ── Task retry policy defaults ───────────────────────────────────────────────

celery_app.conf.task_annotations = {
    "*": {
        "rate_limit": "100/m",
        "max_retries": 3,
        "default_retry_delay": 60,
    },
    "app.workers.alert_tasks.*": {
        "rate_limit": "200/m",
        "max_retries": 5,
        "default_retry_delay": 10,
    },
    "app.workers.video_tasks.*": {
        "rate_limit": "20/m",
        "max_retries": 3,
        "default_retry_delay": 30,
    },
}

celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.task_acks_on_failure_or_timeout = True

# ── Worker concurrency settings ──────────────────────────────────────────────

celery_app.conf.worker_concurrency = 4
celery_app.conf.worker_prefetch_multiplier = 2
celery_app.conf.worker_max_tasks_per_child = 500
celery_app.conf.worker_max_memory_per_child = 512_000  # 512 MB in KB

# ── Broker settings ──────────────────────────────────────────────────────────

celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.broker_connection_retry = True
celery_app.conf.broker_connection_max_retries = 10
celery_app.conf.broker_pool_limit = 10
celery_app.conf.broker_transport_options = {
    "visibility_timeout": 3600,  # 1 hour
    "queue_order_strategy": "priority",
}

# ── Task execution settings ──────────────────────────────────────────────────

celery_app.conf.task_soft_time_limit = 300   # 5 minutes soft limit
celery_app.conf.task_time_limit = 600        # 10 minutes hard limit
celery_app.conf.task_track_started = True
celery_app.conf.task_send_sent_event = True
