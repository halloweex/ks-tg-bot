"""Info page handlers — display About, Contacts, Payment, Delivery from config."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.i18n import Texts
from bot.callbacks import InfoAction, MenuAction
from bot.analytics import track
from bot.config import AppConfig
from bot.screen import render

router = Router()


def _back_to_info_kb(t: Texts) -> InlineKeyboardMarkup:
    """Single Back button that returns to the info submenu."""
    builder = InlineKeyboardBuilder()
    # Use MenuAction(action="info") to reuse the existing show_info_menu handler
    builder.button(text=t.BTN_BACK, callback_data=MenuAction(action="info"))
    return builder.as_markup()


async def _show_info_page(callback: CallbackQuery, text: str, t: Texts, page: str = "") -> None:
    """Turn the screen into an info page with a Back button."""
    await callback.answer()
    track(callback.from_user.id, "info_viewed", page=page)
    await render(callback, text, _back_to_info_kb(t))


@router.callback_query(InfoAction.filter(F.page == "about"))
async def show_about(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Display the About Us page from config.yaml."""
    await _show_info_page(callback, config.about_text, t, "about")


@router.callback_query(InfoAction.filter(F.page == "contacts"))
async def show_contacts(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Display the Contacts page from config.yaml."""
    await _show_info_page(callback, config.contacts_text, t, "contacts")


@router.callback_query(InfoAction.filter(F.page == "payment"))
async def show_payment(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Display the Payment page from config.yaml."""
    await _show_info_page(callback, config.payment_text, t, "payment")


@router.callback_query(InfoAction.filter(F.page == "delivery"))
async def show_delivery(callback: CallbackQuery, config: AppConfig, t: Texts) -> None:
    """Display the Delivery page from config.yaml."""
    await _show_info_page(callback, config.delivery_text, t, "delivery")
