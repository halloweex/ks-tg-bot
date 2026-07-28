"""Broadcast handlers — opt-out commands and durable admin broadcast flow."""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot import texts
from bot.callbacks import BroadcastAction
from bot.config import AppConfig
from bot.db import (
    broadcast_job_stats,
    create_broadcast_job,
    finish_broadcast_job,
    get_broadcast_recipients,
    get_pending_targets,
    get_unfinished_broadcasts,
    mark_target,
    opt_out_user,
)
from bot.keyboards import broadcast_confirm_kb
from bot.states import BroadcastStates
from bot.tasks import spawn

router = Router()

# Only one job sends at a time so the ~20 msg/sec Telegram rate limit is global
# across a fresh job and any jobs being resumed after a restart.
_send_lock = asyncio.Lock()


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


async def _send_one(bot: Bot, job_id: int, chat_id: int, text: str) -> None:
    """Send to one recipient and persist the outcome so a restart can resume.

    403 Forbidden (bot blocked / account deactivated) → mark blocked AND opt the
    user out, so future broadcasts skip them and the dead-chat_id set can't grow.
    429 Too Many Requests → honour retry_after, then retry once.
    """
    try:
        await bot.send_message(chat_id, text)
        await mark_target(job_id, chat_id, "sent")
    except TelegramForbiddenError:
        await mark_target(job_id, chat_id, "blocked")
        await opt_out_user(chat_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try:
            await bot.send_message(chat_id, text)
            await mark_target(job_id, chat_id, "sent")
        except TelegramForbiddenError:
            await mark_target(job_id, chat_id, "blocked")
            await opt_out_user(chat_id)
        except Exception as exc:  # noqa: BLE001
            await mark_target(job_id, chat_id, "failed", str(exc))
    except Exception as exc:  # noqa: BLE001
        await mark_target(job_id, chat_id, "failed", str(exc))


async def run_broadcast_job(
    bot: Bot, job_id: int, text: str, notify_chat_id: int | None
) -> None:
    """Drive a job to completion over its still-pending targets, then report.

    Safe to call again after a restart: already-processed recipients are no
    longer 'pending', so only the remainder are sent.
    """
    async with _send_lock:
        pending = await get_pending_targets(job_id)
        logger.info("Broadcast job #{}: sending to {} recipient(s)", job_id, len(pending))
        for chat_id in pending:
            await _send_one(bot, job_id, chat_id, text)
            await asyncio.sleep(0.05)  # ~20 msg/sec

        await finish_broadcast_job(job_id)
        stats = await broadcast_job_stats(job_id)
        logger.info("Broadcast job #{} done: {}", job_id, stats)
        if notify_chat_id:
            await bot.send_message(
                notify_chat_id,
                texts.MSG_BROADCAST_COMPLETE.format(
                    sent=stats["sent"], failed=stats["failed"], blocked=stats["blocked"]
                ),
            )


async def resume_broadcasts(bot: Bot) -> None:
    """On startup, continue any broadcast interrupted by a restart/redeploy."""
    for job in await get_unfinished_broadcasts():
        logger.warning("Resuming interrupted broadcast job #{}", job["id"])
        spawn(
            run_broadcast_job(bot, job["id"], job["text"], job["created_by"]),
            name=f"broadcast_job_{job['id']}",
        )


async def _start_broadcast(bot: Bot, text: str, admin_id: int) -> None:
    """Persist a new job (snapshotting recipients) and run it in the background."""
    job_id = await create_broadcast_job(text, admin_id)
    logger.info("Broadcast job #{} created by admin {}", job_id, admin_id)
    spawn(run_broadcast_job(bot, job_id, text, admin_id), name=f"broadcast_job_{job_id}")


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
    await _start_broadcast(bot, broadcast_text, callback.from_user.id)


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
    await _start_broadcast(bot, broadcast_text, message.from_user.id)
