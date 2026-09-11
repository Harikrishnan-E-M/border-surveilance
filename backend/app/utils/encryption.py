"""
Fernet Encryption Utilities for VisionAI.

Provides symmetric encryption/decryption using the ``cryptography``
library's Fernet scheme.  Used to encrypt sensitive data at rest,
including camera stream URLs, usernames, and passwords.

The encryption key is loaded from the application configuration
(``ENCRYPTION_KEY``).  The key must be a URL-safe base64-encoded
32-byte value.

Usage::

    from app.utils.encryption import encrypt_string, decrypt_string

    encrypted = encrypt_string("rtsp://admin:secret@192.168.1.10/stream")
    original = decrypt_string(encrypted)
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = structlog.stdlib.get_logger(__name__)


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    """Return a cached Fernet cipher instance using the configured encryption key.

    The ``ENCRYPTION_KEY`` from settings is hashed with SHA-256 and
    base64-encoded to produce a valid Fernet key, regardless of the
    original key format.  This allows operators to use a human-readable
    passphrase rather than a raw base64 string.

    Returns:
        Fernet: A Fernet cipher instance ready for encryption/decryption.

    Raises:
        ValueError: If the encryption key is not configured.
    """
    settings = get_settings()
    raw_key = settings.ENCRYPTION_KEY

    if not raw_key:
        raise ValueError(
            "ENCRYPTION_KEY is not configured. "
            "Set the ENCRYPTION_KEY environment variable."
        )

    # Derive a valid 32-byte Fernet key from the configured passphrase
    # using SHA-256 to normalise arbitrary input into a fixed-size key.
    key_bytes = hashlib.sha256(raw_key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)

    return Fernet(fernet_key)


def encrypt_string(plaintext: str) -> str:
    """Encrypt a plaintext string and return the result as base64.

    The returned ciphertext is URL-safe base64-encoded and can be
    stored directly in database text columns.

    Args:
        plaintext: The string to encrypt.

    Returns:
        str: The base64-encoded ciphertext.

    Raises:
        ValueError: If the plaintext is empty or None.
    """
    if not plaintext:
        raise ValueError("Cannot encrypt an empty or None string.")

    fernet = get_fernet()
    encrypted_bytes: bytes = fernet.encrypt(plaintext.encode("utf-8"))

    # The Fernet token is already base64-encoded bytes; decode to str
    return encrypted_bytes.decode("utf-8")


def decrypt_string(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext string back to plaintext.

    Args:
        ciphertext: The base64-encoded ciphertext produced by ``encrypt_string``.

    Returns:
        str: The original plaintext string.

    Raises:
        ValueError: If the ciphertext is empty, None, or cannot be decrypted.
    """
    if not ciphertext:
        raise ValueError("Cannot decrypt an empty or None ciphertext.")

    fernet = get_fernet()

    try:
        decrypted_bytes: bytes = fernet.decrypt(ciphertext.encode("utf-8"))
    except InvalidToken as exc:
        logger.error(
            "Decryption failed: invalid token or wrong key",
            error=str(exc),
        )
        raise ValueError(
            "Failed to decrypt the ciphertext. "
            "The data may be corrupted or the encryption key may have changed."
        ) from exc

    return decrypted_bytes.decode("utf-8")
