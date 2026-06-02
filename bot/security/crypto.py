"""Encryption of anonymous authors' chat references.

For anonymous corruption complaints we must be able to deliver a reply back to
the author, yet a DB dump must never reveal who they are. We therefore store
only the *encrypted* tg_id (``enc_chat_ref``); the Fernet key lives in the app
config / secret store, never in the database. Decryption happens in memory only
at the moment of delivery.
"""
from __future__ import annotations

from cryptography.fernet import Fernet
from pydantic import SecretStr


class AnonCipher:
    """Symmetric cipher for anonymous author chat ids (Fernet / AES-128-CBC + HMAC)."""

    def __init__(self, key: str | SecretStr) -> None:
        raw = key.get_secret_value() if isinstance(key, SecretStr) else key
        try:
            self._fernet = Fernet(raw.encode() if isinstance(raw, str) else raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("ANON_ENC_KEY is not a valid Fernet key") from exc

    def encrypt_chat_id(self, tg_id: int) -> bytes:
        return self._fernet.encrypt(str(tg_id).encode())

    def decrypt_chat_id(self, token: bytes) -> int:
        return int(self._fernet.decrypt(token).decode())
