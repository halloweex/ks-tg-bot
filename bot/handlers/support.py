"""Support relay — user-to-admin forwarding and admin-to-user reply."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.i18n import Texts
from bot.callbacks import MenuAction
from bot.analytics import track
from bot.config import AppConfig
from bot.keyboards import main_menu_kb
from bot.states import SupportStates

router = Router()


@router.callback_query(MenuAction.filter(F.action == "support"))
async def enter_support_mode(
    callback: CallbackQuery,
    callback_data: MenuAction,
    state: FSMContext,
    t: Texts,
) -> None:
    """Prompt user to type a message for the support team."""
    await callback.answer()
    track(callback.from_user.id, "support_opened")
    await state.set_state(SupportStates.waiting_message)
    await callback.message.edit_text(t.MSG_SUPPORT_PROMPT)


@router.message(SupportStates.waiting_message)
async def forward_to_support(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Forward user's message to admin chat with metadata."""
    bot = message.bot

    # Send metadata line with chat_id (privacy-safe identifier)
    await bot.send_message(
        chat_id=config.support_chat_id,
        text=t.MSG_SUPPORT_ADMIN_NOTE.format(chat_id=message.chat.id),
    )

    # Forward the actual message
    await bot.forward_message(
        chat_id=config.support_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )

    # Send instruction for replying
    await bot.send_message(
        chat_id=config.support_chat_id,
        text=t.MSG_SUPPORT_REPLY_INSTRUCTION,
    )

    # Confirm to user and return to main menu
    await state.clear()
    track(message.chat.id, "support_message_sent")
    await message.answer(
        t.MSG_SUPPORT_FORWARDED,
        reply_markup=main_menu_kb(t, config.website_url),
    )


@router.message(F.reply_to_message)
async def admin_reply(
    message: Message,
    config: AppConfig,
    t: Texts,
) -> None:
    """Route admin's reply back to the user via chat_id from metadata."""
    # Only process messages from the admin chat
    if message.chat.id != config.support_chat_id:
        return

    bot = message.bot
    replied = message.reply_to_message
    user_chat_id = None

    # Try 1: forward_from (works if user hasn't enabled privacy)
    if replied and replied.forward_from:
        user_chat_id = replied.forward_from.id

    # Try 2: parse chat_id from metadata message
    if not user_chat_id and replied and replied.text:
        match = re.search(r"chat_id:\s*(\d+)", replied.text)
        if match:
            user_chat_id = int(match.group(1))

    if not user_chat_id:
        await message.answer(t.MSG_SUPPORT_NO_REPLY_TARGET)
        return

    # Send admin's reply to the user
    await bot.send_message(
        chat_id=user_chat_id,
        text=f"{t.MSG_SUPPORT_REPLY_PREFIX}\n\n{message.text}",
    )
    logger.info("Support reply sent to chat_id={}", user_chat_id)
