"""Onboarding handler — phone input, validation, and registration."""

import re
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

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

    # Normalize: ensure +380 format
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    phone = re.sub(r"[\s\-\(\)]", "", phone)

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
