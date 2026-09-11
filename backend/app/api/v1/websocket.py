"""
WebSocket API endpoints.

Provides real-time streaming of alerts, detection overlay data,
and system health metrics via WebSocket connections authenticated
by JWT tokens passed as query parameters.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.user import User

logger = structlog.stdlib.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# WebSocket Authentication
# ---------------------------------------------------------------------------


async def _authenticate_ws(token: str | None) -> dict | None:
    """Authenticate a WebSocket connection using a JWT token.

    Args:
        token: The JWT access token passed as a query parameter.

    Returns:
        dict: The decoded token payload if valid, None otherwise.
    """
    if not token:
        return None

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        token_type = payload.get("token_type") or payload.get("type")
        if token_type != "access":
            return None
        if "sub" not in payload:
            return None

        # Verify user still exists
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(
                    User.id == uuid.UUID(payload["sub"]),
                    User.is_active.is_(True),
                )
            )
            user = result.scalars().first()
            if not user:
                return None

        return payload
    except (JWTError, ValueError, Exception) as exc:
        logger.warning("WebSocket authentication failed", error=str(exc))
        return None


async def _get_redis():
    """Get the Redis connection for pub/sub operations."""
    from app.dependencies import get_redis
    return await get_redis()


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        self.active_connections[channel].append(websocket)
        logger.info("WebSocket connected", channel=channel)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if channel in self.active_connections:
            self.active_connections[channel] = [
                ws for ws in self.active_connections[channel] if ws != websocket
            ]
            if not self.active_connections[channel]:
                del self.active_connections[channel]
        logger.info("WebSocket disconnected", channel=channel)

    async def send_message(self, channel: str, message: dict) -> None:
        """Send a message to all connections on a channel."""
        if channel in self.active_connections:
            dead_connections = []
            for ws in self.active_connections[channel]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead_connections.append(ws)

            # Clean up dead connections
            for ws in dead_connections:
                self.active_connections[channel] = [
                    c for c in self.active_connections[channel] if c != ws
                ]


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Redis Pub/Sub Listener
# ---------------------------------------------------------------------------


async def _subscribe_and_forward(
    websocket: WebSocket,
    channel: str,
    redis_channel: str,
) -> None:
    """Subscribe to a Redis pub/sub channel and forward messages to the WebSocket.

    This coroutine runs in a loop, listening for messages from Redis and
    forwarding them to the connected WebSocket client.

    Args:
        websocket: The connected WebSocket.
        channel: The logical channel name for the connection manager.
        redis_channel: The Redis pub/sub channel to subscribe to.
    """
    try:
        redis = await _get_redis()
        if not redis:
            while True:
                await asyncio.sleep(5.0)
            return
        pubsub = redis.pubsub()
        await pubsub.subscribe(redis_channel)

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await websocket.send_json(data)
                except (json.JSONDecodeError, Exception) as exc:
                    logger.debug("Failed to forward Redis message", error=str(exc))

            # Small yield to prevent blocking
            await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during pub/sub", channel=channel)
    except Exception as exc:
        logger.error("Redis pub/sub error", channel=channel, error=str(exc))
    finally:
        try:
            await pubsub.unsubscribe(redis_channel)
            await pubsub.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /ws/alerts - Real-time alert stream
# ---------------------------------------------------------------------------


@router.websocket("/alerts")
async def ws_alerts(
    websocket: WebSocket,
    token: str | None = Query(None, description="JWT access token"),
) -> None:
    """Real-time alert stream via WebSocket.

    Authenticates via a JWT token passed as a query parameter.
    Subscribes to the organization's alert channel in Redis pub/sub
    and forwards new alerts to the client.

    Query Parameters:
        token: JWT access token for authentication.
    """
    payload = await _authenticate_ws(token)
    if not payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org_id = payload.get("org_id", "unknown")
    channel = f"alerts:{org_id}"
    redis_channel = f"visionai:alerts:{org_id}"

    await manager.connect(channel, websocket)

    # Send initial connection confirmation
    await websocket.send_json({
        "type": "connection",
        "status": "connected",
        "channel": "alerts",
        "org_id": org_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        # Start Redis subscriber in background
        subscribe_task = asyncio.create_task(
            _subscribe_and_forward(websocket, channel, redis_channel)
        )

        # Keep connection alive and handle incoming messages (e.g., pings)
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Handle client messages (e.g., ping/pong, filter updates)
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.info("Alert WebSocket disconnected", org_id=org_id)
    except Exception as exc:
        logger.error("Alert WebSocket error", error=str(exc))
    finally:
        subscribe_task.cancel()
        manager.disconnect(channel, websocket)


# ---------------------------------------------------------------------------
# /ws/detections/{camera_id} - Detection overlay data
# ---------------------------------------------------------------------------


@router.websocket("/detections/{camera_id}")
async def ws_detections(
    websocket: WebSocket,
    camera_id: str,
    token: str | None = Query(None, description="JWT access token"),
) -> None:
    """Detection overlay data stream for a specific camera.

    Streams bounding box coordinates, labels, and confidence scores
    for real-time display on the camera view. Authenticates via JWT
    and verifies the camera belongs to the user's organization.

    Path Parameters:
        camera_id: The UUID of the camera to stream detections for.

    Query Parameters:
        token: JWT access token for authentication.
    """
    payload = await _authenticate_ws(token)
    if not payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org_id = payload.get("org_id", "unknown")

    # Verify camera belongs to the user's org
    try:
        from app.models.camera import Camera
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Camera).where(
                    Camera.id == uuid.UUID(camera_id),
                    Camera.org_id == uuid.UUID(org_id),
                )
            )
            camera = result.scalars().first()
            if not camera:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
    except (ValueError, Exception) as exc:
        logger.warning("Invalid camera_id for WebSocket", camera_id=camera_id, error=str(exc))
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    channel = f"detections:{camera_id}"
    redis_channel = f"visionai:detections:{camera_id}"

    await manager.connect(channel, websocket)

    await websocket.send_json({
        "type": "connection",
        "status": "connected",
        "channel": "detections",
        "camera_id": camera_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        subscribe_task = asyncio.create_task(
            _subscribe_and_forward(websocket, channel, redis_channel)
        )

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.info("Detection WebSocket disconnected", camera_id=camera_id)
    except Exception as exc:
        logger.error("Detection WebSocket error", camera_id=camera_id, error=str(exc))
    finally:
        subscribe_task.cancel()
        manager.disconnect(channel, websocket)


# ---------------------------------------------------------------------------
# /ws/health - System health metrics stream
# ---------------------------------------------------------------------------


@router.websocket("/health")
async def ws_health(
    websocket: WebSocket,
    token: str | None = Query(None, description="JWT access token"),
) -> None:
    """System health metrics stream via WebSocket.

    Periodically sends system resource usage (CPU, memory, GPU),
    database status, and Redis status to the connected client.

    Query Parameters:
        token: JWT access token for authentication.
    """
    payload = await _authenticate_ws(token)
    if not payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    org_id = payload.get("org_id", "unknown")
    channel = f"health:{org_id}"

    await manager.connect(channel, websocket)

    await websocket.send_json({
        "type": "connection",
        "status": "connected",
        "channel": "health",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        while True:
            # Collect health metrics
            health = {
                "type": "health_update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            try:
                import psutil
                health["cpu_percent"] = psutil.cpu_percent(interval=0.1)
                mem = psutil.virtual_memory()
                health["memory_percent"] = mem.percent
                health["memory_available_gb"] = round(mem.available / (1024**3), 2)
            except ImportError:
                health["cpu_percent"] = None
                health["memory_percent"] = None

            # Redis status
            try:
                redis = await _get_redis()
                await redis.ping()
                health["redis_status"] = "healthy"
            except Exception:
                health["redis_status"] = "unhealthy"

            # Database status
            try:
                from app.database import check_db_health
                db_health = await check_db_health()
                health["database_status"] = db_health.get("status", "unknown")
                health["database_latency_ms"] = db_health.get("latency_ms")
            except Exception:
                health["database_status"] = "unknown"

            await websocket.send_json(health)

            # Check for incoming messages (pings)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                pass

    except WebSocketDisconnect:
        logger.info("Health WebSocket disconnected")
    except Exception as exc:
        logger.error("Health WebSocket error", error=str(exc))
    finally:
        manager.disconnect(channel, websocket)
