"""MinIO S3-compatible object storage client for VisionAI."""

from __future__ import annotations

import io
import os
from datetime import timedelta
from typing import Optional

import numpy as np
import structlog
from minio import Minio
from minio.error import S3Error

logger = structlog.get_logger(__name__)

_storage_client: Optional["StorageClient"] = None


class StorageClient:
    """Wrapper around MinIO client for VisionAI object storage operations.

    Handles upload/download of images, video clips, model files, and reports
    to MinIO (S3-compatible) object storage.
    """

    def __init__(
        self,
        endpoint: str = "localhost:9000",
        access_key: str = "minioadmin",
        secret_key: str = "minioadmin",
        bucket: str = "visionai",
        secure: bool = False,
    ) -> None:
        """Initialize MinIO client.

        Args:
            endpoint: MinIO server endpoint (host:port).
            access_key: MinIO access key.
            secret_key: MinIO secret key.
            bucket: Default bucket name.
            secure: Use HTTPS if True.
        """
        self.bucket = bucket
        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Create the default bucket if it doesn't exist."""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info("Created MinIO bucket", bucket=self.bucket)
        except S3Error as e:
            logger.error("Failed to ensure bucket exists", bucket=self.bucket, error=str(e))
            raise

    def upload_file(
        self,
        file_path: str,
        object_name: str,
        content_type: str = "application/octet-stream",
        bucket: Optional[str] = None,
    ) -> str:
        """Upload a file from local filesystem to MinIO.

        Args:
            file_path: Local file path to upload.
            object_name: Object name/key in the bucket.
            content_type: MIME type of the file.
            bucket: Target bucket (uses default if None).

        Returns:
            The object name (key) of the uploaded file.
        """
        target_bucket = bucket or self.bucket
        try:
            self.client.fput_object(
                bucket_name=target_bucket,
                object_name=object_name,
                file_path=file_path,
                content_type=content_type,
            )
            logger.debug("Uploaded file to MinIO", object_name=object_name, bucket=target_bucket)
            return object_name
        except S3Error as e:
            logger.error("Failed to upload file", object_name=object_name, error=str(e))
            raise

    def upload_bytes(
        self,
        data: bytes,
        object_name: str,
        content_type: str = "application/octet-stream",
        bucket: Optional[str] = None,
    ) -> str:
        """Upload raw bytes to MinIO.

        Args:
            data: Bytes data to upload.
            object_name: Object name/key in the bucket.
            content_type: MIME type.
            bucket: Target bucket (uses default if None).

        Returns:
            The object name (key) of the uploaded file.
        """
        target_bucket = bucket or self.bucket
        try:
            data_stream = io.BytesIO(data)
            self.client.put_object(
                bucket_name=target_bucket,
                object_name=object_name,
                data=data_stream,
                length=len(data),
                content_type=content_type,
            )
            logger.debug("Uploaded bytes to MinIO", object_name=object_name, size=len(data))
            return object_name
        except S3Error as e:
            logger.error("Failed to upload bytes", object_name=object_name, error=str(e))
            raise

    def upload_numpy_image(
        self,
        image: np.ndarray,
        object_name: str,
        format: str = "jpeg",
        quality: int = 85,
        bucket: Optional[str] = None,
    ) -> str:
        """Upload a numpy image array to MinIO.

        Args:
            image: NumPy array (BGR or RGB format).
            object_name: Object name/key in the bucket.
            format: Image format (jpeg, png).
            quality: JPEG quality (1-100).
            bucket: Target bucket.

        Returns:
            The object name of the uploaded image.
        """
        import cv2

        if format.lower() in ("jpeg", "jpg"):
            encode_param = [cv2.IMWRITE_JPEG_QUALITY, quality]
            content_type = "image/jpeg"
            if not object_name.endswith((".jpg", ".jpeg")):
                object_name += ".jpg"
        else:
            encode_param = [cv2.IMWRITE_PNG_COMPRESSION, 3]
            content_type = "image/png"
            if not object_name.endswith(".png"):
                object_name += ".png"

        success, encoded = cv2.imencode(f".{format}", image, encode_param)
        if not success:
            raise ValueError("Failed to encode image")

        return self.upload_bytes(encoded.tobytes(), object_name, content_type, bucket)

    def download_file(
        self,
        object_name: str,
        file_path: str,
        bucket: Optional[str] = None,
    ) -> bool:
        """Download an object from MinIO to local filesystem.

        Args:
            object_name: Object name/key in the bucket.
            file_path: Local destination path.
            bucket: Source bucket.

        Returns:
            True if download was successful.
        """
        target_bucket = bucket or self.bucket
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            self.client.fget_object(target_bucket, object_name, file_path)
            logger.debug("Downloaded file from MinIO", object_name=object_name, file_path=file_path)
            return True
        except S3Error as e:
            logger.error("Failed to download file", object_name=object_name, error=str(e))
            return False

    def download_bytes(
        self,
        object_name: str,
        bucket: Optional[str] = None,
    ) -> Optional[bytes]:
        """Download an object as bytes from MinIO.

        Args:
            object_name: Object name/key in the bucket.
            bucket: Source bucket.

        Returns:
            File contents as bytes, or None on failure.
        """
        target_bucket = bucket or self.bucket
        try:
            response = self.client.get_object(target_bucket, object_name)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except S3Error as e:
            logger.error("Failed to download bytes", object_name=object_name, error=str(e))
            return None

    def get_presigned_url(
        self,
        object_name: str,
        expires: int = 3600,
        bucket: Optional[str] = None,
    ) -> str:
        """Generate a presigned URL for temporary access to an object.

        Args:
            object_name: Object name/key in the bucket.
            expires: URL expiration in seconds (default 1 hour).
            bucket: Source bucket.

        Returns:
            Presigned URL string.
        """
        target_bucket = bucket or self.bucket
        try:
            url = self.client.presigned_get_object(
                bucket_name=target_bucket,
                object_name=object_name,
                expires=timedelta(seconds=expires),
            )
            return url
        except S3Error as e:
            logger.error("Failed to generate presigned URL", object_name=object_name, error=str(e))
            raise

    def delete_file(
        self,
        object_name: str,
        bucket: Optional[str] = None,
    ) -> bool:
        """Delete an object from MinIO.

        Args:
            object_name: Object name/key to delete.
            bucket: Target bucket.

        Returns:
            True if deletion was successful.
        """
        target_bucket = bucket or self.bucket
        try:
            self.client.remove_object(target_bucket, object_name)
            logger.debug("Deleted object from MinIO", object_name=object_name)
            return True
        except S3Error as e:
            logger.error("Failed to delete object", object_name=object_name, error=str(e))
            return False

    def list_objects(
        self,
        prefix: str = "",
        recursive: bool = True,
        bucket: Optional[str] = None,
    ) -> list[str]:
        """List objects in a bucket with optional prefix filter.

        Args:
            prefix: Object name prefix to filter by.
            recursive: Whether to list recursively.
            bucket: Target bucket.

        Returns:
            List of object names matching the prefix.
        """
        target_bucket = bucket or self.bucket
        try:
            objects = self.client.list_objects(
                target_bucket, prefix=prefix, recursive=recursive
            )
            return [obj.object_name for obj in objects if obj.object_name]
        except S3Error as e:
            logger.error("Failed to list objects", prefix=prefix, error=str(e))
            return []

    def object_exists(
        self,
        object_name: str,
        bucket: Optional[str] = None,
    ) -> bool:
        """Check if an object exists in the bucket.

        Args:
            object_name: Object name/key to check.
            bucket: Target bucket.

        Returns:
            True if the object exists.
        """
        target_bucket = bucket or self.bucket
        try:
            self.client.stat_object(target_bucket, object_name)
            return True
        except S3Error:
            return False

    def get_object_size(
        self,
        object_name: str,
        bucket: Optional[str] = None,
    ) -> Optional[int]:
        """Get the size of an object in bytes.

        Args:
            object_name: Object name/key.
            bucket: Target bucket.

        Returns:
            Size in bytes, or None if object doesn't exist.
        """
        target_bucket = bucket or self.bucket
        try:
            stat = self.client.stat_object(target_bucket, object_name)
            return stat.size
        except S3Error:
            return None


def get_storage_client() -> StorageClient:
    """Get or create the singleton StorageClient instance.

    Reads configuration from environment variables or app config.

    Returns:
        Configured StorageClient instance.
    """
    global _storage_client
    if _storage_client is None:
        _storage_client = StorageClient(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            bucket=os.getenv("MINIO_BUCKET", "visionai"),
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        )
    return _storage_client
