"""Menu navigation handlers — callback queries for main/sub menus."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.i18n import Texts
from bot.callbacks import InfoAction, MenuAction, SettingsAction
from bot.config import AppConfig
from bot.keyboards import info_menu_kb, main_menu_kb, settings_menu_kb
from bot.screen import render

router = Router()


async def _show_main_menu(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Turn the current screen back into the main menu."""
    await callback.answer()
    await render(callback, t.MSG_MAIN_MENU, main_menu_kb(t, config.website_url))


@router.callback_query(MenuAction.filter(F.action == "info"))
async def show_info_menu(callback: CallbackQuery, t: Texts) -> None:
    """Replace main menu with info submenu."""
    await callback.answer()
    await render(callback, t.MSG_INFO_MENU, info_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "settings"))
async def show_settings_menu(callback: CallbackQuery, t: Texts) -> None:
    """Replace main menu with settings submenu."""
    await callback.answer()
    await render(callback, t.MSG_SETTINGS_MENU, settings_menu_kb(t))


@router.callback_query(MenuAction.filter(F.action == "back"))
async def back_to_main(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Return to main menu from any context using MenuAction back."""
    await _show_main_menu(callback, config, t)


@router.callback_query(InfoAction.filter(F.page == "back"))
async def info_back_to_main(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Return to main menu from info submenu."""
    await _show_main_menu(callback, config, t)


@router.callback_query(SettingsAction.filter(F.action == "back"))
async def settings_back_to_main(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Return to main menu from settings submenu."""
    await _show_main_menu(callback, config, t)
