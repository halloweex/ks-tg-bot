"""One live screen per chat.

Every menu, list and confirmation reached by tapping a button is the *same*
message, edited in place. A session used to leave a trail: opening the menu,
the orders, the favourites and coming back sent five messages, and the customer
scrolled through their own navigation history looking for the order list. Now
the screen changes under their thumb and the chat keeps only what the shop
actually said.

Falls back to sending a new message whenever an edit cannot work — the anchor
may be a photo, older than Telegram's edit window, or already gone.

Three smaller things about how the chat looks live here too, for the same
reason: `seen()` marks a customer's message as read with a reaction, `ephemeral()`
says something and takes it back, and an effect is how a message arrives. All
three are decoration — every one of them fails silently, because a chat that
looks slightly plainer is never worth a broken flow.
"""
from __future__ import annotations

import asyncio
from typing import Sequence

from aiogram.types import (CallbackQuery, InlineKeyboardMarkup, Message,
                           ReactionTypeEmoji, ReplyKeyboardRemove)
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from loguru import logger

from bot import rich
from bot.tasks import spawn

from core.config import AppConfig
from core.i18n import Texts
from bot.keyboards import main_menu_inline_kb, main_menu_kb

# Telegram's wording when the new text and markup are identical to the old ones.
# It means the screen is already showing what we asked for, which is a success,
# not a failure — a double tap should not spawn a duplicate message.
_NOT_MODIFIED = "message is not modified"


# How long a message that answers an action stays on screen. Long enough to be
# read twice, short enough that a chat scrolled through next week holds what the
# shop said and not the chatter around it.
_EPHEMERAL_SECONDS = 45

# What a customer's message is marked with when it reaches the manager. Reading
# it is the manager's job; this only says it arrived, and it says so on the
# message itself, where the customer is already looking.
_SEEN = "👀"


async def seen(message: Message) -> None:
    """Mark a customer's own message as arrived, with a reaction on it.

    Better than a line of text saying the same: it sits on the message it is
    about, it survives scrolling, and it does not have to be read. Telegram
    allows reactions only from a fixed set of emoji, so a rejected one is
    logged and forgotten — the message was still delivered.
    """
    try:
        await message.react([ReactionTypeEmoji(emoji=_SEEN)])
    except TelegramAPIError as exc:
        logger.debug("Could not react to a message: {}", exc)


async def with_effect(message: Message, text: str, effect: str,
                      **kwargs) -> Message:
    """Answer with an effect over the bubble, or plainly if it is refused.

    The ids are undocumented and read off a client (core/effects.py), so one of
    them going stale has to cost the animation and nothing else. Same fallback
    the outbox has for the same reason.
    """
    if effect:
        try:
            return await message.answer(text, message_effect_id=effect, **kwargs)
        except TelegramBadRequest as exc:
            if "effect" not in exc.message.lower():
                raise
            logger.debug("Message effect rejected ({}), sending plain", exc.message)
    return await message.answer(text, **kwargs)


async def ephemeral(message: Message, text: str,
                    seconds: int = _EPHEMERAL_SECONDS, **kwargs) -> Message | None:
    """Say something and take it back once it has been read.

    For the messages that answer an action rather than carry information — "sent
    to the manager", "here is the shop" — and are noise a day later. The chat
    keeps one live screen and what the shop actually said; this is how the rest
    stops accumulating.

    Deletion is a background task, so nothing waits on it, and a redeploy in the
    meantime leaves the message where it is. Cosmetic either way.
    """
    sent = await message.answer(text, **kwargs)
    spawn(_forget(sent, seconds), name="ephemeral")
    return sent


async def _forget(sent: Message, seconds: int) -> None:
    """Delete a message after `seconds`, and shrug if it is already gone."""
    await asyncio.sleep(seconds)
    try:
        await sent.delete()
    except TelegramAPIError as exc:
        logger.debug("Could not delete an ephemeral message: {}", exc)


async def send_main_menu(message: Message, t: Texts, config: AppConfig,
                         intro: str = "", effect: str = "") -> None:
    """Put both menus on screen: the keyboard below, and the one in a message.

    Two messages because Telegram allows a message only one markup, and these
    are two kinds: the keyboard under the input field and the buttons in the
    bubble. The first carries whatever we were going to say anyway — a
    greeting, a confirmation — and the second is the menu itself.

    Why both at all: only a reply keyboard draws the ☰ toggle in the input row,
    and it never scrolls away; the menu in the message is what stays reachable
    once this one has.

    With `bottom_menu` off the first message carries a ReplyKeyboardRemove
    instead, which is the only way to take a keyboard off a screen it is
    already on — a reply keyboard belongs to the message that sent it, and
    nothing updates the one somebody is looking at. The keys keep working for
    anyone who has not been sent a message since, because the handlers that
    match them are still there.
    """
    below = main_menu_kb(t) if config.bottom_menu else ReplyKeyboardRemove()
    await with_effect(message, intro or t.MSG_MAIN_MENU, effect,
                      reply_markup=below)
    await message.answer(t.MSG_MENU_PICK,
                         reply_markup=main_menu_inline_kb(t, config.website_url))


