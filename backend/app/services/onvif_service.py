"""ONVIF service for camera discovery and PTZ control.

Provides WS-Discovery for finding ONVIF cameras on the network
and PTZ (Pan-Tilt-Zoom) command execution. Uses the onvif-zeep
library for SOAP-based ONVIF communication.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

logger = structlog.stdlib.get_logger(__name__)


async def discover_devices(timeout: int = 5) -> list[dict[str, Any]]:
    """Discover ONVIF-compatible devices on the local network via WS-Discovery.

    Args:
        timeout: Discovery timeout in seconds.

    Returns:
        List of discovered device dicts with ip, port, name, and manufacturer.
    """
    devices = []

    try:
        from wsdiscovery.discovery import ThreadedWSDiscovery

        wsd = ThreadedWSDiscovery()
        wsd.start()

        try:
            import asyncio
            await asyncio.sleep(timeout)
            services = wsd.searchServices()

            for service in services:
                xaddrs = service.getXAddrs()
                scopes = service.getScopes()

                for xaddr in xaddrs:
                    # Parse the address to extract host and port
                    from urllib.parse import urlparse
                    parsed = urlparse(xaddr)

                    device_info: dict[str, Any] = {
                        "ip": parsed.hostname or "unknown",
                        "port": parsed.port or 80,
                        "xaddr": xaddr,
                        "scopes": [str(s) for s in scopes] if scopes else [],
                        "name": None,
                        "manufacturer": None,
                    }

                    # Extract manufacturer and model from scopes
                    for scope in scopes or []:
                        scope_str = str(scope)
                        if "onvif://www.onvif.org/name/" in scope_str:
                            device_info["name"] = scope_str.split("/name/")[-1]
                        elif "onvif://www.onvif.org/hardware/" in scope_str:
                            device_info["manufacturer"] = scope_str.split("/hardware/")[-1]

                    devices.append(device_info)
        finally:
            wsd.stop()

    except ImportError:
        logger.warning("wsdiscovery not installed; using fallback probe")
        # Fallback: simple UDP probe on common ONVIF ports
        devices = await _probe_common_ports()
    except Exception as exc:
        logger.error("ONVIF discovery failed", error=str(exc))
        raise

    logger.info("ONVIF discovery complete", device_count=len(devices))
    return devices


async def _probe_common_ports() -> list[dict[str, Any]]:
    """Simple fallback that probes common ONVIF ports on the local subnet."""
    import asyncio
    import socket

    devices = []
    # Detect local subnet
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        return devices

    subnet_prefix = ".".join(local_ip.split(".")[:3])
    onvif_ports = [80, 8080, 8899]

    async def check_host(ip: str, port: int):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=0.5
            )
            writer.close()
            await writer.wait_closed()
            return {"ip": ip, "port": port, "xaddr": f"http://{ip}:{port}/onvif/device_service"}
        except Exception:
            return None

    tasks = []
    for i in range(1, 255):
        ip = f"{subnet_prefix}.{i}"
        for port in onvif_ports:
            tasks.append(check_host(ip, port))

    results = await asyncio.gather(*tasks)
    devices = [r for r in results if r is not None]
    return devices


async def send_ptz_command(
    camera_id: str,
    stream_url: str,
    username_encrypted: Optional[str] = None,
    password_encrypted: Optional[str] = None,
    action: str = "stop",
    speed: float = 0.5,
    preset_id: Optional[int] = None,
) -> dict[str, Any]:
    """Send a PTZ command to an ONVIF camera.

    Args:
        camera_id: Camera UUID string for logging.
        stream_url: Camera RTSP/HTTP URL to derive ONVIF endpoint.
        username_encrypted: Encrypted camera username.
        password_encrypted: Encrypted camera password.
        action: PTZ action ('left', 'right', 'up', 'down', 'zoom_in',
                'zoom_out', 'stop', 'home', 'goto_preset', 'set_preset').
        speed: Movement speed (0.0 - 1.0).
        preset_id: Preset number for goto/set preset actions.

    Returns:
        Dict with action result information.
    """
    # Decrypt credentials if provided
    username = None
    password = None
    if username_encrypted:
        try:
            from app.services.encryption_service import decrypt_value
            username = decrypt_value(username_encrypted)
        except Exception:
            username = username_encrypted
    if password_encrypted:
        try:
            from app.services.encryption_service import decrypt_value
            password = decrypt_value(password_encrypted)
        except Exception:
            password = password_encrypted

    # Derive ONVIF service URL from stream URL
    from urllib.parse import urlparse
    parsed = urlparse(stream_url)
    onvif_host = parsed.hostname
    onvif_port = parsed.port or 80

    logger.info(
        "Sending PTZ command",
        camera_id=camera_id,
        action=action,
        host=onvif_host,
    )

    try:
        from onvif import ONVIFCamera

        cam = ONVIFCamera(onvif_host, onvif_port, username or "admin", password or "")
        await cam.update_xaddrs()

        ptz_service = await cam.create_ptz_service()
        media_service = await cam.create_media_service()

        # Get the first media profile
        profiles = await media_service.GetProfiles()
        if not profiles:
            return {"status": "error", "message": "No media profiles found"}
        profile_token = profiles[0].token

        if action == "stop":
            await ptz_service.Stop({"ProfileToken": profile_token})
        elif action == "home":
            await ptz_service.GotoHomePosition({"ProfileToken": profile_token})
        elif action == "goto_preset" and preset_id is not None:
            await ptz_service.GotoPreset({
                "ProfileToken": profile_token,
                "PresetToken": str(preset_id),
            })
        elif action == "set_preset" and preset_id is not None:
            await ptz_service.SetPreset({
                "ProfileToken": profile_token,
                "PresetToken": str(preset_id),
            })
        elif action in ("left", "right", "up", "down", "zoom_in", "zoom_out"):
            # Map actions to velocity vectors
            velocity = {"PanTilt": {"x": 0, "y": 0}, "Zoom": {"x": 0}}
            if action == "left":
                velocity["PanTilt"]["x"] = -speed
            elif action == "right":
                velocity["PanTilt"]["x"] = speed
            elif action == "up":
                velocity["PanTilt"]["y"] = speed
            elif action == "down":
                velocity["PanTilt"]["y"] = -speed
            elif action == "zoom_in":
                velocity["Zoom"]["x"] = speed
            elif action == "zoom_out":
                velocity["Zoom"]["x"] = -speed

            await ptz_service.ContinuousMove({
                "ProfileToken": profile_token,
                "Velocity": velocity,
            })
        else:
            return {"status": "error", "message": f"Unknown PTZ action: {action}"}

        return {"status": "success", "action": action, "camera_id": camera_id}

    except ImportError:
        logger.warning("onvif-zeep not installed; PTZ unavailable")
        return {"status": "error", "message": "ONVIF library not installed"}
    except Exception as exc:
        logger.error("PTZ command failed", camera_id=camera_id, error=str(exc))
        raise
