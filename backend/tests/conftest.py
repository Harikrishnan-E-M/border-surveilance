"""Pytest configuration and shared fixtures for VisionAI tests."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment before importing app
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://visionai:visionai_password@localhost:5432/visionai_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-testing-only-do-not-use-in-production")
os.environ.setdefault("ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS1mb3ItdGVzdGluZy0xMjM0NTY3ODk=")
os.environ.setdefault("INFERENCE_DEVICE", "cpu")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.database import Base
from app.main import create_app
from app.models.organization import Organization, SubscriptionTier
from app.models.user import User, UserRole


# Use same event loop for all async tests
@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create event loop for test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def app() -> FastAPI:
    """Create FastAPI app instance for testing."""
    return create_app()


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Create async engine for test database."""
    test_db_url = os.getenv("DATABASE_URL")
    eng = create_async_engine(test_db_url, echo=False)

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test with rollback."""
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_org(db_session: AsyncSession) -> Organization:
    """Create a test organization."""
    org = Organization(
        id=uuid.uuid4(),
        name="Test Organization",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
        subscription_tier=SubscriptionTier.enterprise,
        max_cameras=100,
        max_users=50,
        timezone="Asia/Kolkata",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()
    return org


@pytest_asyncio.fixture
async def test_admin(db_session: AsyncSession, test_org: Organization) -> User:
    """Create a test admin user."""
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    user = User(
        id=uuid.uuid4(),
        org_id=test_org.id,
        email=f"admin-{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=pwd_context.hash("testpassword123"),
        full_name="Test Admin",
        role=UserRole.super_admin,
        is_active=True,
        last_login=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def auth_headers(test_admin: User) -> dict:
    """Generate JWT auth headers for test admin."""
    from app.middleware.auth import create_access_token

    token = create_access_token(
        subject=str(test_admin.id),
        org_id=str(test_admin.org_id),
        role=test_admin.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Create async HTTP client for API testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
