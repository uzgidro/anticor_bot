"""Tests for the composition root (factory) and webhook config validation."""
import pytest
from pydantic import ValidationError


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("ANON_ENC_KEY", "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWZlcm5ldC10ZXN0cw==")
    from bot.config import Settings

    return Settings()


def test_webhook_requires_https_url_and_secret(monkeypatch):
    from bot.config import Settings

    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("ANON_ENC_KEY", "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWZlcm5ldC10ZXN0cw==")
    monkeypatch.setenv("USE_WEBHOOK", "true")
    # No webhook_url/secret -> must fail validation.
    with pytest.raises(ValidationError):
        Settings()


def test_webhook_valid_config(monkeypatch):
    from bot.config import Settings

    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("ANON_ENC_KEY", "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWZlcm5ldC10ZXN0cw==")
    monkeypatch.setenv("USE_WEBHOOK", "true")
    monkeypatch.setenv("WEBHOOK_URL", "https://bot.example.org")
    monkeypatch.setenv("WEBHOOK_PATH", "/webhook/secret-seg")
    monkeypatch.setenv("WEBHOOK_SECRET", "high-entropy-secret")
    s = Settings()
    assert s.webhook_full_url == "https://bot.example.org/webhook/secret-seg"


@pytest.mark.asyncio
async def test_factory_builds_dispatcher_with_middleware_order(settings):
    # Redis.from_url is lazy and create_engine doesn't connect, so no network.
    from bot import factory

    bot, dp, redis, engine = factory.build(settings)
    try:
        names = [type(m).__name__ for m in dp.update.outer_middleware]
        assert "ThrottlingMiddleware" in names
        assert "DbSessionMiddleware" in names
        assert "UserMiddleware" in names
        # Documented order: Throttling (reject flood before DB) -> DbSession ->
        # User -> i18n.
        assert names.index("ThrottlingMiddleware") < names.index("DbSessionMiddleware")
        assert names.index("DbSessionMiddleware") < names.index("UserMiddleware")
        assert names.index("UserMiddleware") < names.index("I18nMiddleware")
    finally:
        await redis.aclose()
        await engine.dispose()


def test_polling_selected_when_not_webhook(settings):
    assert settings.use_webhook is False
