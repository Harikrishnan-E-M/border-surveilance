"""
Video and FFmpeg Utilities for VisionAI.

Provides subprocess-based wrappers around FFmpeg for video clip
extraction, thumbnail generation, metadata retrieval, and HLS
recording from RTSP streams.

All functions require FFmpeg and FFprobe to be installed and
accessible on the system PATH.

Usage::

    from app.utils.video_utils import extract_clip, get_video_info

    info = get_video_info("/recordings/camera_01/2025-01-15.mp4")
    success = extract_clip(
        input_url="rtsp://admin:pass@192.168.1.10/stream",
        output_path="/tmp/clip.mp4",
        start_time=60.0,
        duration=30.0,
    )
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.stdlib.get_logger(__name__)


def _get_ffmpeg_path() -> str:
    """Locate the ffmpeg binary on the system.

    Returns:
        str: Absolute path to the ffmpeg binary.

    Raises:
        FileNotFoundError: If ffmpeg is not found on the PATH.
    """
    path = shutil.which("ffmpeg")
    if path is None:
        raise FileNotFoundError(
            "ffmpeg not found on system PATH. "
            "Install FFmpeg: https://ffmpeg.org/download.html"
        )
    return path


def open_opencv_capture(stream_url: str | int) -> cv2.VideoCapture:
    """Open an OpenCV VideoCapture with optimal backend for OS.

    For integer indices or numeric strings (e.g. 0, "0", "/dev/video0"),
    uses DirectShow (cv2.CAP_DSHOW) on Windows for instant webcam opening without hangs.
    """
    import sys
    import cv2

    source: str | int = stream_url
    if isinstance(stream_url, str):
        url_str = stream_url.strip()
        if url_str.isdigit():
            source = int(url_str)
        elif url_str.startswith("/dev/video"):
            try:
                source = int(url_str.replace("/dev/video", ""))
            except ValueError:
                pass

    if isinstance(source, int) and sys.platform.startswith("win"):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        try:
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1500)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500)
        except Exception:
            pass
        return cap

    cap = cv2.VideoCapture(source)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1500)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1500)
    except Exception:
        pass
    return cap


def _get_ffprobe_path() -> str:
    """Locate the ffprobe binary on the system.

    Returns:
        str: Absolute path to the ffprobe binary.

    Raises:
        FileNotFoundError: If ffprobe is not found on the PATH.
    """
    path = shutil.which("ffprobe")
    if path is None:
        raise FileNotFoundError(
            "ffprobe not found on system PATH. "
            "Install FFmpeg: https://ffmpeg.org/download.html"
        )
    return path


def extract_clip(
    input_url: str,
    output_path: str,
    start_time: float,
    duration: float,
) -> bool:
    """Extract a video clip from a source file or RTSP stream.

    Uses FFmpeg to seek to ``start_time`` and copy ``duration`` seconds
    of video to ``output_path``.  When possible, stream copy is used to
    avoid re-encoding.

    Args:
        input_url: Path or URL of the source video (file path or RTSP URL).
        output_path: Destination file path for the extracted clip.
        start_time: Start position in seconds from the beginning.
        duration: Length of the clip in seconds.

    Returns:
        bool: True if the clip was extracted successfully, False otherwise.
    """
    ffmpeg = _get_ffmpeg_path()

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",                       # Overwrite output
        "-ss", str(start_time),     # Seek to start (before input for fast seek)
        "-i", input_url,            # Input source
        "-t", str(duration),        # Duration to extract
        "-c", "copy",               # Stream copy (no re-encoding)
        "-movflags", "+faststart",  # Enable progressive download
        "-avoid_negative_ts", "make_zero",
        "-loglevel", "warning",
        output_path,
    ]

    # For RTSP sources, add transport and timeout options
    if input_url.startswith("rtsp://"):
        cmd = [
            ffmpeg,
            "-y",
            "-rtsp_transport", "tcp",
            "-stimeout", "10000000",  # 10 second timeout (microseconds)
            "-ss", str(start_time),
            "-i", input_url,
            "-t", str(duration),
            "-c", "copy",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            "-loglevel", "warning",
            output_path,
        ]

    logger.info(
        "Extracting video clip",
        input_url=input_url,
        output_path=output_path,
        start_time=start_time,
        duration=duration,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2-minute timeout
        )

        if result.returncode != 0:
            logger.error(
                "FFmpeg clip extraction failed",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
            return False

        # Verify the output file was created and is non-empty
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            logger.error("Clip extraction produced empty or missing file", path=output_path)
            return False

        logger.info("Clip extracted successfully", output_path=output_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg clip extraction timed out", input_url=input_url)
        return False
    except Exception as exc:
        logger.error("Clip extraction error", error=str(exc))
        return False


def generate_thumbnail(
    video_path: str,
    output_path: str,
    timestamp: float = 0,
) -> bool:
    """Generate a JPEG thumbnail from a video at a specific timestamp.

    Args:
        video_path: Path or URL of the source video.
        output_path: Destination file path for the thumbnail image.
        timestamp: Time position in seconds to capture.  Defaults to 0 (first frame).

    Returns:
        bool: True if the thumbnail was generated successfully, False otherwise.
    """
    ffmpeg = _get_ffmpeg_path()

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",            # Extract exactly one frame
        "-q:v", "2",                # JPEG quality (2 = high quality)
        "-vf", "scale='min(640,iw)':-1",  # Cap width at 640px, maintain ratio
        "-loglevel", "warning",
        output_path,
    ]

    # For RTSP sources, add transport options
    if video_path.startswith("rtsp://"):
        cmd = [
            ffmpeg,
            "-y",
            "-rtsp_transport", "tcp",
            "-stimeout", "10000000",
            "-ss", str(timestamp),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            "-vf", "scale='min(640,iw)':-1",
            "-loglevel", "warning",
            output_path,
        ]

    logger.info(
        "Generating thumbnail",
        video_path=video_path,
        output_path=output_path,
        timestamp=timestamp,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.error(
                "FFmpeg thumbnail generation failed",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
            return False

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            logger.error("Thumbnail generation produced empty file", path=output_path)
            return False

        logger.info("Thumbnail generated successfully", output_path=output_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg thumbnail generation timed out", video_path=video_path)
        return False
    except Exception as exc:
        logger.error("Thumbnail generation error", error=str(exc))
        return False


def get_video_info(path: str) -> dict[str, Any]:
    """Retrieve video metadata using FFprobe.

    Returns a dictionary with the video's resolution, frame rate,
    duration, and codec information.

    Args:
        path: Path or URL of the video to probe.

    Returns:
        dict: Video metadata with the following keys:
            - ``width`` (int): Frame width in pixels.
            - ``height`` (int): Frame height in pixels.
            - ``resolution`` (str): ``"WxH"`` string.
            - ``fps`` (float): Frames per second.
            - ``duration`` (float): Duration in seconds.
            - ``codec`` (str): Video codec name.
            - ``audio_codec`` (str | None): Audio codec name (if present).
            - ``bitrate`` (int | None): Overall bitrate in bits/sec.
            - ``format`` (str): Container format name.

    Raises:
        FileNotFoundError: If FFprobe is not installed.
        RuntimeError: If FFprobe fails to read the video metadata.
    """
    ffprobe = _get_ffprobe_path()

    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]

    # For RTSP sources, add transport options
    if path.startswith("rtsp://"):
        cmd = [
            ffprobe,
            "-v", "quiet",
            "-rtsp_transport", "tcp",
            "-stimeout", "10000000",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            path,
        ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"FFprobe failed with return code {result.returncode}: "
                f"{result.stderr.strip()}"
            )

        probe_data: dict[str, Any] = json.loads(result.stdout)

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFprobe timed out while probing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FFprobe returned invalid JSON: {exc}") from exc

    # Parse video stream info
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = "unknown"
    audio_codec: Optional[str] = None

    for stream in probe_data.get("streams", []):
        codec_type = stream.get("codec_type", "")

        if codec_type == "video" and width == 0:
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
            video_codec = stream.get("codec_name", "unknown")

            # Parse frame rate from r_frame_rate (e.g. "30/1")
            r_frame_rate = stream.get("r_frame_rate", "0/1")
            try:
                num, den = r_frame_rate.split("/")
                fps = float(num) / float(den) if float(den) != 0 else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0

        elif codec_type == "audio" and audio_codec is None:
            audio_codec = stream.get("codec_name")

    # Parse format info
    format_info = probe_data.get("format", {})
    duration_str = format_info.get("duration", "0")
    try:
        duration = float(duration_str)
    except (ValueError, TypeError):
        duration = 0.0

    bitrate_str = format_info.get("bit_rate")
    bitrate: Optional[int] = None
    if bitrate_str:
        try:
            bitrate = int(bitrate_str)
        except (ValueError, TypeError):
            bitrate = None

    format_name = format_info.get("format_name", "unknown")

    info: dict[str, Any] = {
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else "unknown",
        "fps": round(fps, 2),
        "duration": round(duration, 2),
        "codec": video_codec,
        "audio_codec": audio_codec,
        "bitrate": bitrate,
        "format": format_name,
    }

    logger.debug("Video info retrieved", path=path, info=info)
    return info


def start_hls_recording(
    rtsp_url: str,
    output_dir: str,
    segment_duration: int = 10,
) -> subprocess.Popen[str]:
    """Start an FFmpeg process that records an RTSP stream as HLS segments.

    The process runs in the background and writes ``.ts`` segment files
    along with a ``.m3u8`` playlist to ``output_dir``.  The caller is
    responsible for managing (monitoring, stopping) the returned
    ``Popen`` object.

    Args:
        rtsp_url: RTSP stream URL to record.
        output_dir: Directory to write HLS segments and playlist to.
        segment_duration: Duration of each HLS segment in seconds.
            Defaults to 10.

    Returns:
        subprocess.Popen: The running FFmpeg process.  Call ``.terminate()``
            or ``.kill()`` to stop recording.

    Raises:
        FileNotFoundError: If FFmpeg is not installed.
        OSError: If the output directory cannot be created.
    """
    ffmpeg = _get_ffmpeg_path()

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    playlist_path = os.path.join(output_dir, "stream.m3u8")
    segment_pattern = os.path.join(output_dir, "segment_%05d.ts")

    cmd = [
        ffmpeg,
        "-rtsp_transport", "tcp",
        "-stimeout", "10000000",      # 10 second connection timeout
        "-i", rtsp_url,
        "-c", "copy",                  # Stream copy (no re-encoding)
        "-f", "hls",                   # HLS output format
        "-hls_time", str(segment_duration),
        "-hls_list_size", "0",         # Keep all segments in playlist
        "-hls_flags", "delete_segments+append_list",
        "-hls_segment_filename", segment_pattern,
        "-loglevel", "warning",
        playlist_path,
    ]

    logger.info(
        "Starting HLS recording",
        rtsp_url=rtsp_url,
        output_dir=output_dir,
        segment_duration=segment_duration,
    )

    process: subprocess.Popen[str] = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    logger.info(
        "HLS recording process started",
        pid=process.pid,
        output_dir=output_dir,
    )

    return process
