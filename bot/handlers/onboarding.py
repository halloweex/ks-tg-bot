"""Onboarding handler — phone input, validation, and dual-API verification."""
from __future__ import annotations

import asyncio
import re

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import texts
from bot.db import save_user
from bot.services.keycrm import KeyCRMClient
from bot.services.shopify import ShopifyClient
from bot.states import OnboardingStates

router = Router()

PHONE_PATTERN = re.compile(r"^\+380\d{9}$")


@router.message(OnboardingStates.waiting_phone)
async def process_phone(
    message: Message,
    state: FSMContext,
    keycrm: KeyCRMClient,
    shopify: ShopifyClient | None,
) -> None:
    """Validate phone number and verify against Shopify/KeyCRM."""
    # Normalize: strip spaces, dashes, parentheses before validation
    raw = message.text or ""
    phone = re.sub(r"[\s\-\(\)]", "", raw.strip())

    # Validate format
    if not PHONE_PATTERN.match(phone):
        await message.answer(texts.ERR_INVALID_PHONE)
        return

    # Acknowledge receipt
    await message.answer(texts.MSG_PHONE_ACCEPTED)

    # Build parallel lookup tasks
    tasks: list[asyncio.Task[list]] = [keycrm.get_orders_by_phone(phone)]
    if shopify is not None:
        tasks.append(shopify.get_orders_by_phone(phone))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Analyze results — distinguish API failure from empty results
    all_failed = True
    has_orders = False

    for r in results:
        if isinstance(r, list):
            all_failed = False
            if len(r) > 0:
                has_orders = True

    if all_failed:
        await message.answer(texts.ERR_API_UNAVAILABLE)
        return  # Stay in waiting_phone for retry

    if not has_orders:
        await message.answer(texts.ERR_PHONE_NOT_FOUND)
        return  # Stay in waiting_phone for retry

    # Success — persist and clear FSM state
    await save_user(message.chat.id, phone)
    await state.clear()
    await message.answer(texts.MSG_PHONE_VERIFIED)
