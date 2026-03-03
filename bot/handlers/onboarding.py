"""Onboarding handler — phone input, validation, and registration."""

import re
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from loguru import logger

from bot import texts
from bot.db import save_user
from bot.services.keycrm import KeyCRMClient
from bot.services.shopify import ShopifyClient
from bot.states import OnboardingStates

router = Router()

PHONE_PATTERN = re.compile(r"^\+380\d{9}$")


async def _register_user(message: Message, state: FSMContext, phone: str) -> None:
    """Save user and complete onboarding."""
    await save_user(message.chat.id, phone)
    await state.clear()
    await message.answer(texts.MSG_PHONE_VERIFIED, reply_markup=ReplyKeyboardRemove())


@router.message(OnboardingStates.waiting_phone, F.contact)
async def process_contact(
    message: Message,
    state: FSMContext,
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
    await _register_user(message, state, phone)


@router.message(OnboardingStates.waiting_phone)
async def process_phone(
    message: Message,
    state: FSMContext,
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
    await _register_user(message, state, phone)
