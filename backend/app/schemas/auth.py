"""Authentication, user, organization, and API-key Pydantic schemas.

Covers login / registration flows, JWT token payloads, password management,
user CRUD, API key lifecycle, and organization management.
"""

from __future__ import annotations


from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ── Authentication ────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Credentials submitted to obtain a JWT access / refresh token pair."""

    email: EmailStr = Field(
        description="Registered email address.",
        examples=["admin@acme.com"],
    )
    password: str = Field(
        min_length=1,
        description="Account password.",
        examples=["S3cureP@ssw0rd"],
    )

    @field_validator("email", mode="before")
    @classmethod
    def sanitize_email(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class LoginResponse(BaseModel):
    """Token pair returned after successful authentication."""

    access_token: str = Field(
        description="Short-lived JWT access token.",
        examples=["eyJhbGciOiJIUzI1NiIs..."],
    )
    refresh_token: str = Field(
        description="Long-lived refresh token used to obtain new access tokens.",
        examples=["dGhpcyBpcyBhIHJlZnJlc2g..."],
    )
    token_type: str = Field(
        default="bearer",
        description="Token scheme. Always 'bearer'.",
        examples=["bearer"],
    )
    user: UserResponse = Field(description="Authenticated user profile.")


class TokenPayload(BaseModel):
    """Decoded JWT payload used internally for request authorization."""

    sub: UUID = Field(description="Subject — the user's unique ID.")
    org_id: UUID = Field(description="Organization the user belongs to.")
    role: str = Field(
        description="User role within the organization.",
        examples=["org_admin"],
    )
    exp: int = Field(
        description="Token expiration as a UNIX epoch timestamp.",
        examples=[1735689600],
    )


class RefreshTokenRequest(BaseModel):
    """Request body for the token refresh endpoint."""

    refresh_token: str = Field(
        description="Previously issued refresh token.",
        examples=["dGhpcyBpcyBhIHJlZnJlc2g..."],
    )


# ── Password Management ──────────────────────────────────────────────────────


class PasswordResetRequest(BaseModel):
    """Initiate a password-reset flow. An email with a reset link is sent."""

    email: EmailStr = Field(
        description="Email address associated with the account.",
        examples=["user@acme.com"],
    )

    @field_validator("email", mode="before")
    @classmethod
    def sanitize_email(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class PasswordResetConfirm(BaseModel):
    """Complete the password-reset flow with the token received via email."""

    token: str = Field(
        description="One-time password reset token from the email link.",
    )
    new_password: str = Field(
        min_length=8,
        description="New password (minimum 8 characters).",
        examples=["N3wS3cur3P@ss!"],
    )


class ChangePasswordRequest(BaseModel):
    """Change password for the currently authenticated user."""

    current_password: str = Field(
        description="Current account password for verification.",
    )
    new_password: str = Field(
        min_length=8,
        description="Desired new password (minimum 8 characters).",
        examples=["Upd@tedP@ss123"],
    )


# ── Registration ──────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    """Self-service registration for a new user and organization."""

    email: EmailStr = Field(
        description="Email address for the admin account.",
        examples=["founder@newco.io"],
    )

    @field_validator("email", mode="before")
    @classmethod
    def sanitize_email(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip().lower()
        return v
    password: str = Field(
        min_length=8,
        description="Account password (minimum 8 characters).",
        examples=["Str0ngP@ss!"],
    )
    full_name: str = Field(
        min_length=1,
        max_length=255,
        description="Full name of the registering user.",
        examples=["Jane Doe"],
    )
    org_name: str = Field(
        min_length=1,
        max_length=255,
        description="Name for the new organization.",
        examples=["Acme Corp"],
    )


# ── User Schemas ──────────────────────────────────────────────────────────────


class UserResponse(BaseModel):
    """Public representation of a platform user."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="User unique identifier.")
    org_id: UUID = Field(description="Organization the user belongs to.")
    email: EmailStr = Field(description="User email address.")
    full_name: str = Field(description="User full name.", examples=["Jane Doe"])
    phone: str | None = Field(
        default=None,
        description="Phone number in E.164 format.",
        examples=["+919876543210"],
    )
    role: str = Field(
        description="Role within the organization.",
        examples=["org_admin"],
    )
    avatar_url: str | None = Field(
        default=None,
        description="URL to the user's avatar image.",
    )
    is_active: bool = Field(description="Whether the account is active.")
    last_login: datetime | None = Field(
        default=None,
        description="Timestamp of the most recent login.",
    )
    created_at: datetime = Field(description="Account creation timestamp.")


