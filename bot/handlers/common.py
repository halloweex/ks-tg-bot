"""Common command handlers — /start and other global commands."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from bot.i18n import LANGUAGE_NAMES, Texts
from bot.config import AppConfig
from bot.db import get_user, get_user_language, get_user_phone, is_opted_out, opt_in_user
from bot.keyboards import language_kb, main_menu_kb, share_phone_kb
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

    # Re-subscribe if user was opted out of broadcasts
    if await is_opted_out(message.chat.id):
        await opt_in_user(message.chat.id)
        await message.answer(t.MSG_OPT_IN_CONFIRM)

    # Returning user — already verified, show main menu
    user = await get_user(message.chat.id)
    if user:
        if user.get("full_name"):
            greeting = t.MSG_WELCOME_BACK_NAME.format(name=user["full_name"])
        else:
            greeting = t.MSG_WELCOME_BACK
        await message.answer(greeting, reply_markup=ReplyKeyboardRemove())
        await message.answer(t.MSG_MAIN_MENU, reply_markup=main_menu_kb(t, config.website_url))
        await _maybe_offer_language(message, t, lang, tg_lang)
        return

    await _maybe_offer_language(message, t, lang, tg_lang)

    # New user — send greeting with share-phone keyboard (request_contact only)
    greeting = t.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting, reply_markup=share_phone_kb(t))
    await state.set_state(OnboardingStates.waiting_phone)
