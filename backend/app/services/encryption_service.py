"""Encryption service for sensitive data at rest.

Thin wrapper around the core encryption utilities, providing the
interface expected by API endpoints (``encrypt_value`` / ``decrypt_value``).
"""

from __future__ import annotations

from app.utils.encryption import decrypt_string, encrypt_string


def encrypt_value(plaintext: str) -> str:
    """Encrypt a sensitive value for storage.

    Args:
        plaintext: The value to encrypt (e.g. camera password).

    Returns:
        Base64-encoded ciphertext string.
    """
    return encrypt_string(plaintext)


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a previously encrypted value.

    Args:
        ciphertext: The base64-encoded ciphertext.

    Returns:
        Original plaintext string.
    """
    return decrypt_string(ciphertext)
