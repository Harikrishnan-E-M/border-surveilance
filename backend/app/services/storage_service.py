"""Centralized object storage service using MinIO.

Provides upload, download, delete, and presigned URL generation
for files stored in MinIO buckets.
"""

from __future__ import annotations

import io
from typing import Optional

import structlog
from miniopy_async import Minio

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)

_client: Optional[Minio] = None


def _get_client() -> Minio:
    """Get or create the MinIO client singleton."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=getattr(settings, "MINIO_USE_SSL", False),
        )
    return _client


def _parse_path(path: str) -> tuple[str, str]:
    """Parse a storage path into bucket and object key.

    If the path contains a '/' the first segment is the bucket name
    and the rest is the object key. Otherwise the default bucket is used.

    Args:
        path: Storage path like 'bucket/key/to/object' or just 'key/to/object'.

    Returns:
        Tuple of (bucket_name, object_key).
    """
    settings = get_settings()
    parts = path.split("/", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return getattr(settings, "MINIO_BUCKET_RECORDINGS", "recordings"), path


async def _ensure_bucket(client: Minio, bucket: str) -> None:
    """Create the bucket if it doesn't exist."""
    try:
        exists = await client.bucket_exists(bucket)
        if not exists:
            await client.make_bucket(bucket)
            logger.info("Created MinIO bucket", bucket=bucket)
    except Exception as exc:
        logger.warning("Bucket check/create failed", bucket=bucket, error=str(exc))


async def upload_file(
    file_bytes: bytes,
    path: str,
    content_type: Optional[str] = None,
) -> str:
    """Upload a file to MinIO object storage.

    Args:
        file_bytes: Raw file content.
        path: Storage path (bucket/key format).
        content_type: MIME type of the file.

    Returns:
        The storage path where the file was saved.
    """
    client = _get_client()
    bucket, key = _parse_path(path)

    await _ensure_bucket(client, bucket)

    data = io.BytesIO(file_bytes)
    await client.put_object(
        bucket_name=bucket,
        object_name=key,
        data=data,
        length=len(file_bytes),
        content_type=content_type or "application/octet-stream",
    )

    logger.info("File uploaded to storage", bucket=bucket, key=key, size=len(file_bytes))
    return f"{bucket}/{key}"


async def download_file(path: str) -> bytes:
    """Download a file from MinIO object storage.

    Args:
        path: Storage path (bucket/key format).

    Returns:
        Raw file content as bytes.
    """
    client = _get_client()
    bucket, key = _parse_path(path)

    response = await client.get_object(bucket_name=bucket, object_name=key)
    try:
        data = await response.read()
    finally:
        response.close()
        await response.release()

    logger.debug("File downloaded from storage", bucket=bucket, key=key)
    return data


async def delete_file(path: str) -> None:
    """Delete a file from MinIO object storage.

    Args:
        path: Storage path (bucket/key format).
    """
    client = _get_client()
    bucket, key = _parse_path(path)

    try:
        await client.remove_object(bucket_name=bucket, object_name=key)
        logger.info("File deleted from storage", bucket=bucket, key=key)
    except Exception as exc:
        logger.warning("Failed to delete file from storage", path=path, error=str(exc))


async def get_presigned_url(path: str, expires_seconds: int = 3600) -> str:
    """Generate a presigned URL for temporary file access.

    Args:
        path: Storage path (bucket/key format).
        expires_seconds: URL expiration in seconds (default 1 hour).

    Returns:
        Presigned URL string.
    """
    from datetime import timedelta

    client = _get_client()
    bucket, key = _parse_path(path)

    url = await client.presigned_get_object(
        bucket_name=bucket,
        object_name=key,
        expires=timedelta(seconds=expires_seconds),
    )

    return url


async def file_exists(path: str) -> bool:
    """Check if a file exists in storage.

    Args:
        path: Storage path (bucket/key format).

    Returns:
        True if the file exists.
    """
    client = _get_client()
    bucket, key = _parse_path(path)

    try:
        await client.stat_object(bucket_name=bucket, object_name=key)
        return True
    except Exception:
        return False
