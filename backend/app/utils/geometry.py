"""
Geometric Calculation Utilities for VisionAI.

Provides computational geometry functions used throughout the platform
for zone intrusion detection, tripwire crossing, bounding box analysis,
and coordinate transformations.

All coordinate systems use the image convention:

- Origin at top-left corner.
- X increases to the right.
- Y increases downward.

Usage::

    from app.utils.geometry import point_in_polygon, bbox_iou, check_tripwire_crossing

    inside = point_in_polygon((320, 240), [(100, 100), (500, 100), (500, 400), (100, 400)])
    iou = bbox_iou((10, 10, 50, 50), (30, 30, 70, 70))
    crossing = check_tripwire_crossing((100, 200), (100, 180), (0, 190), (640, 190))
"""

from __future__ import annotations

import math
from typing import Optional


def point_in_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    """Test whether a point lies inside a polygon using the ray casting algorithm.

    The algorithm casts a horizontal ray from the point to the right and
    counts the number of polygon edges it crosses.  An odd count means
    the point is inside; even means outside.

    Points exactly on the polygon boundary may return either True or False
    depending on numerical precision.

    Args:
        point: The test point as ``(x, y)``.
        polygon: Ordered list of polygon vertices as ``[(x1, y1), (x2, y2), ...]``.
            The polygon is implicitly closed (last vertex connects to first).
            Must have at least 3 vertices.

    Returns:
        bool: True if the point is inside the polygon.
    """
    if len(polygon) < 3:
        return False

    px, py = point
    n = len(polygon)
    inside = False

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        # Check if the edge crosses the horizontal ray from (px, py) to the right
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside

        j = i

    return inside


def line_intersection(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> Optional[tuple[float, float]]:
    """Compute the intersection point of two line segments.

    Uses the parametric form of line segment intersection.  Returns
    the intersection point only if it lies within both segments.

    Args:
        p1: First endpoint of segment 1 as ``(x, y)``.
        p2: Second endpoint of segment 1 as ``(x, y)``.
        p3: First endpoint of segment 2 as ``(x, y)``.
        p4: Second endpoint of segment 2 as ``(x, y)``.

    Returns:
        Optional[tuple[float, float]]: The intersection point ``(x, y)``
            if the segments intersect, or ``None`` if they do not.
    """
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)

    # Parallel or coincident lines
    if abs(denom) < 1e-10:
        return None

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom

    # Check that the intersection lies within both segments
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        ix = x1 + t * (x2 - x1)
        iy = y1 + t * (y2 - y1)
        return (ix, iy)

    return None


def bbox_iou(
    bbox1: tuple[float, float, float, float],
    bbox2: tuple[float, float, float, float],
) -> float:
    """Compute the Intersection over Union (IoU) of two bounding boxes.

    Both boxes are expected in ``(x1, y1, x2, y2)`` format where
    ``(x1, y1)`` is the top-left corner and ``(x2, y2)`` is the
    bottom-right corner.

    Args:
        bbox1: First bounding box as ``(x1, y1, x2, y2)``.
        bbox2: Second bounding box as ``(x1, y1, x2, y2)``.

    Returns:
        float: IoU value in the range [0.0, 1.0].
    """
    x1 = max(bbox1[0], bbox2[0])
    y1 = max(bbox1[1], bbox2[1])
    x2 = min(bbox1[2], bbox2[2])
    y2 = min(bbox1[3], bbox2[3])

    # Compute intersection area
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)

    if intersection == 0.0:
        return 0.0

    # Compute union area
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
    union = area1 + area2 - intersection

    if union <= 0.0:
        return 0.0

    return intersection / union


def bbox_center(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Compute the centre point of a bounding box.

    Args:
        bbox: Bounding box as ``(x1, y1, x2, y2)``.

    Returns:
        tuple[float, float]: The centre point ``(cx, cy)``.
    """
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def normalize_point(
    point: tuple[float, float],
    width: int,
    height: int,
) -> tuple[float, float]:
    """Normalise a pixel coordinate to the [0, 1] range.

    Args:
        point: Pixel coordinate as ``(x, y)``.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        tuple[float, float]: Normalised coordinate ``(nx, ny)`` where
            both values are in [0.0, 1.0].

    Raises:
        ValueError: If width or height is zero.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Image dimensions must be positive: width={width}, height={height}")

    return (point[0] / width, point[1] / height)


def denormalize_point(
    point: tuple[float, float],
    width: int,
    height: int,
) -> tuple[int, int]:
    """Convert a normalised coordinate back to pixel coordinates.

    Args:
        point: Normalised coordinate as ``(nx, ny)`` in [0.0, 1.0].
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        tuple[int, int]: Pixel coordinate ``(x, y)``.

    Raises:
        ValueError: If width or height is zero.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Image dimensions must be positive: width={width}, height={height}")

    return (int(round(point[0] * width)), int(round(point[1] * height)))


def polygon_area(polygon: list[tuple[float, float]]) -> float:
    """Compute the area of a simple polygon using the Shoelace formula.

    The polygon vertices must be ordered (either clockwise or
    counter-clockwise).  The returned area is always non-negative.

    Args:
        polygon: Ordered list of polygon vertices as ``[(x1, y1), (x2, y2), ...]``.
            Must have at least 3 vertices.

    Returns:
        float: The absolute area of the polygon.
    """
    n = len(polygon)
    if n < 3:
        return 0.0

    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]

    return abs(area) / 2.0


def check_tripwire_crossing(
    prev_pos: tuple[float, float],
    curr_pos: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> Optional[str]:
    """Detect whether a moving object has crossed a tripwire line.

    Determines if the movement vector from ``prev_pos`` to ``curr_pos``
    intersects the tripwire segment from ``line_start`` to ``line_end``,
    and reports the crossing direction.

    The direction is determined using the cross product of the tripwire
    direction vector and the movement vector:

    - ``"left_to_right"``: The object crossed from the left side of the
      tripwire to the right side (relative to the tripwire direction
      from ``line_start`` to ``line_end``).
    - ``"right_to_left"``: The object crossed from the right side to
      the left side.

    Args:
        prev_pos: Previous position of the object as ``(x, y)``.
        curr_pos: Current position of the object as ``(x, y)``.
        line_start: First endpoint of the tripwire line as ``(x, y)``.
        line_end: Second endpoint of the tripwire line as ``(x, y)``.

    Returns:
        Optional[str]: ``"left_to_right"``, ``"right_to_left"``, or
            ``None`` if no crossing occurred.
    """
    # Check if the movement segment intersects the tripwire
    intersection = line_intersection(prev_pos, curr_pos, line_start, line_end)

    if intersection is None:
        return None

    # Determine crossing direction using the cross product
    # Tripwire direction vector
    lx = line_end[0] - line_start[0]
    ly = line_end[1] - line_start[1]

    # Vector from line_start to prev_pos
    px = prev_pos[0] - line_start[0]
    py = prev_pos[1] - line_start[1]

    # Cross product: positive = left side, negative = right side
    cross = lx * py - ly * px

    if cross > 0:
        return "left_to_right"
    elif cross < 0:
        return "right_to_left"

    # Exactly on the line (degenerate case)
    return None
