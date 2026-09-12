"""Common command handlers — /start and other global commands."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.i18n import LANGUAGE_NAMES, Texts
from bot.analytics import track
from core.config import AppConfig
from core.repos.users import get_user, get_user_language, is_opted_out, opt_in_user
from bot import rich
from bot.keyboards import language_kb, share_phone_kb
from bot.screen import send_main_menu
from bot.handlers.orders import (favourites_screen, follow_up_parcel,
                                 orders_screen)
from core.adapters.keycrm.client import KeyCRMClient
from core.adapters.novaposhta.client import NovaPoshtaClient
from bot.profile import ensure_menu_button
from bot.states import OnboardingStates

router = Router()

# What the buttons above the inline lists send to /start — one per list. Their
# own names because two modules have to agree on the strings: the one that puts
# them on the buttons (bot/handlers/inline.py) and this one, which reads them.
FAVOURITES_DEEP_LINK = "favourites"
ORDERS_DEEP_LINK = "orders"

# And what a recommendation carries: "ref_" and the chat id of whoever shared
# it. Written into `users.source` when — and only when — the person it brought
# registers, so the column answers "who brought them" and keeps answering it
# after a later visit through somebody else's link.
REFERRAL_PREFIX = "ref_"


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
    command: CommandObject,
    config: AppConfig,
    state: FSMContext,
    keycrm: KeyCRMClient,
    novaposhta: NovaPoshtaClient | None,
    t: Texts,
    lang: str,
    tg_lang: str,
    customer_name: str = "",
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
        # **It used to take itself back after 45 seconds**, as "a status notice
        # that means nothing an hour later". It means a great deal: she opted
        # out of the mailing on purpose, pressed /start for some other reason,
        # and was put back on the list. This line is the only thing telling her
        # so, and a notice about her own subscription that disappears while she
        # is reading the greeting under it is worse than no notice at all.
        await message.answer(t.MSG_OPT_IN_CONFIRM)

    # Returning user — already verified, show main menu
    user = await get_user(message.chat.id)
    track(message.chat.id, "start", returning=bool(user), lang=lang)
    if user:
        if user.get("full_name"):
            greeting = t.MSG_WELCOME_BACK_NAME.format(name=user["full_name"])
        else:
            greeting = t.MSG_WELCOME_BACK
        # Arriving from the button above an inline list: the customer is
        # already looking at their products or their orders and asked for what
        # the list cannot carry — the discount, the subscription, the paging.
        # Straight to that screen, no menu in the way: the keyboard they tapped
        # from is already on their screen.
        payload = command.args or ""
        if payload == FAVOURITES_DEEP_LINK:
            screen = await favourites_screen(
                message.chat.id, t, keycrm, message, config.website_url, config
            )
            await rich.send(message.bot, message.chat.id, screen.blocks or [],
                            plain=screen.text, reply_markup=screen.markup,
                            admins=config.env.admin_ids)
            return
        if payload == ORDERS_DEEP_LINK:
            screen = await orders_screen(message.chat.id, t, keycrm, message,
                                         config)
            sent = await rich.send(message.bot, message.chat.id,
                                   screen.blocks or [], plain=screen.text,
                                   reply_markup=screen.markup,
                                   admins=config.env.admin_ids)
            follow_up_parcel(sent, message.chat.id, t, novaposhta)
            return

        # The greeting carries the menu keyboard — sending a keyboard replaces
        # whatever the chat had before it — and the menu itself follows as
        # buttons in a message, which is the only kind that can hand the input
        # field to the inline list.
        await send_main_menu(message, t, config, greeting, name=customer_name)
        await _maybe_offer_language(message, t, lang, tg_lang)
        return

    await _maybe_offer_language(message, t, lang, tg_lang)

    # New user — send greeting with share-phone keyboard (request_contact only)
    greeting = t.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting, reply_markup=share_phone_kb(t))
    await state.set_state(OnboardingStates.waiting_phone)

    # Where they came from, kept until registration — which is the only moment
    # it is written, and the only moment it means "first touch". The FSM store
    # is SQLite, so a redeploy between the two does not lose it.
    payload = command.args or ""
    if payload.startswith(REFERRAL_PREFIX):
        await state.update_data(source=payload)
        track(message.chat.id, "referral_arrived", ref=payload[len(REFERRAL_PREFIX):])
