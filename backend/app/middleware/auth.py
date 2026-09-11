"""
JWT Authentication Middleware for VisionAI.

Provides JWT token creation, decoding, and FastAPI security dependency
using python-jose for JOSE/JWT operations with HS256 signing.

Usage::

    from app.middleware.auth import JWTBearer, create_access_token

    # Protect a route
    @router.get("/protected", dependencies=[Depends(JWTBearer())])
    async def protected_route(): ...

    # Create tokens for a user
    access = create_access_token(user_id=str(user.id), org_id=str(user.org_id), role=user.role.value)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)


# ── Token Payload Schema ─────────────────────────────────────────────────────


class TokenPayload(BaseModel):
    """Decoded JWT token payload with validated claims.

    Attributes:
        sub: Subject -- the user ID as a string UUID.
        org_id: Organization ID the user belongs to.
        role: User role (e.g. ``super_admin``, ``operator``).
        exp: Token expiration timestamp (UTC epoch seconds).
        iat: Token issued-at timestamp (UTC epoch seconds).
        jti: Unique token identifier for revocation tracking.
        token_type: Either ``access`` or ``refresh``.
    """

    sub: str = Field(..., description="User ID (UUID string)")
    org_id: str = Field(..., description="Organization ID (UUID string)")
    role: str = Field(..., description="User role")
    exp: int = Field(..., description="Expiration time (UTC epoch)")
    iat: int = Field(..., description="Issued at time (UTC epoch)")
    jti: str = Field(..., description="JWT ID for revocation tracking")
    token_type: str = Field(default="access", description="Token type: access or refresh")


# ── Token Creation ────────────────────────────────────────────────────────────


def create_access_token(
    user_id: str,
    org_id: str,
    role: str,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """Create a signed JWT access token.

    The token is valid for 15 minutes from the time of creation and includes
    standard claims (sub, exp, iat, jti) plus application-specific claims
    (org_id, role, token_type).

    Args:
        user_id: The user's UUID as a string, set as the ``sub`` claim.
        org_id: The user's organization UUID as a string.
        role: The user's role string (e.g. ``"org_admin"``).
        extra_claims: Optional dictionary of additional claims to embed.

    Returns:
        str: The encoded JWT access token.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=15)

    payload: dict[str, Any] = {
        "sub": user_id,
        "org_id": org_id,
        "role": role,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "token_type": "access",
    }

    if extra_claims:
        payload.update(extra_claims)

    token: str = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token


def create_refresh_token(
    user_id: str,
    org_id: str,
    role: str,
) -> str:
    """Create a signed JWT refresh token.

    The token is valid for 7 days from the time of creation. Refresh tokens
    carry the same identity claims as access tokens but with a longer TTL
    and a ``token_type`` of ``"refresh"``.

    Args:
        user_id: The user's UUID as a string.
        org_id: The user's organization UUID as a string.
        role: The user's role string.

    Returns:
        str: The encoded JWT refresh token.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=7)

    payload: dict[str, Any] = {
        "sub": user_id,
        "org_id": org_id,
        "role": role,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "token_type": "refresh",
    }

    token: str = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token


# ── Token Decoding ────────────────────────────────────────────────────────────


def decode_access_token(token: str) -> TokenPayload:
    """Decode and validate a JWT access token.

    Verifies the signature, expiration, and required claims. If the token
    is a refresh token, it is rejected -- use ``decode_refresh_token`` for
    those.

    Args:
        token: The raw JWT string.

    Returns:
        TokenPayload: The validated token payload.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or malformed.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require_exp": True, "require_iat": True},
        )
    except JWTError as exc:
        logger.warning("JWT decode failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Validate required claims are present
    required_claims = {"sub", "org_id", "role", "jti"}
    missing = required_claims - set(payload.keys())
    if missing:
        logger.warning("JWT missing required claims", missing=list(missing))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing required claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Reject refresh tokens used as access tokens
    if payload.get("token_type") == "refresh":
        logger.warning("Refresh token used as access token", sub=payload.get("sub"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh tokens cannot be used for API access.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenPayload(**payload)


def decode_refresh_token(token: str) -> TokenPayload:
    """Decode and validate a JWT refresh token.

    Similar to ``decode_access_token`` but requires ``token_type`` to be
    ``"refresh"``. Used exclusively by the token-refresh endpoint.

    Args:
        token: The raw JWT string.

    Returns:
        TokenPayload: The validated token payload.

    Raises:
        HTTPException: 401 if the token is invalid, expired, or not a refresh token.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require_exp": True, "require_iat": True},
        )
    except JWTError as exc:
        logger.warning("Refresh token decode failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected a refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenPayload(**payload)


# ── FastAPI Security Dependency ───────────────────────────────────────────────


class JWTBearer(HTTPBearer):
    """FastAPI security dependency that validates JWT Bearer tokens.

    Extracts the token from the ``Authorization: Bearer <token>`` header,
    decodes it, and attaches the ``TokenPayload`` to ``request.state.user``
    for downstream handlers.

    Usage::

        @router.get("/me", dependencies=[Depends(JWTBearer())])
        async def get_me(request: Request):
            user_payload = request.state.user
            return {"user_id": user_payload.sub}

    To restrict by role::

        @router.get("/admin", dependencies=[Depends(JWTBearer(required_roles=["super_admin", "org_admin"]))])
        async def admin_only(request: Request): ...
    """

    def __init__(
        self,
        required_roles: Optional[list[str]] = None,
        auto_error: bool = True,
    ) -> None:
        """Initialise the JWTBearer security scheme.

        Args:
            required_roles: If provided, the decoded token's role must be
                in this list. Otherwise a 403 Forbidden is raised.
            auto_error: Whether to automatically raise HTTP errors on
                authentication failure.
        """
        super().__init__(auto_error=auto_error)
        self.required_roles = required_roles

    async def __call__(self, request: Request) -> TokenPayload:
        """Validate the Bearer token on every request.

        Args:
            request: The incoming FastAPI request.

        Returns:
            TokenPayload: The decoded and validated token payload.

        Raises:
            HTTPException: 401 if credentials are missing or invalid.
            HTTPException: 403 if the user's role is not authorised.
        """
        credentials: Optional[HTTPAuthorizationCredentials] = await super().__call__(request)

        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication credentials were not provided.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme. Expected 'Bearer'.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token_payload = decode_access_token(credentials.credentials)

        # Role-based access control
        if self.required_roles and token_payload.role not in self.required_roles:
            logger.warning(
                "Insufficient role for endpoint",
                user_id=token_payload.sub,
                user_role=token_payload.role,
                required_roles=self.required_roles,
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{token_payload.role}' does not have permission for this resource.",
            )

        # Attach payload to request state for downstream access
        request.state.user = token_payload

        return token_payload
