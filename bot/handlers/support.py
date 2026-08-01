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
from bot.db import get_user_language, remember_support_thread, support_thread_owner
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
    note = await bot.send_message(
        chat_id=config.support_chat_id,
        text=op.MSG_SUPPORT_ADMIN_NOTE.format(chat_id=message.chat.id),
    )

    # Forward the actual message
    forwarded = await bot.forward_message(
        chat_id=config.support_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )

    # Send instruction for replying
    instruction = await bot.send_message(
        chat_id=config.support_chat_id,
        text=op.MSG_SUPPORT_REPLY_INSTRUCTION,
    )

    # All three, because a manager replies to whichever of them is under their
    # thumb — most often the forwarded text, which is the one that carries no
    # usable sender when the customer has forwarding privacy on.
    await remember_support_thread(
        [note.message_id, forwarded.message_id, instruction.message_id],
        message.chat.id,
    )

    # Confirm to user and return to main menu
    await state.clear()
    track(message.chat.id, "support_message_sent")
    # No keyboard to attach: the menu is already under the input field.
    await message.answer(t.MSG_SUPPORT_FORWARDED)


async def _reply_target(replied: Message | None) -> int | None:
    """Which customer this reply is aimed at.

    The table answers for anything sent since it existed. The two guesses below
    it are what the bot used to rely on entirely, kept only for threads that
    predate the table:

      - `forward_from` is absent whenever the customer has forwarding privacy
        enabled, which is the default for a large share of accounts;
      - the metadata line is only readable when the manager happened to reply to
        that exact message rather than to the forwarded text sitting under it.
    """
    if replied is None:
        return None
    owner = await support_thread_owner(replied.message_id)
    if owner is not None:
        return owner
    if replied.forward_from:
        return replied.forward_from.id
    if replied.text:
        match = re.search(r"chat_id:\s*(\d+)", replied.text)
        if match:
            return int(match.group(1))
    return None


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
    """Route admin's reply back to the customer the thread belongs to."""
    # Only process messages from the admin chat
    if message.chat.id != config.support_chat_id:
        return

    bot = message.bot
    replied = message.reply_to_message
    user_chat_id = await _reply_target(replied)

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

    if message.text:
        await bot.send_message(
            chat_id=user_chat_id,
            text=f"{ct.MSG_SUPPORT_REPLY_PREFIX}\n\n{message.text}",
        )
    else:
        # A photo, a voice note or a document. The previous version sent
        # `message.text` regardless, and for anything but text that is None —
        # so the customer received the prefix followed by the word "None" and
        # the manager had no way of knowing. copy_message carries whatever was
        # actually sent, caption included, and hides that it came from the
        # support chat.
        await bot.send_message(chat_id=user_chat_id, text=ct.MSG_SUPPORT_REPLY_PREFIX)
        await bot.copy_message(
            chat_id=user_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )

    logger.info("Support reply sent to chat_id={}", user_chat_id)
