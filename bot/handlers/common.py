"""Common command handlers — /start and other global commands."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.config import AppConfig
from bot import texts

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, config: AppConfig) -> None:
    """Handle /start command — send branded greeting."""
    greeting = texts.GREETING.format(brand_name=config.brand_name)
    await message.answer(greeting)
