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

    def encrypt_chat_id(self, tg_id: int, submission_id: int) -> bytes:
        """Encrypt an anonymous author's tg_id, bound to its submission.

        The submission_id is baked into the plaintext as a poor-man's AAD: a
        token cannot be moved to another submission's row without detection,
        because decrypt verifies the bound submission_id.
        """
        return self._fernet.encrypt(f"{submission_id}:{tg_id}".encode())

    def decrypt_chat_id(self, token: bytes, submission_id: int) -> int:
        raw = self._fernet.decrypt(token).decode()
        bound_id, _, tg = raw.partition(":")
        if not tg or int(bound_id) != submission_id:
            raise ValueError("anon chat-ref does not match its submission")
        return int(tg)
