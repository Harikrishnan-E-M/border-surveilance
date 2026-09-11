"""
Image Processing Utilities for VisionAI.

Common image operations used across the platform for snapshot
processing, face recognition, object detection visualisation,
and image quality assessment.

All functions work with NumPy arrays in BGR colour space (OpenCV
convention) unless otherwise noted.

Usage::

    from app.utils.image_utils import decode_base64_image, resize_image

    image = decode_base64_image(base64_string)
    resized = resize_image(image, max_size=640)
"""

from __future__ import annotations

import base64
from typing import Any

import cv2
import numpy as np
import structlog

logger = structlog.stdlib.get_logger(__name__)


def decode_base64_image(b64_string: str) -> np.ndarray:
    """Decode a base64-encoded image string into a NumPy array.

    Supports both raw base64 and data-URI formatted strings
    (e.g. ``data:image/jpeg;base64,/9j/4AAQ...``).

    Args:
        b64_string: The base64-encoded image string.

    Returns:
        np.ndarray: The decoded image as a BGR NumPy array (H, W, C).

    Raises:
        ValueError: If the string cannot be decoded into a valid image.
    """
    if not b64_string:
        raise ValueError("Base64 string is empty or None.")

    # Strip data URI prefix if present
    if "," in b64_string and b64_string.startswith("data:"):
        b64_string = b64_string.split(",", 1)[1]

    try:
        image_bytes = base64.b64decode(b64_string)
    except Exception as exc:
        raise ValueError(f"Invalid base64 encoding: {exc}") from exc

    # Convert bytes to numpy array
    nparr = np.frombuffer(image_bytes, dtype=np.uint8)

    # Decode image
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(
            "Failed to decode image from base64 data. "
            "The data may not be a valid image format."
        )

    return image


def encode_image_to_base64(image: np.ndarray, format: str = "jpeg") -> str:
    """Encode a NumPy image array to a base64 string.

    Args:
        image: The image as a BGR NumPy array (H, W, C).
        format: Output format -- ``"jpeg"`` or ``"png"``.  Defaults to ``"jpeg"``.

    Returns:
        str: The base64-encoded image string (without data URI prefix).

    Raises:
        ValueError: If the image is invalid or encoding fails.
    """
    if image is None or image.size == 0:
        raise ValueError("Cannot encode an empty or None image.")

    ext = f".{format.lower().replace('jpg', 'jpeg')}"
    if ext == ".jpeg":
        ext = ".jpg"

    encode_params: list[int] = []
    if ext == ".jpg":
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, 85]
    elif ext == ".png":
        encode_params = [cv2.IMWRITE_PNG_COMPRESSION, 6]

    success, buffer = cv2.imencode(ext, image, encode_params)
    if not success:
        raise ValueError(f"Failed to encode image to {format} format.")

    return base64.b64encode(buffer.tobytes()).decode("utf-8")


def resize_image(image: np.ndarray, max_size: int = 640) -> np.ndarray:
    """Resize an image so that its largest dimension does not exceed ``max_size``.

    The aspect ratio is preserved.  If the image is already smaller than
    ``max_size`` on both dimensions, it is returned unchanged.

    Args:
        image: The input image as a BGR NumPy array (H, W, C).
        max_size: Maximum allowed size for the longest dimension.  Defaults to 640.

    Returns:
        np.ndarray: The resized (or original) image.

    Raises:
        ValueError: If the image is invalid.
    """
    if image is None or image.size == 0:
        raise ValueError("Cannot resize an empty or None image.")

    height, width = image.shape[:2]

    # No resize needed if already within bounds
    if max(height, width) <= max_size:
        return image

    # Compute scale factor preserving aspect ratio
    scale = max_size / max(height, width)
    new_width = int(width * scale)
    new_height = int(height * scale)

    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized: np.ndarray = cv2.resize(
        image, (new_width, new_height), interpolation=interpolation
    )
    return resized


