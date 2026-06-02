"""Tests for bot.security.crypto — anonymous chat-ref encryption.

The whole anti-corruption anonymity guarantee rests here: a DB dump containing
``enc_chat_ref`` MUST NOT reveal the author's tg_id without the key held
outside the DB. The token is also bound to its submission_id (AAD) so it cannot
be moved to another submission's row.
"""
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def test_roundtrip(key):
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    token = cipher.encrypt_chat_id(123456789, submission_id=42)
    assert isinstance(token, bytes)
    assert cipher.decrypt_chat_id(token, submission_id=42) == 123456789


def test_ciphertext_does_not_contain_plaintext(key):
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    tg_id = 987654321
    token = cipher.encrypt_chat_id(tg_id, submission_id=1)
    assert str(tg_id).encode() not in token
    assert tg_id.to_bytes(8, "big") not in token


def test_wrong_key_cannot_decrypt(key):
    from cryptography.fernet import InvalidToken

    from bot.security.crypto import AnonCipher

    token = AnonCipher(key).encrypt_chat_id(42, submission_id=1)
    other = AnonCipher(Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        other.decrypt_chat_id(token, submission_id=1)


def test_token_bound_to_submission(key):
    """A token decrypted against the wrong submission_id must be rejected (AAD)."""
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    token = cipher.encrypt_chat_id(555, submission_id=10)
    with pytest.raises(ValueError):
        cipher.decrypt_chat_id(token, submission_id=11)


def test_nondeterministic(key):
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    # Fernet embeds a timestamp+IV, so two encryptions differ.
    assert cipher.encrypt_chat_id(7, submission_id=1) != cipher.encrypt_chat_id(7, submission_id=1)


def test_accepts_secretstr(key):
    from pydantic import SecretStr

    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(SecretStr(key))
    assert cipher.decrypt_chat_id(cipher.encrypt_chat_id(5, submission_id=3), submission_id=3) == 5


def test_invalid_key_raises():
    from bot.security.crypto import AnonCipher

    with pytest.raises(ValueError):
        AnonCipher("not-a-valid-fernet-key")
