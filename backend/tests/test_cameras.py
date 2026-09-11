"""Tests for camera management endpoints and services."""

import pytest
import uuid
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.user import User


class TestCameraEndpoints:
    """Test camera management API endpoints."""

    @pytest.mark.asyncio
    async def test_create_camera(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test creating a new camera."""
        response = await client.post(
            "/api/v1/cameras/",
            headers=auth_headers,
            json={
                "name": "Test Camera 1",
                "stream_url": "rtsp://admin:admin@192.168.1.100:554/stream1",
                "protocol": "rtsp",
                "location_description": "Main entrance",
                "fps": 25,
                "recording_mode": "event",
            },
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["name"] == "Test Camera 1"

    @pytest.mark.asyncio
    async def test_list_cameras(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test listing cameras with pagination."""
        response = await client.get(
            "/api/v1/cameras/",
            headers=auth_headers,
            params={"page": 1, "page_size": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "data" in data

    @pytest.mark.asyncio
    async def test_list_cameras_unauthenticated(self, client: AsyncClient) -> None:
        """Test that listing cameras requires authentication."""
        response = await client.get("/api/v1/cameras/")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_create_camera_invalid_url(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test creating camera with invalid stream URL."""
        response = await client.post(
            "/api/v1/cameras/",
            headers=auth_headers,
            json={
                "name": "Bad Camera",
                "stream_url": "",
                "protocol": "rtsp",
            },
        )
        assert response.status_code in (400, 422)


class TestCameraUtils:
    """Test camera-related utility functions."""

    def test_stream_url_encryption(self) -> None:
        """Test that stream URLs can be encrypted and decrypted."""
        from app.utils.encryption import encrypt_string, decrypt_string

        original_url = "rtsp://admin:password123@192.168.1.100:554/stream"
        encrypted = encrypt_string(original_url)
        decrypted = decrypt_string(encrypted)

        assert decrypted == original_url
        assert encrypted != original_url
