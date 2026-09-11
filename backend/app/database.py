"""
VisionAI Database Configuration.

Async SQLAlchemy engine, session factory, declarative base with common
columns mixin, and helper utilities for FastAPI dependency injection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import MetaData, Uuid, event, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    declared_attr,
    mapped_column,
)
from sqlalchemy.sql import func

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)

# ── Naming convention for constraints (keeps Alembic migrations tidy) ─────

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


# ── Common columns mixin ─────────────────────────────────────────────────

class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamp columns.

    Both columns are timezone-aware and default to the current UTC time.
    ``updated_at`` is refreshed automatically on every UPDATE.
    """

    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )


class Base(AsyncAttrs, DeclarativeBase, TimestampMixin):
    """Declarative base with UUID primary key and timestamp columns.

    All models in the application should inherit from this class.  It
    provides:

    * ``id``  -- UUID v4 primary key
    * ``created_at`` -- row creation timestamp (UTC)
    * ``updated_at`` -- last modification timestamp (UTC)
    """

    metadata = metadata

    # Abstract base -- no table is created for Base itself
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        nullable=False,
    )

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Generate table name automatically from the class name.

        CamelCase class names are converted to snake_case and pluralised
        with a trailing 's'.  e.g. ``CameraGroup`` -> ``camera_groups``.
        """
        import re

        name = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", cls.__name__)
        return name.lower() + "s"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the model instance to a dictionary.

        Returns:
            dict: Column names mapped to their values.
        """
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.id!r})>"


# ── Engine & Session Factory ─────────────────────────────────────────────

def _build_engine(settings: Any | None = None):
    """Build and return the async engine."""
    if settings is None:
        settings = get_settings()

    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        return create_async_engine(
            url,
            echo=settings.DB_ECHO,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    return create_async_engine(
        url,
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_pre_ping=True,
        pool_recycle=1800,  # Recycle connections every 30 minutes
        connect_args={
            "server_settings": {
                "application_name": "visionai",
                "jit": "off",
            },
        },
    )


engine = _build_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Dependency Injection Helpers ─────────────────────────────────────────

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a scoped async database session.

    The session is committed automatically when the request handler returns
    without error.  On exception the transaction is rolled back.

    Yields:
        AsyncSession: An active database session.
    """
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions outside of FastAPI.

    Useful in Celery workers, CLI scripts, and background tasks where
    FastAPI's ``Depends`` mechanism is not available.

    Usage::

        async with get_db_context() as session:
            result = await session.execute(select(User))

    Yields:
        AsyncSession: An active database session.
    """
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ── Initialisation ───────────────────────────────────────────────────────

async def init_db() -> None:
    """Verify database connectivity and log connection details.

    This function is called during application startup. It does **not**
    run migrations -- that responsibility belongs to Alembic.
    """
    settings = get_settings()
    logger.info(
        "Initialising database connection",
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
        logger.info("Database connection verified successfully")
    except Exception as exc:
        logger.error("Database connection failed", error=str(exc))
        raise


async def close_db() -> None:
    """Dispose of the engine and release all pooled connections.

    Called during application shutdown.
    """
    logger.info("Closing database connection pool")
    await engine.dispose()
    logger.info("Database connection pool closed")


async def check_db_health() -> dict[str, Any]:
    """Run a lightweight health-check query against the database.

    Returns:
        dict: Health status including database version and latency.
    """
    import time

    start = time.monotonic()
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT version()"))
            version = result.scalar_one()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {
            "status": "healthy",
            "version": version,
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "error": str(exc),
        }
