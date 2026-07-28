"""Onboarding handler — phone input, validation, and registration."""

import asyncio
import re
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from loguru import logger

from bot import texts
from bot.config import AppConfig
from bot.db import save_user, upsert_orders
from bot.keyboards import main_menu_kb
from bot.services.keycrm import KeyCRMClient, keycrm_order_to_dict
from bot.services.shopify import ShopifyClient, shopify_order_to_dict
from bot.states import OnboardingStates

router = Router()

# E.164: + followed by 7-15 digits (covers all international numbers)
PHONE_PATTERN = re.compile(r"^\+\d{7,15}$")


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
    message: Message, state: FSMContext, phone: str, config: AppConfig,
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
    await message.answer(texts.MSG_PHONE_VERIFIED, reply_markup=ReplyKeyboardRemove())
    await message.answer(texts.MSG_MAIN_MENU, reply_markup=main_menu_kb(config.website_url))


@router.message(OnboardingStates.waiting_phone, F.contact)
async def process_contact(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    keycrm: KeyCRMClient,
    shopify: Optional[ShopifyClient],
) -> None:
    """Handle shared Telegram contact."""
    contact = message.contact
    if not contact or not contact.phone_number:
        await message.answer(texts.ERR_INVALID_PHONE)
        return

    # Normalize: strip everything except digits, then add +
    raw_phone = contact.phone_number
    logger.info("Contact phone raw: '{}'", raw_phone)
    digits = re.sub(r"\D", "", raw_phone)
    logger.info("Contact phone digits: '{}'", digits)

    # Handle various formats: 380XXXXXXXXX, +380XXXXXXXXX, 0XXXXXXXXX
    if digits.startswith("380") and len(digits) == 12:
        phone = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        phone = "+38" + digits
    else:
        phone = "+" + digits

    logger.info("Contact phone normalized: '{}'", phone)

    if not PHONE_PATTERN.match(phone):
        await message.answer(texts.ERR_INVALID_PHONE)
        return

    await message.answer(texts.MSG_PHONE_ACCEPTED)
    await _register_user(message, state, phone, config, keycrm=keycrm, shopify=shopify)


@router.message(OnboardingStates.waiting_phone)
async def process_phone(
    message: Message,
    state: FSMContext,
    config: AppConfig,
    keycrm: KeyCRMClient,
    shopify: Optional[ShopifyClient],
) -> None:
    """Validate phone number typed manually."""
    raw = message.text or ""
    phone = re.sub(r"[\s\-\(\)]", "", raw.strip())

    if not PHONE_PATTERN.match(phone):
        await message.answer(texts.ERR_INVALID_PHONE)
        return

    await message.answer(texts.MSG_PHONE_ACCEPTED)
    await _register_user(message, state, phone, config, keycrm=keycrm, shopify=shopify)
