"""Broadcast handlers — opt-out commands and admin broadcast flow."""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import texts
from bot.callbacks import BroadcastAction
from bot.config import AppConfig
from bot.db import get_broadcast_recipients, opt_out_user
from bot.keyboards import broadcast_confirm_kb
from bot.states import BroadcastStates

router = Router()


# --------------- Opt-out commands (/stop, /unsubscribe) ---------------


@router.message(Command("stop"))
@router.message(Command("unsubscribe"))
async def cmd_stop(message: Message) -> None:
    """Opt the user out of broadcast messages."""
    await opt_out_user(message.chat.id)
    await message.answer(texts.MSG_OPT_OUT_CONFIRM)


# --------------- Admin broadcast flow ---------------


def _is_admin(user_id: int, config: AppConfig) -> bool:
    return user_id in config.env.admin_ids


async def _run_broadcast(bot: Bot, text: str) -> tuple[int, int, int]:
    """Send `text` to every opted-in recipient. Returns (sent, failed, blocked)."""
    recipients = await get_broadcast_recipients()
    sent = failed = blocked = 0
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, text)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(chat_id, text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # ~20 msg/sec Telegram rate limit
    return sent, failed, blocked


@router.message(Command("broadcast"))
async def cmd_broadcast(
    message: Message, config: AppConfig, state: FSMContext
) -> None:
    """Start the broadcast flow (admin only)."""
    if not _is_admin(message.from_user.id, config):
        return
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer(texts.MSG_BROADCAST_PROMPT)


@router.message(BroadcastStates.waiting_message, F.text)
async def process_broadcast_message(
    message: Message, config: AppConfig, state: FSMContext
) -> None:
    """Receive the broadcast text and ask for confirmation."""
    if not _is_admin(message.from_user.id, config):
        return

    recipients = await get_broadcast_recipients()
    if not recipients:
        await message.answer(texts.MSG_BROADCAST_NO_RECIPIENTS)
        await state.clear()
        return

    await state.update_data(broadcast_text=message.text)
    await state.set_state(BroadcastStates.waiting_confirm)
    await message.answer(
        texts.MSG_BROADCAST_CONFIRM.format(count=len(recipients)),
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(BroadcastStates.waiting_confirm, BroadcastAction.filter())
async def process_broadcast_confirm(
    callback: CallbackQuery,
    callback_data: BroadcastAction,
    config: AppConfig,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Execute or cancel the broadcast from the inline Yes/No buttons."""
    if not _is_admin(callback.from_user.id, config):
        await callback.answer()
        return
    await callback.answer()

    if callback_data.action != "send":
        await state.clear()
        await callback.message.edit_text(texts.MSG_BROADCAST_CANCELLED)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    await state.clear()
    if not broadcast_text:
        await callback.message.edit_text(texts.MSG_BROADCAST_CANCELLED)
        return

    await callback.message.edit_text(texts.MSG_BROADCAST_STARTED)
    sent, failed, blocked = await _run_broadcast(bot, broadcast_text)
    await callback.message.answer(
        texts.MSG_BROADCAST_COMPLETE.format(sent=sent, failed=failed, blocked=blocked)
    )


@router.message(BroadcastStates.waiting_confirm, F.text)
async def process_broadcast_confirm_text(
    message: Message, config: AppConfig, state: FSMContext, bot: Bot
) -> None:
    """Fallback: typing так/yes/да still confirms; anything else cancels."""
    if not _is_admin(message.from_user.id, config):
        return

    if message.text.lower().strip() not in ("так", "yes", "да"):
        await message.answer(texts.MSG_BROADCAST_CANCELLED)
        await state.clear()
        return

    data = await state.get_data()
    broadcast_text = data["broadcast_text"]
    await state.clear()

    await message.answer(texts.MSG_BROADCAST_STARTED)
    sent, failed, blocked = await _run_broadcast(bot, broadcast_text)
    await message.answer(
        texts.MSG_BROADCAST_COMPLETE.format(sent=sent, failed=failed, blocked=blocked)
    )