def crop_image(image: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    """Crop a rectangular region from the image.

    Args:
        image: The input image as a BGR NumPy array (H, W, C).
        bbox: Bounding box as ``(x1, y1, x2, y2)`` in pixel coordinates.
            Coordinates are clamped to the image boundaries.

    Returns:
        np.ndarray: The cropped image region.

    Raises:
        ValueError: If the bounding box is invalid or produces an empty crop.
    """
    if image is None or image.size == 0:
        raise ValueError("Cannot crop an empty or None image.")

    height, width = image.shape[:2]
    x1, y1, x2, y2 = bbox

    # Clamp coordinates to image boundaries
    x1 = max(0, min(int(x1), width - 1))
    y1 = max(0, min(int(y1), height - 1))
    x2 = max(0, min(int(x2), width))
    y2 = max(0, min(int(y2), height))

    if x2 <= x1 or y2 <= y1:
        raise ValueError(
            f"Invalid bounding box ({x1}, {y1}, {x2}, {y2}): "
            "produces an empty crop region."
        )

    return image[y1:y2, x1:x2].copy()


def draw_bboxes(
    image: np.ndarray,
    detections: list[dict[str, Any]],
) -> np.ndarray:
    """Draw bounding boxes with labels on the image.

    Each detection dictionary should contain:

    - ``bbox``: ``[x1, y1, x2, y2]`` in pixel coordinates.
    - ``label`` (optional): Text label to display above the box.
    - ``confidence`` (optional): Confidence score (0.0--1.0).
    - ``color`` (optional): BGR tuple, e.g. ``(0, 255, 0)``.

    Args:
        image: The input image as a BGR NumPy array (H, W, C).
            A copy is made; the original is not modified.
        detections: List of detection dictionaries.

    Returns:
        np.ndarray: A copy of the image with bounding boxes drawn.
    """
    if image is None or image.size == 0:
        raise ValueError("Cannot draw on an empty or None image.")

    output = image.copy()

    for det in detections:
        bbox = det.get("bbox")
        if bbox is None or len(bbox) < 4:
            continue

        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        color: tuple[int, int, int] = det.get("color", (0, 255, 0))
        thickness = 2

        # Draw the bounding box
        cv2.rectangle(output, (x1, y1), (x2, y2), color, thickness)

        # Build label text
        label = det.get("label", "")
        confidence = det.get("confidence")
        if confidence is not None:
            label = f"{label} {confidence:.2f}" if label else f"{confidence:.2f}"

        if label:
            # Draw label background
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.5
            font_thickness = 1
            (text_width, text_height), baseline = cv2.getTextSize(
                label, font, font_scale, font_thickness
            )

            # Position label above the box, or below if near the top edge
            label_y = y1 - 6
            bg_y1 = y1 - text_height - 10
            if bg_y1 < 0:
                label_y = y2 + text_height + 6
                bg_y1 = y2 + 2
                bg_y2 = y2 + text_height + 12
            else:
                bg_y2 = y1

            cv2.rectangle(
                output,
                (x1, bg_y1),
                (x1 + text_width + 4, bg_y2),
                color,
                cv2.FILLED,
            )
            cv2.putText(
                output,
                label,
                (x1 + 2, label_y),
                font,
                font_scale,
                (0, 0, 0),  # Black text on coloured background
                font_thickness,
                cv2.LINE_AA,
            )

    return output


def compute_image_quality(image: np.ndarray) -> float:
    """Compute a blur-detection quality score using Laplacian variance.

    A higher value indicates a sharper image; a lower value indicates
    blur.  Typical thresholds:

    - < 50: Very blurry / unusable
    - 50--100: Somewhat blurry
    - 100--500: Acceptable quality
    - > 500: Sharp / good quality

    Args:
        image: The input image as a BGR NumPy array (H, W, C).

    Returns:
        float: The Laplacian variance (non-negative).

    Raises:
        ValueError: If the image is invalid.
    """
    if image is None or image.size == 0:
        raise ValueError("Cannot compute quality on an empty or None image.")

    # Convert to grayscale for Laplacian
    if len(image.shape) == 3 and image.shape[2] == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    elif len(image.shape) == 2:
        gray = image
    else:
        raise ValueError(f"Unexpected image shape: {image.shape}")

    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    variance: float = float(laplacian.var())
    return variance


def numpy_to_bytes(
    image: np.ndarray,
    format: str = "jpeg",
    quality: int = 85,
) -> bytes:
    """Convert a NumPy image array to raw bytes.

    Args:
        image: The image as a BGR NumPy array (H, W, C).
        format: Output format -- ``"jpeg"`` or ``"png"``.  Defaults to ``"jpeg"``.
        quality: JPEG quality (1--100).  Ignored for PNG.  Defaults to 85.

    Returns:
        bytes: The encoded image bytes.

    Raises:
        ValueError: If encoding fails.
    """
    if image is None or image.size == 0:
        raise ValueError("Cannot convert an empty or None image to bytes.")

    ext = f".{format.lower().replace('jpg', 'jpeg')}"
    if ext == ".jpeg":
        ext = ".jpg"

    encode_params: list[int] = []
    if ext == ".jpg":
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    elif ext == ".png":
        encode_params = [cv2.IMWRITE_PNG_COMPRESSION, min(9, max(0, 9 - quality // 11))]

    success, buffer = cv2.imencode(ext, image, encode_params)
    if not success:
        raise ValueError(f"Failed to encode image to {format} format.")

    return buffer.tobytes()
