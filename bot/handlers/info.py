"""Info page handlers — display About, Contacts, Payment, Delivery from config."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot import texts
from bot.callbacks import InfoAction, MenuAction
from bot.config import AppConfig

router = Router()


def _back_to_info_kb() -> InlineKeyboardMarkup:
    """Single Back button that returns to the info submenu."""
    builder = InlineKeyboardBuilder()
    # Use MenuAction(action="info") to reuse the existing show_info_menu handler
    builder.button(text=texts.BTN_BACK, callback_data=MenuAction(action="info"))
    return builder.as_markup()


async def _show_info_page(callback: CallbackQuery, text: str) -> None:
    """Edit current message to show an info page with Back button."""
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=_back_to_info_kb())
    except TelegramBadRequest as exc:
        logger.debug("edit_text failed ({}), sending new message", exc.message)
        await callback.message.answer(text, reply_markup=_back_to_info_kb())


@router.callback_query(InfoAction.filter(F.page == "about"))
async def show_about(callback: CallbackQuery, config: AppConfig) -> None:
    """Display the About Us page from config.yaml."""
    await _show_info_page(callback, config.about_text)


@router.callback_query(InfoAction.filter(F.page == "contacts"))
async def show_contacts(callback: CallbackQuery, config: AppConfig) -> None:
    """Display the Contacts page from config.yaml."""
    await _show_info_page(callback, config.contacts_text)


@router.callback_query(InfoAction.filter(F.page == "payment"))
async def show_payment(callback: CallbackQuery, config: AppConfig) -> None:
    """Display the Payment page from config.yaml."""
    await _show_info_page(callback, config.payment_text)


@router.callback_query(InfoAction.filter(F.page == "delivery"))
async def show_delivery(callback: CallbackQuery, config: AppConfig) -> None:
    """Display the Delivery page from config.yaml."""
    await _show_info_page(callback, config.delivery_text)
