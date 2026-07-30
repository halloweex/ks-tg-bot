"""Broadcast handlers — opt-out commands and durable admin broadcast flow."""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.i18n import Texts, admin_texts
from bot.callbacks import BroadcastAction
from bot.analytics import track
from bot.config import AppConfig
from bot.db import (
    broadcast_job_stats,
    event_counts,
    funnel_counts,
    lookup_miss_rate,
    returning_users,
    create_broadcast_job,
    finish_broadcast_job,
    get_broadcast_recipients,
    get_pending_targets,
    get_unfinished_broadcasts,
    get_user_language,
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
async def cmd_stop(message: Message, t: Texts) -> None:
    """Opt the user out of broadcast messages."""
    await opt_out_user(message.chat.id)
    track(message.chat.id, "opted_out")
    await message.answer(t.MSG_OPT_OUT_CONFIRM)


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
    bot: Bot,
    job_id: int,
    text: str,
    notify_chat_id: int | None,
    t: Texts | None = None,
) -> None:
    """Drive a job to completion over its still-pending targets, then report.

    `t` is the admin's language when a person started the job. Resuming after a
    restart has no user context, so the summary falls back to the default.

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
                (t or admin_texts(None)).MSG_BROADCAST_COMPLETE.format(
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


async def _start_broadcast(bot: Bot, text: str, admin_id: int, t: Texts) -> None:
    """Persist a new job (snapshotting recipients) and run it in the background."""
    job_id = await create_broadcast_job(text, admin_id)
    logger.info("Broadcast job #{} created by admin {}", job_id, admin_id)
    spawn(run_broadcast_job(bot, job_id, text, admin_id, t), name=f"broadcast_job_{job_id}")


@router.message(Command("broadcast"))
async def cmd_broadcast(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """Start the broadcast flow (admin only)."""
    if not _is_admin(message.from_user.id, config):
        return
    at = admin_texts(await get_user_language(message.from_user.id))
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer(at.MSG_BROADCAST_PROMPT)


@router.message(BroadcastStates.waiting_message, F.text)
async def process_broadcast_message(
    message: Message, config: AppConfig, state: FSMContext,
    t: Texts,
) -> None:
    """Receive the broadcast text and ask for confirmation."""
    if not _is_admin(message.from_user.id, config):
        return

    at = admin_texts(await get_user_language(message.from_user.id))
    recipients = await get_broadcast_recipients()
    if not recipients:
        await message.answer(at.MSG_BROADCAST_NO_RECIPIENTS)
        await state.clear()
        return

    await state.update_data(broadcast_text=message.text)
    await state.set_state(BroadcastStates.waiting_confirm)
    await message.answer(
        at.MSG_BROADCAST_CONFIRM.format(count=len(recipients)),
        reply_markup=broadcast_confirm_kb(at),
    )


@router.callback_query(BroadcastStates.waiting_confirm, BroadcastAction.filter())
async def process_broadcast_confirm(
    callback: CallbackQuery,
    callback_data: BroadcastAction,
    config: AppConfig,
    state: FSMContext,
    bot: Bot,
    t: Texts,
) -> None:
    """Execute or cancel the broadcast from the inline Yes/No buttons."""
    if not _is_admin(callback.from_user.id, config):
        await callback.answer()
        return
    await callback.answer()
    at = admin_texts(await get_user_language(callback.from_user.id))

    if callback_data.action != "send":
        await state.clear()
        await callback.message.edit_text(at.MSG_BROADCAST_CANCELLED)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    await state.clear()
    if not broadcast_text:
        await callback.message.edit_text(at.MSG_BROADCAST_CANCELLED)
        return

    await callback.message.edit_text(at.MSG_BROADCAST_STARTED)
    await _start_broadcast(bot, broadcast_text, callback.from_user.id, at)


@router.message(BroadcastStates.waiting_confirm, F.text)
async def process_broadcast_confirm_text(
    message: Message, config: AppConfig, state: FSMContext, bot: Bot,
    t: Texts,
) -> None:
    """Fallback: typing так/yes/да still confirms; anything else cancels."""
    if not _is_admin(message.from_user.id, config):
        return

    at = admin_texts(await get_user_language(message.from_user.id))
    if message.text.lower().strip() not in ("так", "yes", "да"):
        await message.answer(at.MSG_BROADCAST_CANCELLED)
        await state.clear()
        return

    data = await state.get_data()
    broadcast_text = data["broadcast_text"]
    await state.clear()

    await message.answer(at.MSG_BROADCAST_STARTED)
    await _start_broadcast(bot, broadcast_text, message.from_user.id, at)


# --------------- Admin analytics ---------------


@router.message(Command("stats"))
async def cmd_stats(message: Message, config: AppConfig) -> None:
    """Summarise the instrumentation so the numbers are readable from the bot.

    Admin only, and in English: this is an operational readout, not customer
    copy, and it sits alongside logs and the runbook, which are English too.
    """
    if not _is_admin(message.from_user.id, config):
        return

    funnel = await funnel_counts(30)
    misses, lookups = await lookup_miss_rate(30)
    returning, active = await returning_users(30)
    counts = await event_counts(7)

    lines = ["\U0001f4ca <b>Last 30 days</b>", "", "<b>Funnel (unique users)</b>"]
    labels = {
        "start": "/start",
        "contact_shared": "shared contact",
        "registered": "registered",
        "orders_viewed": "viewed orders",
    }
    top = funnel.get("start", 0)
    for key, label in labels.items():
        n = funnel.get(key, 0)
        share = f"  {100 * n / top:.0f}%" if top else ""
        lines.append(f"  {label}: {n}{share}")

    lines += ["", "<b>Order lookups</b>"]
    if lookups:
        lines.append(
            f"  found nothing: {misses} of {lookups} "
            f"({100 * misses / lookups:.0f}%)"
        )
        lines.append("  ^ Telegram phone did not match the one in the CRM")
    else:
        lines.append("  no lookups yet")

    lines += ["", "<b>Retention</b>",
              f"  active: {active}, of them on more than one day: {returning}"]

    lines += ["", "<b>Events, last 7 days</b>"]
    if counts:
        for event, total, users in counts:
            lines.append(f"  {event}: {total} ({users} users)")
    else:
        lines.append("  no events yet")

    lines += ["", "Taps on the Website button are not reported by Telegram — "
              "look for utm_source=telegram in the shop's analytics."]

    await message.answer("\n".join(lines), parse_mode="HTML")
