"""Support relay — user-to-admin forwarding and admin-to-user reply."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from core.i18n import Texts, customer_texts, operator_texts
from bot.analytics import track
from bot.config import AppConfig
from bot.db import (album_in_progress, get_user_language, remember_support_thread,
                    start_album, support_thread_owner)
from bot.states import SupportStates

router = Router()


@router.message(SupportStates.waiting_message)
async def forward_to_support(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Forward user's message to admin chat with metadata.

    An album is several messages sharing a media_group_id, and Telegram gives no
    signal for the last one. Only the first claims the album; the rest are
    forwarded into the same thread without repeating the metadata line, the
    instruction or the confirmation to the customer. Before this, the state was
    cleared by the first part and every later photo matched no handler at all,
    so a customer sending three photos had two of them disappear.
    """
    bot = message.bot
    album = message.media_group_id
    first_of_album = True
    if album:
        first_of_album = await start_album(message.chat.id, album)

    # These two go to the support chat, not to the customer. `t` is the
    # customer's language — using it here made the managers' own note and
    # instructions change language depending on who happened to write in.
    op = operator_texts()

    thread_ids: list[int] = []

    if first_of_album:
        # Send metadata line with chat_id (privacy-safe identifier)
        note = await bot.send_message(
            chat_id=config.support_chat_id,
            text=op.MSG_SUPPORT_ADMIN_NOTE.format(chat_id=message.chat.id),
        )
        thread_ids.append(note.message_id)

    # Forward the actual message. forward_message carries whatever the customer
    # sent — photo, voice, video note, document — so attachments reach the
    # manager unchanged in this direction.
    forwarded = await bot.forward_message(
        chat_id=config.support_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )
    thread_ids.append(forwarded.message_id)

    if first_of_album:
        # Send instruction for replying
        instruction = await bot.send_message(
            chat_id=config.support_chat_id,
            text=op.MSG_SUPPORT_REPLY_INSTRUCTION,
        )
        thread_ids.append(instruction.message_id)

    # Every bot-sent message of the request, because a manager replies to
    # whichever is under their thumb — most often the forwarded one, which is
    # exactly the one carrying no usable sender when the customer has
    # forwarding privacy on. Each part of an album is registered too, so a
    # reply to any photo reaches the right person.
    await remember_support_thread(thread_ids, message.chat.id)

    if not first_of_album:
        # A later part of an album: already confirmed, state already cleared.
        return

    # Confirm to user and return to main menu
    await state.clear()
    track(message.chat.id, "support_message_sent")
    # No keyboard to attach: the menu is already under the input field.
    await message.answer(t.MSG_SUPPORT_FORWARDED)


@router.message(StateFilter(None), F.media_group_id)
async def forward_album_tail(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    t: Texts,
) -> None:
    """Later parts of an album whose first part already cleared the state.

    Without this they match nothing: the state filter above no longer applies
    and there is no other handler for a photo. Scoped to an album this chat is
    already sending, so an unrelated album sent later is not silently forwarded
    to support.
    """
    if message.chat.id == config.support_chat_id:
        return
    if not await album_in_progress(message.chat.id, message.media_group_id):
        return
    await forward_to_support(message, state, config, t)


async def _reply_target(replied: Message | None) -> int | None:
    """Which customer this reply is aimed at. The table, and nothing else.

    Two guesses used to sit under this — `forward_from`, and a regex for the
    chat_id in the metadata line — and both were removed rather than kept as a
    fallback. They are what the failure was: `forward_from` is empty whenever
    the customer has forwarding privacy on, and the metadata line is only
    readable if the manager replied to that exact message. Keeping them meant a
    reply could still be routed by a guess, and a guess that is right most of
    the time is worse than an error, because the time it is wrong the message
    goes to a stranger.

    Threads that predate the table are not migrated: there were three users when
    it shipped. Replying to one now produces the visible error below, which is
    the correct outcome — the manager retries in the current thread.
    """
    if replied is None:
        return None
    return await support_thread_owner(replied.message_id)


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