class UserCreate(BaseModel):
    """Schema for an organization admin creating a new user."""

    email: EmailStr = Field(
        description="Email address for the new account.",
        examples=["operator@acme.com"],
    )
    full_name: str = Field(
        min_length=1,
        max_length=255,
        description="Full name.",
        examples=["John Smith"],
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="Phone number in E.164 format.",
        examples=["+919876543210"],
    )
    role: str = Field(
        default="viewer",
        description="Role to assign. One of: super_admin, org_admin, manager, operator, viewer.",
        examples=["operator"],
    )
    password: str = Field(
        min_length=8,
        description="Initial password (minimum 8 characters).",
        examples=["T3mpP@ss!"],
    )


class UserUpdate(BaseModel):
    """Partial update for an existing user. Only supplied fields are changed."""

    full_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated full name.",
    )
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="Updated phone number.",
    )
    role: str | None = Field(
        default=None,
        description="Updated role.",
        examples=["manager"],
    )
    is_active: bool | None = Field(
        default=None,
        description="Enable or disable the account.",
    )


# ── API Key Schemas ───────────────────────────────────────────────────────────


class APIKeyCreate(BaseModel):
    """Request body for creating a new API key."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable name for the API key.",
        examples=["CI/CD Pipeline Key"],
    )
    permissions: list[str] = Field(
        default_factory=list,
        description="List of permission scopes granted to this key.",
        examples=[["cameras:read", "alerts:read", "analytics:read"]],
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp. Null means no expiry.",
    )


class APIKeyResponse(BaseModel):
    """Representation of an API key. The raw ``key`` value is only present
    in the creation response; subsequent reads omit it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="API key unique identifier.")
    name: str = Field(description="Human-readable key name.")
    permissions: list[str] = Field(
        default_factory=list,
        description="Permission scopes granted to the key.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Expiration timestamp, or null if the key does not expire.",
    )
    created_at: datetime = Field(description="Key creation timestamp.")
    key: str | None = Field(
        default=None,
        description="Raw API key value. Only returned once at creation time.",
        examples=["vai_k_abc123def456..."],
    )


# ── Organization Schemas ──────────────────────────────────────────────────────


class OrganizationResponse(BaseModel):
    """Public representation of an organization (tenant)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Organization unique identifier.")
    name: str = Field(
        description="Organization display name.",
        examples=["Acme Corp"],
    )
    slug: str = Field(
        description="URL-safe slug derived from the name.",
        examples=["acme-corp"],
    )
    subscription_tier: str = Field(
        description="Current subscription level: free, pro, or enterprise.",
        examples=["pro"],
    )
    max_cameras: int = Field(
        description="Maximum cameras allowed under the current plan.",
        examples=[16],
    )
    max_users: int = Field(
        description="Maximum users allowed under the current plan.",
        examples=[20],
    )
    timezone: str = Field(
        description="IANA timezone for the organization.",
        examples=["Asia/Kolkata"],
    )
    logo_url: str | None = Field(
        default=None,
        description="URL to the organization's logo image.",
    )
    is_active: bool = Field(description="Whether the organization is active.")
    created_at: datetime = Field(description="Organization creation timestamp.")


class OrganizationUpdate(BaseModel):
    """Partial update for organization settings."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated organization display name.",
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="Updated IANA timezone.",
        examples=["America/New_York"],
    )
    logo_url: str | None = Field(
        default=None,
        max_length=1024,
        description="Updated logo URL.",
    )
