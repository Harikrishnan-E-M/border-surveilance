"""
VisionAI Predictive Analytics Celery Tasks.

Handles periodic prediction generation, accuracy evaluation,
risk forecast updates, and staff schedule generation.  All tasks
run asynchronous database operations via the ``_run_async`` helper.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import redis
import structlog
from sqlalchemy import select

from app.config import get_settings
from app.workers.celery_app import celery_app

logger = structlog.stdlib.get_logger(__name__)
settings = get_settings()

_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Task: generate_predictions_task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.prediction_tasks.generate_predictions_task",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="analytics",
)
def generate_predictions_task(self, org_id: str) -> dict[str, Any]:
    """Hourly prediction generation for an organization.

    Generates footfall predictions for all active cameras and alert
    volume predictions.  Called hourly by the Celery beat scheduler.

    Args:
        org_id: UUID of the organization.

    Returns:
        dict summarizing prediction results.
    """
    log = logger.bind(task_id=self.request.id, org_id=org_id)
    log.info("Starting hourly prediction generation")

    try:

        async def _generate():
            from app.database import get_db_context
            from app.models.camera import Camera
            from app.services.predictive_service import PredictiveService

            results = {
                "org_id": org_id,
                "footfall_cameras": 0,
                "alert_prediction": False,
                "errors": [],
            }

            org_uuid = uuid.UUID(org_id)

            async with get_db_context() as session:
                # Get active cameras
                cam_result = await session.execute(
                    select(Camera).where(
                        Camera.org_id == org_uuid,
                        Camera.is_active.is_(True),
                    )
                )
                cameras = cam_result.scalars().all()

                # Generate footfall predictions for each camera
                for camera in cameras:
                    try:
                        await PredictiveService.predict_footfall(
                            session, org_uuid, camera.id, hours_ahead=24
                        )
                        results["footfall_cameras"] += 1
                    except Exception as exc:
                        log.warning(
                            "Footfall prediction failed for camera",
                            camera_id=str(camera.id),
                            error=str(exc),
                        )
                        results["errors"].append({
                            "camera_id": str(camera.id),
                            "type": "footfall",
                            "error": str(exc),
                        })

                # Generate alert predictions
                try:
                    await PredictiveService.predict_alerts(session, org_uuid, hours_ahead=24)
                    results["alert_prediction"] = True
                except Exception as exc:
                    log.warning("Alert prediction failed", error=str(exc))
                    results["errors"].append({
                        "type": "alerts",
                        "error": str(exc),
                    })

            return results

        result = _run_async(_generate())

        # Cache result summary in Redis
        _redis.set(
            f"visionai:predictions:last_run:{org_id}",
            json.dumps({
                "status": "completed",
                "footfall_cameras": result["footfall_cameras"],
                "alert_prediction": result["alert_prediction"],
                "errors_count": len(result["errors"]),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }),
            ex=7200,
        )

        log.info(
            "Hourly prediction generation completed",
            footfall_cameras=result["footfall_cameras"],
            alert_prediction=result["alert_prediction"],
            errors=len(result["errors"]),
        )

        return result

    except Exception as exc:
        log.error("Prediction generation failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: evaluate_prediction_accuracy
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.prediction_tasks.evaluate_prediction_accuracy",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="analytics",
)
def evaluate_prediction_accuracy(self, org_id: str) -> dict[str, Any]:
    """Compare past predictions with actual observed values.

    Back-fills the ``actual_value`` column for predictions whose
    target timestamp has passed, enabling MAPE computation.

    Args:
        org_id: UUID of the organization.

    Returns:
        dict with the number of predictions updated.
    """
    log = logger.bind(task_id=self.request.id, org_id=org_id)
    log.info("Starting prediction accuracy evaluation")

    try:

        async def _evaluate():
            from app.database import get_db_context
            from app.services.predictive_service import PredictiveService

            async with get_db_context() as session:
                result = await PredictiveService.evaluate_prediction_accuracy(
                    session, uuid.UUID(org_id)
                )
                return result

        result = _run_async(_evaluate())

        # Cache accuracy summary
        _redis.set(
            f"visionai:predictions:accuracy_eval:{org_id}",
            json.dumps({
                "predictions_updated": result.get("predictions_updated", 0),
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            }),
            ex=7200,
        )

        log.info(
            "Prediction accuracy evaluation completed",
            predictions_updated=result.get("predictions_updated", 0),
        )

        return result

    except Exception as exc:
        log.error("Prediction accuracy evaluation failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: generate_risk_forecast_task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.prediction_tasks.generate_risk_forecast_task",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    queue="analytics",
)
def generate_risk_forecast_task(self, org_id: str) -> dict[str, Any]:
    """Daily risk forecast update for an organization.

    Recomputes risk scores for all cameras based on recent alerts,
    anomaly events, and camera health.

    Args:
        org_id: UUID of the organization.

    Returns:
        dict with forecast summary.
    """
    log = logger.bind(task_id=self.request.id, org_id=org_id)
    log.info("Starting risk forecast generation")

    try:

        async def _generate():
            from app.database import get_db_context
            from app.services.predictive_service import PredictiveService

            async with get_db_context() as session:
                result = await PredictiveService.get_risk_forecast(
                    session, uuid.UUID(org_id)
                )
                return result

        result = _run_async(_generate())

        # Cache risk forecast summary
        _redis.set(
            f"visionai:predictions:risk_forecast:{org_id}",
            json.dumps({
                "overall_risk_level": result.get("overall_risk_level", "low"),
                "overall_risk_score": result.get("overall_risk_score", 0),
                "cameras_assessed": len(result.get("forecasts", [])),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }),
            ex=86400,  # 24h cache
        )

        log.info(
            "Risk forecast generation completed",
            overall_level=result.get("overall_risk_level"),
            cameras=len(result.get("forecasts", [])),
        )

        return {
            "org_id": org_id,
            "status": "completed",
            "overall_risk_level": result.get("overall_risk_level", "low"),
            "overall_risk_score": result.get("overall_risk_score", 0),
            "cameras_assessed": len(result.get("forecasts", [])),
        }

    except Exception as exc:
        log.error("Risk forecast generation failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: generate_staff_schedule_task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.workers.prediction_tasks.generate_staff_schedule_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="analytics",
)
def generate_staff_schedule_task(
    self, org_id: str, target_date: str
) -> dict[str, Any]:
    """Generate an optimal staff schedule for a specific date.

    Analyses historical alert and footfall patterns for the target
    day-of-week and produces hourly staffing recommendations.

    Args:
        org_id: UUID of the organization.
        target_date: ISO-8601 date string (YYYY-MM-DD).

    Returns:
        dict with schedule summary.
    """
    log = logger.bind(task_id=self.request.id, org_id=org_id, date=target_date)
    log.info("Starting staff schedule generation")

    try:
        parsed_date = date.fromisoformat(target_date)

        async def _generate():
            from app.database import get_db_context
            from app.services.predictive_service import PredictiveService

            async with get_db_context() as session:
                result = await PredictiveService.generate_staff_schedule(
                    session, uuid.UUID(org_id), parsed_date
                )
                return result

        result = _run_async(_generate())

        # Cache schedule summary
        _redis.set(
            f"visionai:predictions:staff_schedule:{org_id}:{target_date}",
            json.dumps({
                "total_staff_hours": result.get("total_staff_hours", 0),
                "peak_hour": result.get("peak_hour"),
                "peak_staff": result.get("peak_staff"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }),
            ex=86400,
        )

        log.info(
            "Staff schedule generation completed",
            total_hours=result.get("total_staff_hours"),
            peak_hour=result.get("peak_hour"),
        )

        return {
            "org_id": org_id,
            "date": target_date,
            "status": "completed",
            "total_staff_hours": result.get("total_staff_hours", 0),
            "peak_hour": result.get("peak_hour"),
            "peak_staff": result.get("peak_staff"),
        }

    except Exception as exc:
        log.error("Staff schedule generation failed", error=str(exc))
        raise self.retry(exc=exc)