async def render(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    blocks: Sequence[object] | None = None,
) -> Message | None:
    """Show this screen on the message the callback came from.

    `text` is always required, even when `blocks` are given: it is the screen
    for a reader whose client cannot draw blocks, and there is no way to detect
    one — Telegram answers 200 and the degrading happens on the device.

    **Plain text written over a rich screen destroys it, silently.** Measured
    against the live API (`docs/rich-messages.md`): the edit succeeds, the
    message id is unchanged, and `rich_message` is simply gone. No exception,
    nothing in the log. Since seventeen call sites reach this function, a
    half-migrated screen would decay to plain the first time any of them was
    tapped, and nobody would learn why — so the case is detected here and said
    out loud rather than left to the discipline of callers.

    It is reported and then done anyway. The alternative is refusing the edit,
    which leaves the customer tapping a screen that never changes — and silence
    is the thing this bot has spent two commits removing.
    """
    message = callback.message
    if message is None:
        # Telegram drops the message from very old callbacks.
        return None
    if not isinstance(message, Message):
        # An InaccessibleMessage: the anchor is older than Telegram keeps, or
        # was deleted. It has an id and a chat and nothing else — calling
        # edit_text on it raised AttributeError, which reached the customer as
        # silence instead of as the new screen this exists to draw.
        return await callback.bot.send_message(message.chat.id, text,
                                               reply_markup=reply_markup)
    # Blocks win wherever they are offered, including over a plain anchor:
    # editing a plain message into a rich one is allowed and was verified
    # against the live API. Only the reverse is dangerous, and that is what the
    # complaint below is for — so the two entrances to a screen cannot end up
    # showing different things depending on which message they edit.
    anchor_is_rich = getattr(message, "rich_message", None) is not None

    # A screen keeps the shape it was born with, and this line is what makes
    # the admin gate in `orders_screen` mean anything. The redraw handlers —
    # show_order, track_parcel, _fill_in_parcel — build their blocks straight
    # from `rich_orders_blocks` and never go through `orders_screen`, so they
    # never see the gate. Without this, a customer whose screen was sent plain
    # got it silently converted to rich by her first tap, which is exactly the
    # audience the gate exists to keep away from it.
    if not anchor_is_rich:
        blocks = None

    if anchor_is_rich and blocks is None:
        logger.error(
            "Plain text written over a rich screen (chat {}, message {}): the "
            "blocks are destroyed and will not come back until something "
            "redraws it rich. A caller of render() was missed by the migration.",
            message.chat.id, message.message_id)

    try:
        if blocks is not None:
            edited = await rich.edit(message, blocks, reply_markup=reply_markup)
        else:
            edited = await message.edit_text(text, reply_markup=reply_markup)
        return edited if isinstance(edited, Message) else message
    except TelegramBadRequest as exc:
        if _NOT_MODIFIED in exc.message:
            # Verified against the live API: Telegram uses this same wording
            # for a rich edit, so one guard covers both shapes.
            return message
        logger.debug("edit failed ({}), sending a new screen", exc.message)
        if blocks is not None:
            return await rich.send(message.bot, message.chat.id, blocks,
                                   plain=text, reply_markup=reply_markup)
        return await message.answer(text, reply_markup=reply_markup)


async def typing(message: Message) -> None:
    """Show "typing…" so a KeyCRM round-trip does not look like a hang.

    Replaced the "Завантажую…" message it used to send, which stayed in the
    chat forever to report something that had finished seconds earlier.
    """
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except Exception:  # noqa: BLE001 — never let a cosmetic call break a flow
        pass


# Taking a reply keyboard away has no helper on purpose. It was one — a stub
# message carrying ReplyKeyboardRemove, deleted immediately so as not to litter
# the chat — and it did not work: the client ties the keyboard's state to the
# message that changed it, so deleting that message put the keyboard back. The
# grid icon in the input row stayed, and with it the reason the menu button had
# nowhere to appear.
#
# So the instruction has to ride on a message that stays: the greeting on
# /start, the confirmation after a phone is verified. A message carries either
# ReplyKeyboardRemove or inline buttons, never both, which is why those flows
# send two messages and the menu is always the second one.
