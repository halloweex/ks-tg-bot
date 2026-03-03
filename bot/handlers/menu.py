"""Menu navigation handlers — callback queries for main/sub menus."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from loguru import logger

from bot import texts
from bot.callbacks import InfoAction, MenuAction, SettingsAction
from bot.config import AppConfig
from bot.keyboards import info_menu_kb, main_menu_kb, settings_menu_kb

router = Router()


async def _show_main_menu(callback: CallbackQuery, config: AppConfig) -> None:
    """Edit current message to show the main menu, or send new if edit fails."""
    await callback.answer()
    try:
        await callback.message.edit_text(
            texts.MSG_MAIN_MENU,
            reply_markup=main_menu_kb(config.website_url),
        )
    except TelegramBadRequest as exc:
        logger.debug("edit_text failed ({}), sending new message", exc.message)
        await callback.message.answer(
            texts.MSG_MAIN_MENU,
            reply_markup=main_menu_kb(config.website_url),
        )


@router.callback_query(MenuAction.filter(F.action == "info"))
async def show_info_menu(callback: CallbackQuery) -> None:
    """Replace main menu with info submenu."""
    await callback.answer()
    try:
        await callback.message.edit_text(
            texts.MSG_INFO_MENU,
            reply_markup=info_menu_kb(),
        )
    except TelegramBadRequest as exc:
        logger.debug("edit_text failed ({}), sending new message", exc.message)
        await callback.message.answer(
            texts.MSG_INFO_MENU,
            reply_markup=info_menu_kb(),
        )


@router.callback_query(MenuAction.filter(F.action == "settings"))
async def show_settings_menu(callback: CallbackQuery) -> None:
    """Replace main menu with settings submenu."""
    await callback.answer()
    try:
        await callback.message.edit_text(
            texts.MSG_SETTINGS_MENU,
            reply_markup=settings_menu_kb(),
        )
    except TelegramBadRequest as exc:
        logger.debug("edit_text failed ({}), sending new message", exc.message)
        await callback.message.answer(
            texts.MSG_SETTINGS_MENU,
            reply_markup=settings_menu_kb(),
        )


@router.callback_query(MenuAction.filter(F.action == "back"))
async def back_to_main(callback: CallbackQuery, config: AppConfig) -> None:
    """Return to main menu from any context using MenuAction back."""
    await _show_main_menu(callback, config)


@router.callback_query(InfoAction.filter(F.page == "back"))
async def info_back_to_main(callback: CallbackQuery, config: AppConfig) -> None:
    """Return to main menu from info submenu."""
    await _show_main_menu(callback, config)


@router.callback_query(SettingsAction.filter(F.action == "back"))
async def settings_back_to_main(callback: CallbackQuery, config: AppConfig) -> None:
    """Return to main menu from settings submenu."""
    await _show_main_menu(callback, config)
