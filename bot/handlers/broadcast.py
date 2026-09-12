"""Broadcast handlers — opt-out commands and the admin flow that queues a send.

The sending itself left with stage 6: the flow below records a job, queues one
message per recipient and stops. Everything that used to be here — the driver,
the per-recipient retry, the blocked-chat handling, the pacing, the lock that
kept two jobs from overlapping and the resume-after-restart — is the outbox now,
and none of it was ever specific to broadcasts.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from core.i18n import Texts, admin_texts
from bot.callbacks import BroadcastAction
from bot.analytics import track
from core.config import AppConfig
from core.repos.campaigns import SqliteCampaignOutcomes
from core.usecases.analytics import campaign_report, usage_report
from core.usecases.broadcast import start_broadcast
from core.repos.broadcast import SqliteBroadcastJournal
from core.repos.events import SqliteUsageStats
from core.repos.outbox import SqliteMessageQueue
from core.repos.users import (SqliteMailingList, get_broadcast_recipients,
                              get_user_language, opt_out_user)
from bot.keyboards import broadcast_confirm_kb
from bot.states import BroadcastStates

router = Router()


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


async def _queue_broadcast(text: str, admin_id: int) -> None:
    """Hand the job to the queue and let the sender get on with it.

    Nothing is spawned any more. The messages are rows before this returns, and
    the outbox sender empties them at its own pace — which is what makes a
    redeploy in the middle of a broadcast a non-event instead of the reason
    resume_broadcasts existed.

    The three handles are built here for the same reason cmd_stats builds its
    store: there is no composition root yet, so the handler is where it is known
    which engine is underneath. When one exists, this line takes them as
    arguments like everything else.
    """
    started = await start_broadcast(
        text, admin_id,
        SqliteBroadcastJournal(), SqliteMailingList(), SqliteMessageQueue(),
    )
    logger.info("Broadcast job #{} queued for {} recipient(s)",
                started.job_id, started.queued)


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

    # **The one place an admin command can reach a customer.**
    #
    # This handler takes `message.text` verbatim, and `F.text` matches a command
    # like any other line. Registration order then decides the outcome and is
    # not on our side: `/stop` and `/broadcast` sit above this handler and fire,
    # while `/stats` and `/chatid` sit below it and become the broadcast — so
    # "/stats" goes out to every subscriber, from the shop, signed by the shop.
    #
    # Checked here rather than by reordering the router, because reordering only
    # covers the commands that have handlers. A typo — "/statss", "/menü" — has
    # none, and would still be sent. Everything starting with a slash is
    # refused, and the state is kept so the real text can simply be typed next.
    if message.text.startswith("/"):
        await message.answer(at.MSG_BROADCAST_LOOKS_LIKE_A_COMMAND)
        return

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
    t: Texts,
) -> None:
    """Queue or cancel the broadcast from the inline Yes/No buttons."""
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
    await _queue_broadcast(broadcast_text, callback.from_user.id)


@router.message(BroadcastStates.waiting_confirm, F.text)
async def process_broadcast_confirm_text(
    message: Message, config: AppConfig, state: FSMContext,
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
    await _queue_broadcast(broadcast_text, message.from_user.id)


# --------------- Admin utilities ---------------


@router.message(Command("chatid"))
async def cmd_chatid(message: Message, config: AppConfig) -> None:
    """Report this chat's id, for filling in support_chat_id.

    Works in a group as well as a DM. In a group the bot only receives commands
    addressed to it while privacy mode is on, so use /chatid@<botname> there.
    """
    if message.from_user.id not in config.env.admin_ids:
        return

    chat = message.chat
    current = config.support_chat_id
    lines = [
        f"Chat id: <code>{chat.id}</code>",
        f"Type: {chat.type}" + (f" · {chat.title}" if chat.title else ""),
        "",
        f"support_chat_id in config.yaml is currently {current}"
        + (" — this chat." if chat.id == current else "."),
    ]
    if chat.id != current and chat.type != "private":
        lines += ["", "To route support here, set support_chat_id to the id above."]
    await message.answer("\n".join(lines), parse_mode="HTML")


# --------------- Admin analytics ---------------


@router.message(Command("stats"))
async def cmd_stats(message: Message, config: AppConfig) -> None:
    """Summarise the instrumentation so the numbers are readable from the bot.

    Admin only, and in English: this is an operational readout, not customer
    copy, and it sits alongside logs and the runbook, which are English too.
    """
    if not _is_admin(message.from_user.id, config):
        return

    # Constructed here because there is no composition root yet: handlers still
    # import repositories (the handlers-see-ports-only contract is the one still
    # commented out in .importlinter). When one exists, this moves into it and
    # this line takes the store as an argument like everything else.
    report = await usage_report(SqliteUsageStats())

    lines = [f"\U0001f4ca <b>Last {report.days} days</b>", "",
             "<b>Funnel (unique users)</b>"]
    labels = {
        "start": "/start",
        "contact_shared": "shared contact",
        "registered": "registered",
        "orders_viewed": "viewed orders",
    }
    for step in report.funnel:
        share = f"  {step.share:.0f}%" if step.share is not None else ""
        lines.append(f"  {labels.get(step.key, step.key)}: {step.users}{share}")

    lines += ["", "<b>Order lookups</b>"]
    if report.miss_rate is not None:
        lines.append(
            f"  found nothing: {report.lookups_without_orders} of {report.lookups} "
            f"({report.miss_rate:.0f}%)"
        )
        lines.append("  ^ Telegram phone did not match the one in the CRM")
    else:
        lines.append("  no lookups yet")

    lines += ["", "<b>Retention</b>",
              f"  active: {report.active_users}, of them on more than one day: "
              f"{report.returning_users}"]

    lines += ["", f"<b>Events, last {report.event_days} days</b>"]
    if report.events:
        for event, total, users in report.events:
            lines.append(f"  {event}: {total} ({users} users)")
    else:
        lines.append("  no events yet")

    campaigns = await campaign_report(SqliteCampaignOutcomes())
    lines += ["", f"<b>Proactive, last {campaigns.days} days</b>"]
    if campaigns.rows:
        lines.append(f"  <i>ordered within {campaigns.window_days}d of the "
                     f"message, against the same window before it</i>")
        for row in campaigns.rows:
            after = (f"{row.bought}/{row.people}"
                     + (f" ({row.share:.0f}%)" if row.share is not None else ""))
            if row.bought_before is None:
                before = "—"
            else:
                before = str(row.bought_before) + (
                    f" ({row.share_before:.0f}%)"
                    if row.share_before is not None else "")
            failed = f", {row.failed} undelivered" if row.failed else ""
            lines.append(f"  {row.key} ({row.type}): {after} after, "
                         f"{before} before{failed}")
        if campaigns.unreadable:
            lines.append(f"  {campaigns.unreadable} row(s) carried something "
                         f"that is not a campaign key — see the log")
    else:
        lines.append("  nothing sent yet")

    lines += ["", "Taps on the Website button are not reported by Telegram — "
              "look for utm_source=telegram in the shop's analytics.",
              "The same is true of every button above: no proactive message "
              "carries one this bot can see a tap on, so there is no click "
              "column and a \"—\" means unmeasurable, never zero.",
              "\"before\" is the ordinary rate for the same people, not a "
              "control group: they were chosen by the campaign, not at random."]

    await message.answer("\n".join(lines), parse_mode="HTML")
