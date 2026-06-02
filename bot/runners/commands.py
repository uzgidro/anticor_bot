"""Register the bot's command menu (the blue '/' menu), localized per locale."""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault

# (command, i18n key) — descriptions are localized via the i18n core.
_COMMANDS = [
    ("start", "cmd-start"),
    ("language", "cmd-language"),
    ("cancel", "cmd-cancel"),
]


async def set_commands(bot: Bot, core, locales: tuple[str, ...], default_locale: str) -> None:
    for locale in locales:
        commands = [
            BotCommand(command=cmd, description=core.get(key, locale)) for cmd, key in _COMMANDS
        ]
        await bot.set_my_commands(
            commands, scope=BotCommandScopeDefault(), language_code=locale
        )
    # Fallback for clients with an unlisted language.
    commands = [
        BotCommand(command=cmd, description=core.get(key, default_locale)) for cmd, key in _COMMANDS
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
