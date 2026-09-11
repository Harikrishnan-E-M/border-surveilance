"""
VisionAI Application Configuration.

Centralized configuration management using Pydantic Settings.
All values are loaded from environment variables with sensible defaults
for local development. Production deployments should use a .env file
or inject environment variables directly.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Every field maps to an environment variable of the same name (case-insensitive).
    Nested models are not used -- all config is flat for 12-factor compliance.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── General ───────────────────────────────────────────────────────────
    APP_NAME: str = "VisionAI"
    APP_ENV: str = Field(default="development", pattern="^(development|staging|production|testing)$")
    DEBUG: bool = False
    LOG_LEVEL: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    SECRET_KEY: str = Field(default="change-me-in-production", min_length=16)

    # ── CORS ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = Field(
        default='["http://localhost:3000","http://localhost:8000"]',
        description="JSON-encoded list of allowed CORS origins",
    )

    # ── Database (PostgreSQL) ─────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://visionai:visionai@localhost:5432/visionai",
        description="Async SQLAlchemy database URL",
    )
    DB_POOL_SIZE: int = Field(default=20, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=50)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=5, le=120)
    DB_ECHO: bool = False

    # ── Redis ─────────────────────────────────────────────────────────────
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )
    REDIS_MAX_CONNECTIONS: int = Field(default=20, ge=1, le=200)

    # ── MinIO / S3-compatible Storage ─────────────────────────────────────
    MINIO_ENDPOINT: str = Field(default="localhost:9000")
    MINIO_ACCESS_KEY: str = Field(default="minioadmin")
    MINIO_SECRET_KEY: str = Field(default="minioadmin")
    MINIO_BUCKET_SNAPSHOTS: str = Field(default="snapshots")
    MINIO_BUCKET_RECORDINGS: str = Field(default="recordings")
    MINIO_BUCKET_FACES: str = Field(default="faces")
    MINIO_BUCKET_MODELS: str = Field(default="models")
    MINIO_SECURE: bool = False

    # ── JWT / Authentication ──────────────────────────────────────────────
    JWT_SECRET_KEY: str = Field(default="change-me-jwt-secret", min_length=16)
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1)

    # ── Encryption ────────────────────────────────────────────────────────
    ENCRYPTION_KEY: str = Field(
        default="change-me-encryption-key-32bytes!",
        min_length=32,
        description="Fernet-compatible encryption key for sensitive data at rest",
    )

    # ── SMTP / Email ──────────────────────────────────────────────────────
    SMTP_HOST: str = Field(default="localhost")
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str = Field(default="")
    SMTP_PASSWORD: str = Field(default="")
    SMTP_FROM_EMAIL: str = Field(default="noreply@visionai.local")
    SMTP_FROM_NAME: str = Field(default="VisionAI")
    SMTP_TLS: bool = True
    SMTP_SSL: bool = False

    # ── Twilio / SMS ──────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = Field(default="")
    TWILIO_AUTH_TOKEN: str = Field(default="")
    TWILIO_FROM_NUMBER: str = Field(default="")

    # ── Telegram ──────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = Field(default="")
    TELEGRAM_DEFAULT_CHAT_ID: str = Field(default="")

    # ── MediaMTX (RTSP Server) ────────────────────────────────────────────
    MEDIAMTX_API_URL: str = Field(default="http://localhost:9997")
    MEDIAMTX_RTSP_PORT: int = Field(default=8554, ge=1, le=65535)
    MEDIAMTX_WEBRTC_PORT: int = Field(default=8889, ge=1, le=65535)

    # ── Inference / ML ────────────────────────────────────────────────────
    INFERENCE_DEVICE: str = Field(
        default="cuda",
        pattern="^(cuda|cpu|tensorrt)$",
        description="Device for model inference: cuda, cpu, or tensorrt",
    )
    MODEL_DIR: str = Field(
        default="/opt/visionai/models",
        description="Directory where ML model weights are stored",
    )
    MODEL_CONFIDENCE_THRESHOLD: float = Field(default=0.5, ge=0.0, le=1.0)
    MODEL_NMS_THRESHOLD: float = Field(default=0.4, ge=0.0, le=1.0)
    MAX_BATCH_SIZE: int = Field(default=8, ge=1, le=64)

    # ── Celery ────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/1")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/2")

    # ── Anthropic (AI Copilot) ────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = Field(
        default="",
        description="Anthropic API key for the AI Surveillance Copilot",
    )

    # ── Rate Limiting ─────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1)
    RATE_LIMIT_BURST: int = Field(default=10, ge=1)

    # ── Prometheus ────────────────────────────────────────────────────────
    PROMETHEUS_ENABLED: bool = True
    PROMETHEUS_PORT: int = Field(default=9090, ge=1, le=65535)

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure the database URL uses the asyncpg driver."""
        if not v.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError(
                "DATABASE_URL must start with 'postgresql+asyncpg://' "
                "or 'sqlite+aiosqlite://' for async support"
            )
        return v

    @field_validator("REDIS_URL")
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        """Ensure the Redis URL uses a valid scheme."""
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must start with 'redis://' or 'rediss://'")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, v: str) -> str:
        """Validate that CORS_ORIGINS is a JSON-encoded list of strings."""
        try:
            origins = json.loads(v)
            if not isinstance(origins, list):
                raise ValueError("CORS_ORIGINS must be a JSON list")
            for origin in origins:
                if not isinstance(origin, str):
                    raise ValueError("Each CORS origin must be a string")
        except json.JSONDecodeError as exc:
            raise ValueError("CORS_ORIGINS must be valid JSON") from exc
        return v

    @field_validator("MODEL_DIR")
    @classmethod
    def validate_model_dir(cls, v: str) -> str:
        """Warn-only validation: ensure model directory path is absolute."""
        if not Path(v).is_absolute():
            raise ValueError("MODEL_DIR must be an absolute path")
        return v

    @field_validator("MINIO_ENDPOINT")
    @classmethod
    def validate_minio_endpoint(cls, v: str) -> str:
        """Ensure MinIO endpoint is a valid host:port or hostname."""
        if "://" in v:
            raise ValueError(
                "MINIO_ENDPOINT should be host:port without a scheme "
                "(e.g. 'localhost:9000')"
            )
        return v

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Enforce stricter requirements when running in production."""
        if self.APP_ENV == "production":
            if self.SECRET_KEY == "change-me-in-production":
                raise ValueError("SECRET_KEY must be changed in production")
            if self.JWT_SECRET_KEY == "change-me-jwt-secret":
                raise ValueError("JWT_SECRET_KEY must be changed in production")
            if self.DEBUG:
                raise ValueError("DEBUG must be False in production")
        return self

    # ── Computed Properties ───────────────────────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a Python list."""
        return json.loads(self.CORS_ORIGINS)

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.APP_ENV == "production"

    @property
    def is_testing(self) -> bool:
        """Check if running in testing mode."""
        return self.APP_ENV == "testing"

    @property
    def database_url_sync(self) -> str:
        """Return the synchronous variant of the database URL (for Alembic)."""
        return self.DATABASE_URL.replace("+asyncpg", "").replace("+aiosqlite", "")

    @property
    def minio_buckets(self) -> list[str]:
        """Return all configured MinIO bucket names."""
        return [
            self.MINIO_BUCKET_SNAPSHOTS,
            self.MINIO_BUCKET_RECORDINGS,
            self.MINIO_BUCKET_FACES,
            self.MINIO_BUCKET_MODELS,
        ]

    @property
    def jwt_access_token_expire_seconds(self) -> int:
        """Return access token expiry in seconds."""
        return self.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    @property
    def jwt_refresh_token_expire_seconds(self) -> int:
        """Return refresh token expiry in seconds."""
        return self.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400

    @property
    def model_dir_path(self) -> Path:
        """Return MODEL_DIR as a Path object."""
        return Path(self.MODEL_DIR)

    @property
    def smtp_configured(self) -> bool:
        """Check if SMTP is properly configured for sending emails."""
        return bool(self.SMTP_HOST and self.SMTP_USERNAME and self.SMTP_PASSWORD)

    @property
    def twilio_configured(self) -> bool:
        """Check if Twilio is properly configured for sending SMS."""
        return bool(
            self.TWILIO_ACCOUNT_SID
            and self.TWILIO_AUTH_TOKEN
            and self.TWILIO_FROM_NUMBER
        )

    @property
    def telegram_configured(self) -> bool:
        """Check if Telegram bot is properly configured."""
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_DEFAULT_CHAT_ID)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.

    The instance is created once and reused for the lifetime of the process.
    Calling this function is the canonical way to access configuration
    throughout the application.

    Returns:
        Settings: The application settings singleton.
    """
    return Settings()
