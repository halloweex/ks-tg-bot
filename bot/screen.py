"""One live screen per chat.

Every menu, list and confirmation reached by tapping a button is the *same*
message, edited in place. A session used to leave a trail: opening the menu,
the orders, the favourites and coming back sent five messages, and the customer
scrolled through their own navigation history looking for the order list. Now
the screen changes under their thumb and the chat keeps only what the shop
actually said.

Falls back to sending a new message whenever an edit cannot work — the anchor
may be a photo, older than Telegram's edit window, or already gone.
"""
from __future__ import annotations

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from core.config import AppConfig
from core.i18n import Texts
from bot.keyboards import main_menu_inline_kb, main_menu_kb

# Telegram's wording when the new text and markup are identical to the old ones.
# It means the screen is already showing what we asked for, which is a success,
# not a failure — a double tap should not spawn a duplicate message.
_NOT_MODIFIED = "message is not modified"


async def send_main_menu(message: Message, t: Texts, config: AppConfig,
                         intro: str = "") -> None:
    """Put both menus on screen: the keyboard below, and the one in a message.

    Two messages because Telegram allows a message only one markup, and these
    are two kinds: the keyboard under the input field and the buttons in the
    bubble. The first carries whatever we were going to say anyway — a
    greeting, a confirmation — and the second is the menu itself.

    Why both at all: only a reply keyboard draws the ☰ toggle in the input row,
    and it never scrolls away; the menu in the message is what stays reachable
    once this one has. Both open the inline list from «⭐ Улюблені» — the one
    below through the Mini App in webapp/, the one here through the button type
    that does it natively (bot/keyboards.py).
    """
    await message.answer(intro or t.MSG_MAIN_MENU,
                         reply_markup=main_menu_kb(t, config.webapp_url))
    await message.answer(t.MSG_MENU_PICK,
                         reply_markup=main_menu_inline_kb(t, config.website_url))


async def render(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """Show `text` on the screen the callback came from."""
    message = callback.message
    if message is None:
        # Telegram drops the message from very old callbacks.
        return None
    try:
        edited = await message.edit_text(text, reply_markup=reply_markup)
        return edited if isinstance(edited, Message) else message
    except TelegramBadRequest as exc:
        if _NOT_MODIFIED in exc.message:
            return message
        logger.debug("edit_text failed ({}), sending a new screen", exc.message)
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
