"""Onboarding handler — phone input, validation, and registration."""

import asyncio
import re
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from loguru import logger

from bot.i18n import Texts
from bot.analytics import track
from bot.config import AppConfig
from bot.db import save_user, upsert_orders
from bot.keyboards import main_menu_kb, share_phone_kb
from bot.screen import typing
from bot.services.keycrm import KeyCRMClient, keycrm_order_to_dict
from bot.services.shopify import ShopifyClient, shopify_order_to_dict
from bot.states import OnboardingStates

router = Router()

# E.164: + followed by 7-15 digits (covers all international numbers)
PHONE_PATTERN = re.compile(r"^\+\d{7,15}$")


def normalize_phone(raw: str | None) -> str | None:
    """Normalize any input to an E.164 number (+digits). Accepts all countries.

    Ukrainian local formats are handled for convenience:
      380XXXXXXXXX (12 digits) -> +380XXXXXXXXX
      0XXXXXXXXX   (10 digits) -> +380XXXXXXXXX
    Anything else becomes '+' + digits (so a US/UK/DE/etc. number typed with or
    without a leading '+' is accepted). Returns None if the result isn't a valid
    E.164 number.
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("380") and len(digits) == 12:
        phone = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        phone = "+38" + digits
    else:
        phone = "+" + digits
    return phone if PHONE_PATTERN.match(phone) else None


def own_contact_phone(message: Message) -> str | None:
    """Return the sender's *own* verified phone from a shared contact, else None.

    Security boundary: Telegram sets ``contact.user_id`` to the sharer's own id
    only when they tap the request_contact button to share THEIR number. A
    forwarded or address-book contact of another person has a different (or
    missing) user_id and is rejected — otherwise anyone could bind a victim's
    phone to their chat and read that person's orders + delivery address (IDOR).
    """
    contact = message.contact
    if not contact or not contact.phone_number:
        return None
    if not message.from_user or contact.user_id != message.from_user.id:
        return None
    return normalize_phone(contact.phone_number)


async def _sync_orders(
    chat_id: int, phone: str,
    keycrm: KeyCRMClient | None, shopify: ShopifyClient | None,
) -> None:
    """Fetch orders from APIs and cache locally (best-effort)."""
    coros = []
    if keycrm:
        coros.append(keycrm.get_orders_by_phone(phone))
    if shopify:
        coros.append(shopify.get_orders_by_phone(phone))
    if not coros:
        return

    results = await asyncio.gather(*coros, return_exceptions=True)

    db_rows: list[dict] = []
    idx = 0
    if keycrm:
        if not isinstance(results[idx], Exception):
            db_rows.extend(keycrm_order_to_dict(o, chat_id) for o in results[idx])
        idx += 1
    if shopify:
        if not isinstance(results[idx], Exception):
            db_rows.extend(shopify_order_to_dict(o, chat_id) for o in results[idx])

    if db_rows:
        await upsert_orders(chat_id, db_rows)


async def _register_user(
    message: Message,
    state: FSMContext,
    phone: str,
    config: AppConfig,
    t: Texts,
    keycrm: KeyCRMClient | None = None,
    shopify: ShopifyClient | None = None,
) -> None:
    """Save user, complete onboarding, and show main menu."""
    await save_user(message.chat.id, phone)

    # Enrich profile with KeyCRM buyer data (best-effort)
    if keycrm:
        try:
            buyer = await keycrm.get_buyer_by_phone(phone)
            if buyer:
                await save_user(
                    message.chat.id, phone,
                    full_name=buyer["full_name"], email=buyer["email"],
                )
        except Exception:
            logger.debug("Buyer profile sync failed for {}", phone)

    # Sync orders into local cache (best-effort, don't block onboarding)
    try:
        await _sync_orders(message.chat.id, phone, keycrm, shopify)
    except Exception:
        logger.debug("Order sync on registration failed for {}", phone)

    await state.clear()
    track(message.chat.id, "registered")
    await message.answer(t.MSG_PHONE_VERIFIED, reply_markup=ReplyKeyboardRemove())
    await message.answer(t.MSG_MAIN_MENU, reply_markup=main_menu_kb(t, config.website_url))


@router.message(OnboardingStates.waiting_phone, F.contact)
async def process_contact(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    keycrm: KeyCRMClient,
    shopify: Optional[ShopifyClient],
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
    await _register_user(message, state, phone, config, t, keycrm=keycrm, shopify=shopify)


@router.message(OnboardingStates.waiting_phone)
async def reject_typed_phone(message: Message, t: Texts) -> None:
    """Refuse manually typed numbers — ownership can't be proven, so allowing
    them would expose another person's orders. User must tap the button."""
    track(message.chat.id, "contact_rejected", reason="typed")
    await message.answer(t.MSG_USE_SHARE_BUTTON, reply_markup=share_phone_kb(t))
