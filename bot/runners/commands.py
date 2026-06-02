"""Register the bot's command menu (the blue '/' menu), localized per locale.

Telegram's set_my_commands accepts only ISO 639-1 two-letter language codes, so
our internal locales must be mapped before they reach the API. Several internal
locales (uz_cyrl, uz_latn, kaa) share one Telegram code (uz) — that's fine: the
command menu is decorative, while the actual UI language comes from
``User.language``. When locales collapse, the first one in ``locales`` wins.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault

# (command, i18n key) — descriptions are localized via the i18n core.
_COMMANDS = [
    ("start", "cmd-start"),
    ("language", "cmd-language"),
    ("cancel", "cmd-cancel"),
]

# Internal locale -> Telegram ISO 639-1 code. Karakalpak has no ISO 639-1 code;
# uz is the closest practical fit for the command menu.
_TG_CODE = {
    "ru": "ru",
    "en": "en",
    "uz_cyrl": "uz",
    "uz_latn": "uz",
    "kaa": "uz",
}


def _telegram_language_code(locale: str) -> str:
    """Map an internal locale to a Telegram-valid language code."""
    if locale in _TG_CODE:
        return _TG_CODE[locale]
    # Fall back to the leading two letters (e.g. "de_DE" -> "de").
    return locale[:2]


def _commands_for(core, locale: str) -> list[BotCommand]:
    return [BotCommand(command=cmd, description=core.get(key, locale)) for cmd, key in _COMMANDS]


async def set_commands(bot: Bot, core, locales: tuple[str, ...], default_locale: str) -> None:
    seen: set[str] = set()
    for locale in locales:
        code = _telegram_language_code(locale)
        if code in seen:
            # Another internal locale already claimed this Telegram code.
            continue
        seen.add(code)
        await bot.set_my_commands(
            _commands_for(core, locale),
            scope=BotCommandScopeDefault(),
            language_code=code,
        )
    # Fallback for clients with an unlisted language (default scope, no code).
    await bot.set_my_commands(_commands_for(core, default_locale), scope=BotCommandScopeDefault())
