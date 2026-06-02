"""Tests for bot.security.crypto — anonymous chat-ref encryption.

The whole anti-corruption anonymity guarantee rests here: a DB dump containing
``enc_chat_ref`` MUST NOT reveal the author's tg_id without the key held
outside the DB.
"""
import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


def test_roundtrip(key):
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    token = cipher.encrypt_chat_id(123456789)
    assert isinstance(token, bytes)
    assert cipher.decrypt_chat_id(token) == 123456789


def test_ciphertext_does_not_contain_plaintext(key):
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    tg_id = 987654321
    token = cipher.encrypt_chat_id(tg_id)
    # The raw tg_id must not appear in the ciphertext in any obvious encoding.
    assert str(tg_id).encode() not in token
    assert tg_id.to_bytes(8, "big") not in token


def test_wrong_key_cannot_decrypt(key):
    from cryptography.fernet import InvalidToken

    from bot.security.crypto import AnonCipher

    token = AnonCipher(key).encrypt_chat_id(42)
    other = AnonCipher(Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        other.decrypt_chat_id(token)


def test_nondeterministic(key):
    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(key)
    # Fernet embeds a timestamp+IV, so two encryptions differ.
    assert cipher.encrypt_chat_id(7) != cipher.encrypt_chat_id(7)


def test_accepts_secretstr(key):
    from pydantic import SecretStr

    from bot.security.crypto import AnonCipher

    cipher = AnonCipher(SecretStr(key))
    assert cipher.decrypt_chat_id(cipher.encrypt_chat_id(5)) == 5


def test_invalid_key_raises():
    from bot.security.crypto import AnonCipher

    with pytest.raises(ValueError):
        AnonCipher("not-a-valid-fernet-key")
