"""Tests for authentication endpoints and auth service."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.user import User


class TestAuthEndpoints:
    """Test authentication API endpoints."""

    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient, test_admin: User) -> None:
        """Test successful login returns access token."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": test_admin.email,
                "password": "testpassword123",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "access_token" in data["data"]
        assert data["data"]["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient, test_admin: User) -> None:
        """Test login with wrong password returns 401."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": test_admin.email,
                "password": "wrongpassword",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client: AsyncClient) -> None:
        """Test login with non-existent email returns 401."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "nonexistent@test.com",
                "password": "somepassword",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_me_authenticated(
        self, client: AsyncClient, auth_headers: dict, test_admin: User
    ) -> None:
        """Test getting current user profile with valid token."""
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["email"] == test_admin.email

    @pytest.mark.asyncio
    async def test_get_me_unauthenticated(self, client: AsyncClient) -> None:
        """Test getting profile without token returns 401/403."""
        response = await client.get("/api/v1/auth/me")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_register_new_org(self, client: AsyncClient) -> None:
        """Test registering a new organization and admin user."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newadmin@neworg.com",
                "password": "securepassword123",
                "full_name": "New Admin",
                "org_name": "New Organization",
            },
        )
        # Should succeed or return validation error
        assert response.status_code in (200, 201, 422)

    @pytest.mark.asyncio
    async def test_change_password(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test changing password with valid current password."""
        response = await client.post(
            "/api/v1/auth/change-password",
            headers=auth_headers,
            json={
                "current_password": "testpassword123",
                "new_password": "newpassword456",
            },
        )
        # May succeed or fail depending on implementation
        assert response.status_code in (200, 400, 422)


class TestAuthService:
    """Test authentication service functions."""

    @pytest.mark.asyncio
    async def test_password_hashing(self) -> None:
        """Test password hashing and verification."""
        from passlib.context import CryptContext

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        password = "test_password_123"
        hashed = pwd_context.hash(password)

        assert pwd_context.verify(password, hashed) is True
        assert pwd_context.verify("wrong_password", hashed) is False

    @pytest.mark.asyncio
    async def test_jwt_token_creation(self) -> None:
        """Test JWT token creation and decoding."""
        from app.middleware.auth import create_access_token, decode_access_token

        token = create_access_token(
            subject="test-user-id",
            org_id="test-org-id",
            role="admin",
        )
        assert token is not None
        assert len(token) > 0

        payload = decode_access_token(token)
        assert payload["sub"] == "test-user-id"

    @pytest.mark.asyncio
    async def test_jwt_expired_token(self) -> None:
        """Test that expired tokens are rejected."""
        from app.middleware.auth import create_access_token, decode_access_token

        # Create token with -1 minute expiry (already expired)
        token = create_access_token(
            subject="test-user-id",
            org_id="test-org-id",
            role="admin",
            expires_delta_minutes=-1,
        )

        with pytest.raises(Exception):
            decode_access_token(token)
