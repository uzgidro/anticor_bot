"""Tests for bot.config — settings parsing, ADMIN_IDS, DSN building, secrets."""
import pytest
from pydantic import SecretStr


@pytest.fixture
def base_env(monkeypatch):
    """Minimal valid environment for Settings.

    Pins every field these tests assert on (including passwords) so the suite is
    hermetic and never inherits values from a developer's real ``.env``.
    """
    env = {
        "BOT_TOKEN": "123:ABC",
        "ANON_ENC_KEY": "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWZlcm5ldC10ZXN0cw==",
        "ADMIN_IDS": "111, 222 ,333",
        "POSTGRES__HOST": "db",
        "POSTGRES__PORT": "5433",
        "POSTGRES__USER": "u",
        "POSTGRES__PASSWORD": "p",
        "POSTGRES__DB": "d",
        "REDIS__HOST": "rds",
        "REDIS__PORT": "6380",
        "REDIS__PASSWORD": "",  # explicit: assert the no-auth DSN form
        "REDIS__DB": "2",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def test_loads_basic_settings(base_env):
    from bot.config import Settings

    s = Settings()
    assert isinstance(s.bot_token, SecretStr)
    assert s.bot_token.get_secret_value() == "123:ABC"
    assert s.use_webhook is False  # default
    assert s.default_locale == "ru"


def test_admin_ids_parsed_as_int_set(base_env):
    from bot.config import Settings

    s = Settings()
    assert s.admin_ids == {111, 222, 333}


def test_admin_ids_empty(base_env, monkeypatch):
    from bot.config import Settings

    monkeypatch.setenv("ADMIN_IDS", "")
    s = Settings()
    assert s.admin_ids == set()


def test_postgres_dsn_is_asyncpg(base_env):
    from bot.config import Settings

    s = Settings()
    dsn = s.postgres.dsn
    assert dsn == "postgresql+asyncpg://u:p@db:5433/d"


def test_redis_dsn(base_env):
    from bot.config import Settings

    s = Settings()
    assert s.redis.dsn == "redis://rds:6380/2"


def test_redis_dsn_with_password(base_env, monkeypatch):
    from bot.config import Settings

    monkeypatch.setenv("REDIS__PASSWORD", "secret")
    s = Settings()
    assert s.redis.dsn == "redis://:secret@rds:6380/2"


def test_secrets_not_leaked_in_repr(base_env):
    from bot.config import Settings

    s = Settings()
    assert "123:ABC" not in repr(s.bot_token)
    assert "p" not in repr(s.postgres.password) or "**" in repr(s.postgres.password)


def test_locales_list_has_five(base_env):
    from bot.config import Settings

    s = Settings()
    assert set(s.locales) == {"ru", "kaa", "uz_cyrl", "uz_latn", "en"}


def test_run_migrations_on_startup_defaults_true(base_env):
    from bot.config import Settings

    # Convenient for dev / single-replica: schema is brought up to head on boot.
    assert Settings().run_migrations_on_startup is True


def test_run_migrations_on_startup_can_be_disabled(base_env, monkeypatch):
    from bot.config import Settings

    # Prod with multiple replicas: disable and run migrations as a separate step
    # so replicas don't race to upgrade the same database.
    monkeypatch.setenv("RUN_MIGRATIONS_ON_STARTUP", "false")
    assert Settings().run_migrations_on_startup is False


def test_matrix_disabled_by_default(base_env, monkeypatch):
    from bot.config import Settings

    for key in ("MATRIX__HOMESERVER", "MATRIX__USER", "MATRIX__PASSWORD", "MATRIX__TOKEN"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.matrix.enabled is False
    assert s.matrix.locale == "uz_latn"


def test_matrix_enabled_with_password_or_token(base_env, monkeypatch):
    from bot.config import Settings

    monkeypatch.setenv("MATRIX__HOMESERVER", "https://matrix.example.uz")
    monkeypatch.setenv("MATRIX__USER", "@bot:example.uz")
    monkeypatch.setenv("MATRIX__PASSWORD", "pw")
    s = Settings(_env_file=None)
    assert s.matrix.enabled is True
    assert "pw" not in repr(s.matrix)  # secrets never leak through repr

    monkeypatch.delenv("MATRIX__PASSWORD")
    monkeypatch.setenv("MATRIX__TOKEN", "syt_abc")
    s = Settings(_env_file=None)
    assert s.matrix.enabled is True


def test_matrix_room_mapping(base_env, monkeypatch):
    from bot.config import Settings

    monkeypatch.setenv("MATRIX__ROOM_APPEAL", "!a:example.uz")
    monkeypatch.setenv("MATRIX__ROOM_CORRUPTION", "!c:example.uz")
    s = Settings(_env_file=None)
    assert s.matrix.room_for("appeal") == "!a:example.uz"
    assert s.matrix.room_for("corruption") == "!c:example.uz"
    assert s.matrix.type_for_room("!a:example.uz") == "appeal"
    assert s.matrix.type_for_room("!c:example.uz") == "corruption"
    assert s.matrix.type_for_room("!other:example.uz") is None
