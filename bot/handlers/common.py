"""Common command handlers — /start and other global commands."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.i18n import LANGUAGE_NAMES, Texts
from bot.analytics import track
from bot.config import AppConfig
from bot.db import get_user, get_user_language, is_opted_out, opt_in_user
from bot.keyboards import language_kb, main_menu_kb, share_phone_kb
from bot.profile import ensure_menu_button
from bot.states import OnboardingStates

router = Router()


async def _maybe_offer_language(
    message: Message, t: Texts, lang: str, tg_lang: str
) -> None:
    """Offer to switch when Telegram's language isn't the one we're speaking.

    The bot already answers in the Telegram language when it supports it, so
    this normally fires for a user we've just switched TO — the offer is their
    way back to Ukrainian. It is skipped once a choice has been stored.
    """
    if tg_lang == lang or await get_user_language(message.chat.id):
        return
    await message.answer(
        t.MSG_LANGUAGE_OFFER.format(language=LANGUAGE_NAMES[tg_lang]),
        reply_markup=language_kb(lang),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    config: AppConfig,
    state: FSMContext,
    t: Texts,
    lang: str,
    tg_lang: str,
) -> None:
    """Handle /start command — greet new users or welcome back returning ones."""
    # Always clear any active FSM state (e.g. user sends /start mid-onboarding)
    await state.clear()

    # The button in the input row that opens the menu underneath it. Set for
    # this chat by name, not left to the global default, which a per-chat
    # setting silently overrides.
    await ensure_menu_button(message.bot, message.chat.id)

    # Re-subscribe if user was opted out of broadcasts
    if await is_opted_out(message.chat.id):
        await opt_in_user(message.chat.id)
        track(message.chat.id, "opted_in")
        await message.answer(t.MSG_OPT_IN_CONFIRM)

    # Returning user — already verified, show main menu
    user = await get_user(message.chat.id)
    track(message.chat.id, "start", returning=bool(user), lang=lang)
    if user:
        if user.get("full_name"):
            greeting = t.MSG_WELCOME_BACK_NAME.format(name=user["full_name"])
        else:
            greeting = t.MSG_WELCOME_BACK
        # One message: the greeting carries the menu keyboard, and sending a
        # keyboard replaces whatever the chat had before it.
        await message.answer(
            f"{greeting}\n\n{t.MSG_MAIN_MENU}", reply_markup=main_menu_kb(t)
        )
        await _maybe_offer_language(message, t, lang, tg_lang)
        return

    await _maybe_offer_language(message, t, lang, tg_lang)

    # New user — send greeting with share-phone keyboard (request_contact only)
    greeting = t.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting, reply_markup=share_phone_kb(t))
    await state.set_state(OnboardingStates.waiting_phone)
