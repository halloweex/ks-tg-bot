"""Onboarding handler — phone input, validation, and registration."""


from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from core.i18n import Texts, variants
from bot.analytics import track
from core.config import AppConfig
from core.effects import CONFETTI
from bot.handlers.support import begin_support
from bot.keyboards import menu_kb, share_phone_kb
from bot.handlers.orders import first_order_kb, first_order_offer
from bot.screen import send_main_menu, typing
from core.adapters.keycrm.client import KeyCRMClient
from core.domain.phone import VerifiedPhone, verified_phone
from core.repos.orders import get_cached_orders
from core.repos.uow import SqliteUnitOfWork
from core.ports.gender import BuyerGenders
from core.repos.users import SqliteCustomerDirectory, SqliteGenderForm
from core.usecases.gender import refresh_forms
from core.usecases.register import register_customer
from bot.states import OnboardingStates, SupportStates

router = Router()


def own_contact_phone(message: Message) -> VerifiedPhone | None:
    """The sender's own verified phone from a shared contact, else None.

    Three lines of Telegram and no rule: the rule is core.domain.phone, which
    takes the three facts this pulls out of the message. What used to be a
    convention — "everyone downstream trusts that this function was the one that
    produced the string" — is now the type, and a bare string does not become a
    VerifiedPhone anywhere in the tree.
    """
    contact = message.contact
    return verified_phone(
        raw_number=contact.phone_number if contact else None,
        contact_user_id=contact.user_id if contact else None,
        sender_user_id=message.from_user.id if message.from_user else None,
    )


async def _register_user(
    message: Message,
    state: FSMContext,
    phone: VerifiedPhone,
    config: AppConfig,
    t: Texts,
    keycrm: KeyCRMClient | None = None,
    genders: BuyerGenders | None = None,
) -> None:
    """Register the customer, then show them the menu.

    The phone is ownership-verified before this is called — see
    own_contact_phone above, which is the whole security boundary of the flow.

    This is also the moment the bot can first learn how to address them: the
    CRM buyer cards behind their number are written one line above, and the
    warehouse can be asked about exactly those. Doing it here rather than
    leaving it to the hourly sweep is the difference between a man reading
    «Красуне» on the first screen after registering and never reading it at all.
    """
    # The deep link that brought them, put aside by /start a couple of messages
    # ago. Written once, here, because this is where a chat becomes a customer.
    source = str((await state.get_data()).get("source") or "")
    # Built here, as in the other handlers: no composition root exists yet, so
    # this is where it is known which engine is underneath.
    await register_customer(message.chat.id, phone, keycrm,
                            SqliteCustomerDirectory(), SqliteUnitOfWork,
                            source=source)

    # Best-effort, like everything else registration does to somebody else's
    # data: the warehouse is another project's database and the customer is
    # standing in front of us. `t` is rebuilt rather than re-read, because the
    # middleware resolved it before the row it reads from was written.
    if genders is not None:
        try:
            decided = await refresh_forms(SqliteCustomerDirectory(), genders,
                                          SqliteGenderForm(),
                                          only={message.chat.id})
        except Exception as exc:  # noqa: BLE001 — never costs a registration
            logger.warning("Could not resolve how to address chat {}: {}",
                           message.chat.id, exc)
        else:
            if message.chat.id in decided:
                t = Texts(t.lang, decided[message.chat.id])

    await state.clear()
    track(message.chat.id, "registered")
    # Sending a reply keyboard replaces the share-phone one, so registration
    # ends with the menu already under the customer's thumb — and with the same
    # menu in a message above it, where «⭐ Улюблені» opens the inline list.
    #
    # With confetti, which the restock notification has had for a while and
    # this moment deserves more: it happens once per customer, and what it says
    # is "we found you". Refused ids fall back to a plain message (bot/screen).
    await send_main_menu(message, t, config, t.MSG_PHONE_VERIFIED, effect=CONFETTI)

    # A customer the CRM has never heard of is a new one, and this is the
    # moment the offer means something. Said once, here — the empty orders and
    # favourites screens say it too, and whoever has bought before sees none
    # of it (bot/handlers/orders.py).
    offer = first_order_offer(t, config)
    if offer and not await get_cached_orders(message.chat.id):
        await message.answer(offer, reply_markup=first_order_kb(t, config))


@router.message(OnboardingStates.waiting_phone, F.contact)
async def process_contact(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    keycrm: KeyCRMClient,
    t: Texts,
    genders: BuyerGenders | None = None,
) -> None:
    """Register the user from their OWN shared contact (ownership-verified)."""
    if message.contact and message.contact.user_id != (message.from_user.id if message.from_user else None):
        # Forwarded / someone else's contact card — refuse.
        track(message.chat.id, "contact_rejected", reason="not_own")
        await message.answer(t.ERR_CONTACT_NOT_OWN, reply_markup=share_phone_kb(t))
        return

    phone = own_contact_phone(message)
    if not phone:
        track(message.chat.id, "contact_rejected", reason="invalid")
        await message.answer(
            t.ERR_INVALID_PHONE, reply_markup=share_phone_kb(t, with_manager=True)
        )
        return
    logger.info("Verified own contact registered for chat {}", message.chat.id)

    track(message.chat.id, "contact_shared")
    # Registration fetches the buyer and their orders, which takes a moment;
    # "typing…" covers it without leaving a "Номер прийнято!" message behind to
    # be read minutes later as if it were news.
    await typing(message)
    await _register_user(message, state, phone, config, t, keycrm=keycrm,
                         genders=genders)


@router.message(OnboardingStates.waiting_phone, F.text.in_(variants("BTN_SUPPORT")))
async def escape_to_support(message: Message, state: FSMContext,
                            config: AppConfig, t: Texts) -> None:
    """The exit from the share-phone flow, and the only one it has.

    Menu keys are filtered out while a number is being shared (see menu.py):
    for every other key that is right, because letting it through abandons the
    flow halfway. This one is different — it is offered by the keyboard the
    flow itself sends after a number it could not read, and pressing the share
    button again would only reproduce the refusal. Handled here, where the
    state is, and registered above the catch-all that would otherwise answer
    "the number cannot be typed" to a button the bot drew.
    """
    track(message.chat.id, "support_opened", source="share_phone")
    await message.answer(await begin_support(state, t, config),
                         reply_markup=menu_kb(t))


@router.message(OnboardingStates.waiting_phone)
async def reject_typed_phone(message: Message, t: Texts) -> None:
    """Refuse manually typed numbers — ownership can't be proven, so allowing
    them would expose another person's orders. User must tap the button."""
    track(message.chat.id, "contact_rejected", reason="typed")
    await message.answer(t.MSG_USE_SHARE_BUTTON, reply_markup=share_phone_kb(t))
