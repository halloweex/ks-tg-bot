"""The referral screen: one link, two numbers, one button.

What a customer can do here is send the bot to a friend — and what the screen
owes her in return is an honest account of where that stands. Two numbers
rather than one: somebody whose friend has opened the bot but not ordered yet
would otherwise read a zero and conclude it does not work.

The reward itself is not written here. Its size is a commitment the business
makes, so the line describing it lives in config.yaml — and the promise the bot
makes on its own is only the one it can keep: when the friend orders, a code
comes into this chat.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import MenuAction
from core.config import AppConfig
from core.i18n import Texts
from core.repos.referrals import referral_counts
from bot.handlers.common import REFERRAL_PREFIX


async def invite_screen(chat_id: int, t: Texts,
                        config: AppConfig) -> tuple[str, InlineKeyboardMarkup]:
    """The screen, ready to be sent or edited into place."""
    invited, earned = await referral_counts(chat_id, REFERRAL_PREFIX)

    lines = [t.MSG_INVITE_SCREEN]
    if config.referral_reward:
        lines += ["", config.referral_reward]
    if invited:
        lines += ["", t.MSG_INVITE_COUNTS.format(invited=invited, earned=earned)]

    return "\n".join(lines), _invite_kb(t)


def _invite_kb(t: Texts) -> InlineKeyboardMarkup:
    """Send it, or go back.

    The sending button is the same mechanism the product cards use: Telegram
    asks which chat, opens it, and writes the query — which the inline handler
    answers with a card about the bot itself (bot/handlers/inline.py).
    """
    from aiogram.types import SwitchInlineQueryChosenChat

    builder = InlineKeyboardBuilder()
    builder.button(
        text=t.BTN_INVITE_SEND,
        switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
            query=t.MSG_INLINE_SHARE_PREFIX,
            allow_user_chats=True,
            allow_group_chats=True,
        ),
    )
    builder.button(text=t.BTN_MENU, callback_data=MenuAction(action="menu"))
    builder.adjust(1)
    return builder.as_markup()
