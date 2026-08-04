"""Onboarding handler — phone input, validation, and registration."""


from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from core.i18n import Texts
from bot.analytics import track
from core.config import AppConfig
from bot.keyboards import main_menu_kb, share_phone_kb
from bot.screen import typing
from core.adapters.keycrm.client import KeyCRMClient
from core.domain.phone import VerifiedPhone, verified_phone
from core.usecases.register import register_customer
from bot.states import OnboardingStates

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
) -> None:
    """Register the customer, then show them the menu.

    The phone is ownership-verified before this is called — see
    own_contact_phone above, which is the whole security boundary of the flow.
    """
    await register_customer(message.chat.id, phone, keycrm)

    await state.clear()
    track(message.chat.id, "registered")
    # Sending a reply keyboard replaces the share-phone one, so registration
    # ends with the menu already under the customer's thumb.
    await message.answer(
        f"{t.MSG_PHONE_VERIFIED}\n\n{t.MSG_MAIN_MENU}", reply_markup=main_menu_kb(t)
    )


@router.message(OnboardingStates.waiting_phone, F.contact)
async def process_contact(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    keycrm: KeyCRMClient,
    t: Texts,
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
        await message.answer(t.ERR_INVALID_PHONE, reply_markup=share_phone_kb(t))
        return
    logger.info("Verified own contact registered for chat {}", message.chat.id)

    track(message.chat.id, "contact_shared")
    # Registration fetches the buyer and their orders, which takes a moment;
    # "typing…" covers it without leaving a "Номер прийнято!" message behind to
    # be read minutes later as if it were news.
    await typing(message)
    await _register_user(message, state, phone, config, t, keycrm=keycrm)


@router.message(OnboardingStates.waiting_phone)
async def reject_typed_phone(message: Message, t: Texts) -> None:
    """Refuse manually typed numbers — ownership can't be proven, so allowing
    them would expose another person's orders. User must tap the button."""
    track(message.chat.id, "contact_rejected", reason="typed")
    await message.answer(t.MSG_USE_SHARE_BUTTON, reply_markup=share_phone_kb(t))
