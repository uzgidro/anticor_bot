"""Role-aware menu and per-chat command scope.

The personal command list REPLACES the global one for that chat, so it must
include the base commands or an assigned responsible loses /start from the menu.
"""
from unittest.mock import AsyncMock

import pytest

from bot.db.models import User
from bot.keyboards.inline import main_menu_keyboard
from bot.runners.commands import set_personal_commands


class _I18n:
    def get(self, key, /, *args, **kwargs):
        return key


class _Core:
    def get(self, key, locale=None, **kwargs):
        return f"{key}"


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def test_citizen_menu_has_no_registry_entries():
    user = User(tg_id=1)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-appeals" not in texts
    assert "btn-registry-complaints" not in texts


def test_menu_without_user_is_citizen_menu():
    """Back-compat: existing call sites pass no user."""
    texts = _texts(main_menu_keyboard(_I18n()))
    assert "btn-appeal" in texts
    assert "btn-registry-appeals" not in texts


def test_appeal_responsible_sees_only_appeals_registry():
    user = User(tg_id=2, resp_appeal=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-appeals" in texts
    assert "btn-registry-complaints" not in texts


def test_corruption_responsible_sees_only_complaints_registry():
    user = User(tg_id=3, resp_corruption=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-complaints" in texts
    assert "btn-registry-appeals" not in texts


def test_admin_sees_both_registries():
    user = User(tg_id=4, is_admin=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-appeals" in texts
    assert "btn-registry-complaints" in texts


def test_citizen_menu_keeps_its_own_entries():
    """Adding registry entries must not displace the citizen-facing ones."""
    user = User(tg_id=9, resp_appeal=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    for key in ("btn-appeal", "btn-corruption", "btn-my-submissions", "btn-change-language"):
        assert key in texts


@pytest.mark.asyncio
async def test_personal_commands_keep_base_commands():
    """Chat scope REPLACES the global list — base commands must survive."""
    bot = AsyncMock()
    user = User(tg_id=5, resp_appeal=True, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    bot.set_my_commands.assert_awaited()
    commands = bot.set_my_commands.await_args.args[0]
    names = [c.command for c in commands]
    assert "start" in names and "language" in names and "cancel" in names
    assert "appeals" in names
    assert "complaints" not in names


@pytest.mark.asyncio
async def test_personal_commands_for_admin_include_both():
    bot = AsyncMock()
    user = User(tg_id=6, is_admin=True, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    names = [c.command for c in bot.set_my_commands.await_args.args[0]]
    assert "appeals" in names and "complaints" in names


@pytest.mark.asyncio
async def test_personal_commands_scoped_to_the_users_chat():
    bot = AsyncMock()
    user = User(tg_id=4242, resp_corruption=True, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    scope = bot.set_my_commands.await_args.kwargs["scope"]
    assert scope.chat_id == 4242


@pytest.mark.asyncio
async def test_citizen_scope_is_deleted_not_set():
    """Losing the last role must restore the global menu, not pin an empty one."""
    bot = AsyncMock()
    user = User(tg_id=7, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    bot.delete_my_commands.assert_awaited()
    bot.set_my_commands.assert_not_awaited()


@pytest.mark.asyncio
async def test_falls_back_to_default_locale_when_user_has_none():
    bot = AsyncMock()
    user = User(tg_id=8, resp_appeal=True, language=None)
    await set_personal_commands(bot, _Core(), user, default_locale="en")

    bot.set_my_commands.assert_awaited()
