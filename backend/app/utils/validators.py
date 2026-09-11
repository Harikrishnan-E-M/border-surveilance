"""Input validation utilities for VisionAI."""

import re
from urllib.parse import urlparse


# Indian license plate patterns
_INDIAN_PLATE_PATTERNS = [
    # Standard format: XX00XX0000 (e.g., KA01AB1234)
    r"^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$",
    # Temporary: XX00T0000
    r"^[A-Z]{2}\d{2}T\d{4}$",
    # Diplomatic: 00CD0000
    r"^\d{2}CD\d{4}$",
    # BH series (Bharat): 00BH\d{4}[A-Z]{2}
    r"^\d{2}BH\d{4}[A-Z]{2}$",
    # Defense: xxC0000x (various military codes)
    r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$",
    # Old format with space tolerance
    r"^[A-Z]{2}\s?\d{2}\s?[A-Z]{1,2}\s?\d{4}$",
]

_COMPILED_PLATE_PATTERNS = [re.compile(p) for p in _INDIAN_PLATE_PATTERNS]

_RTSP_PATTERN = re.compile(
    r"^rtsps?://([a-zA-Z0-9_\-\.]+:?[a-zA-Z0-9_\-\.]*@)?"
    r"[a-zA-Z0-9\-\.]+:\d{1,5}(/.*)?$"
)

_PHONE_INDIA_PATTERN = re.compile(r"^(\+91|91|0)?[6-9]\d{9}$")

_CRON_FIELD_PATTERN = re.compile(
    r"^(\*|(\d+(-\d+)?)(,(\d+(-\d+)?))*(/\d+)?)$"
)

_UNSAFE_FILENAME_CHARS = re.compile(r"[^\w\s\-.]")


def validate_indian_plate(plate: str) -> bool:
    """Validate an Indian license plate number.

    Supports standard, temporary, diplomatic, BH series, and defense formats.
    Strips spaces before validation.

    Args:
        plate: License plate string to validate.

    Returns:
        True if the plate matches any known Indian format.
    """
    cleaned = plate.strip().upper().replace(" ", "").replace("-", "")
    return any(pattern.match(cleaned) for pattern in _COMPILED_PLATE_PATTERNS)


def normalize_indian_plate(plate: str) -> str:
    """Normalize an Indian plate number to standard format (uppercase, no spaces/dashes).

    Args:
        plate: Raw plate text from OCR.

    Returns:
        Cleaned and uppercased plate string.
    """
    return plate.strip().upper().replace(" ", "").replace("-", "").replace(".", "")


def validate_rtsp_url(url: str) -> bool:
    """Validate an RTSP stream URL format.

    Args:
        url: URL string to validate.

    Returns:
        True if URL is a valid RTSP/RTSPS URL.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("rtsp", "rtsps") and bool(parsed.hostname)
    except Exception:
        return False


def validate_stream_url(url: str) -> bool:
    """Validate any supported stream URL (RTSP, HTTP, RTMP, file path).

    Args:
        url: URL or file path to validate.

    Returns:
        True if the URL format is valid for any supported protocol.
    """
    if not url:
        return False

    # File path
    if url.startswith("/") or url.startswith("./"):
        return True

    try:
        parsed = urlparse(url)
        valid_schemes = {"rtsp", "rtsps", "rtmp", "http", "https", "file"}
        return parsed.scheme in valid_schemes and bool(parsed.hostname or parsed.path)
    except Exception:
        return False


def validate_email(email: str) -> bool:
    """Validate email address format.

    Args:
        email: Email string to validate.

    Returns:
        True if the email format is valid.
    """
    if not email or len(email) > 254:
        return False
    pattern = re.compile(
        r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
        r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
    )
    return bool(pattern.match(email))


def validate_phone_india(phone: str) -> bool:
    """Validate an Indian phone number.

    Accepts formats: +91XXXXXXXXXX, 91XXXXXXXXXX, 0XXXXXXXXXX, XXXXXXXXXX
    Must start with 6-9 after country/trunk code.

    Args:
        phone: Phone number string.

    Returns:
        True if valid Indian mobile number.
    """
    if not phone:
        return False
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    return bool(_PHONE_INDIA_PATTERN.match(cleaned))


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing unsafe characters.

    Args:
        filename: Original filename.

    Returns:
        Sanitized filename safe for filesystem use.
    """
    if not filename:
        return "unnamed"
    # Remove path separators
    name = filename.replace("/", "_").replace("\\", "_")
    # Remove unsafe characters
    name = _UNSAFE_FILENAME_CHARS.sub("", name)
    # Remove leading dots (hidden files)
    name = name.lstrip(".")
    # Truncate to reasonable length
    if len(name) > 200:
        parts = name.rsplit(".", 1)
        if len(parts) == 2:
            name = parts[0][:195] + "." + parts[1]
        else:
            name = name[:200]
    return name or "unnamed"


def validate_cron_expression(cron: str) -> bool:
    """Validate a cron expression (5-field format).

    Validates minute, hour, day_of_month, month, day_of_week fields.

    Args:
        cron: Cron expression string (e.g., "0 9 * * 1-5").

    Returns:
        True if the cron expression is syntactically valid.
    """
    if not cron:
        return False

    fields = cron.strip().split()
    if len(fields) != 5:
        return False

    # Max values for each field: minute(0-59), hour(0-23), dom(1-31), month(1-12), dow(0-7)
    max_values = [59, 23, 31, 12, 7]
    min_values = [0, 0, 1, 1, 0]

    for i, field in enumerate(fields):
        if not _CRON_FIELD_PATTERN.match(field):
            return False

        if field == "*":
            continue

        # Extract all numeric values for range validation
        try:
            for part in field.split("/")[0].split(","):
                for num_str in part.split("-"):
                    if num_str != "*":
                        num = int(num_str)
                        if num < min_values[i] or num > max_values[i]:
                            return False
        except ValueError:
            return False

    return True


def validate_polygon_points(points: list[dict]) -> bool:
    """Validate polygon points for zone definition.

    Args:
        points: List of point dicts with 'x' and 'y' keys, normalized to 0-1.

    Returns:
        True if valid polygon (min 3 points, all in 0-1 range).
    """
    if not points or len(points) < 3:
        return False

    for point in points:
        if not isinstance(point, dict):
            return False
        x = point.get("x")
        y = point.get("y")
        if x is None or y is None:
            return False
        if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
            return False

    return True


def validate_uuid(value: str) -> bool:
    """Check if a string is a valid UUID4.

    Args:
        value: String to check.

    Returns:
        True if valid UUID format.
    """
    pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    return bool(pattern.match(str(value)))
