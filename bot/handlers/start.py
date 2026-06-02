"""/start, language selection, and the main menu."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext

from bot.db.models import User
from bot.keyboards.inline import LangCb, MenuCb, language_keyboard, main_menu_keyboard

router = Router(name="start")


async def show_menu(message: Message, i18n: I18nContext) -> None:
    await message.answer(i18n.get("main-menu"), reply_markup=main_menu_keyboard(i18n))


@router.message(CommandStart())
async def cmd_start(message: Message, db_user: User, i18n: I18nContext, state: FSMContext) -> None:
    await state.clear()
    if not db_user.language:
        await message.answer(i18n.get("choose-language"), reply_markup=language_keyboard())
        return
    await show_menu(message, i18n)


@router.callback_query(LangCb.filter())
async def on_language_chosen(
    query: CallbackQuery,
    callback_data: LangCb,
    i18n: I18nContext,
    db_user: User,
) -> None:
    await i18n.set_locale(callback_data.code, db_user=db_user)
    await query.message.edit_text(i18n.get("language-set"))
    await query.message.answer(i18n.get("main-menu"), reply_markup=main_menu_keyboard(i18n))
    await query.answer()


@router.callback_query(MenuCb.filter(F.action == "language"))
async def on_change_language(query: CallbackQuery, i18n: I18nContext) -> None:
    await query.message.answer(i18n.get("choose-language"), reply_markup=language_keyboard())
    await query.answer()


@router.message(Command("language"))
async def cmd_language(message: Message, i18n: I18nContext, state: FSMContext) -> None:
    # Changing language mid-form would desync the UI; ask the user to finish first.
    if await state.get_state() is not None:
        await message.answer(i18n.get("language-locked-in-form"))
        return
    await message.answer(i18n.get("choose-language"), reply_markup=language_keyboard())
