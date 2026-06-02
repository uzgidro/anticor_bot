"""Tests for the localized command-menu registration (bot.runners.commands).

Telegram's set_my_commands only accepts ISO 639-1 two-letter language codes,
so our internal locales (kaa, uz_cyrl, uz_latn) must be mapped to valid codes
before they reach the API — otherwise it returns
"Bad Request: invalid language code specified".
"""
from unittest.mock import AsyncMock

import pytest

from bot.runners.commands import _telegram_language_code, set_commands


def test_maps_internal_locales_to_iso_639_1():
    # Plain two-letter locales pass through.
    assert _telegram_language_code("ru") == "ru"
    assert _telegram_language_code("en") == "en"
    # Uzbek script/region variants and Karakalpak collapse to a valid code.
    assert _telegram_language_code("uz_cyrl") == "uz"
    assert _telegram_language_code("uz_latn") == "uz"
    assert _telegram_language_code("kaa") == "uz"


class _StubCore:
    """i18n core stub: returns "<key>@<locale>" so we can see which locale won."""

    def get(self, key, locale=None, **kw):
        return f"{key}@{locale}"


@pytest.mark.asyncio
async def test_set_commands_uses_valid_codes_and_dedupes():
    bot = AsyncMock()
    locales = ("ru", "kaa", "uz_cyrl", "uz_latn", "en")
    await set_commands(bot, _StubCore(), locales, default_locale="ru")

    # Collect the language_code passed on each call (None/"" == default scope).
    codes = [
        kw.get("language_code")
        for _, kw in bot.set_my_commands.call_args_list
    ]
    # Every non-empty code must be a valid 2-letter ISO 639-1 code.
    for code in codes:
        if code:
            assert len(code) == 2 and code.isalpha(), code
    # The three uz_* / kaa locales collapse to a single "uz" registration.
    assert codes.count("uz") == 1
    assert "uz_cyrl" not in codes and "kaa" not in codes
    # ru and en registered, plus the default-scope fallback (no language_code).
    assert "ru" in codes and "en" in codes
    assert any(not c for c in codes)  # default-scope call present


@pytest.mark.asyncio
async def test_set_commands_does_not_raise_on_our_locales():
    """Regression: the real five-locale tuple must not produce an invalid code."""
    bot = AsyncMock()
    from bot.config import LOCALES

    await set_commands(bot, _StubCore(), LOCALES, default_locale="ru")
    assert bot.set_my_commands.await_count >= 1
