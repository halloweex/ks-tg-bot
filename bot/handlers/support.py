"""Support relay — user-to-admin forwarding and admin-to-user reply."""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from bot.i18n import Texts, customer_texts, operator_texts
from bot.analytics import track
from bot.config import AppConfig
from bot.db import get_user_language
from bot.states import SupportStates

router = Router()


@router.message(SupportStates.waiting_message)
async def forward_to_support(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Forward user's message to admin chat with metadata."""
    bot = message.bot

    # These two go to the support chat, not to the customer. `t` is the
    # customer's language — using it here made the managers' own note and
    # instructions change language depending on who happened to write in.
    op = operator_texts()

    # Send metadata line with chat_id (privacy-safe identifier)
    await bot.send_message(
        chat_id=config.support_chat_id,
        text=op.MSG_SUPPORT_ADMIN_NOTE.format(chat_id=message.chat.id),
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
        text=op.MSG_SUPPORT_REPLY_INSTRUCTION,
    )

    # Confirm to user and return to main menu
    await state.clear()
    track(message.chat.id, "support_message_sent")
    # No keyboard to attach: the menu is already under the input field.
    await message.answer(t.MSG_SUPPORT_FORWARDED)


# Narrow on purpose. support_chat_id is often an admin's own DM with the bot, and
# this router sits ahead of onboarding — so a bare `F.reply_to_message` swallowed
# anything the admin sent as a reply in that chat, including the contact shared
# during /start, which then never reached registration.
#   StateFilter(None) — never intercept a flow in progress (onboarding, settings)
#   ~F.contact        — a shared contact is never a support reply
@router.message(StateFilter(None), F.reply_to_message, ~F.contact)
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
        # Only complain about a reply that was plausibly aimed at a customer:
        # replying to something else in this chat is not a support action, and
        # answering it would be noise.
        if replied and replied.from_user and replied.from_user.is_bot:
            await message.answer(operator_texts().MSG_SUPPORT_NO_REPLY_TARGET)
        return

    # This one goes to the customer, so it must be in *their* language. `t` here
    # belongs to the manager who typed the reply — using it sent a Ukrainian
    # customer an English prefix whenever the manager's Telegram was English.
    ct = customer_texts(await get_user_language(user_chat_id))
    await bot.send_message(
        chat_id=user_chat_id,
        text=f"{ct.MSG_SUPPORT_REPLY_PREFIX}\n\n{message.text}",
    )
    logger.info("Support reply sent to chat_id={}", user_chat_id)
