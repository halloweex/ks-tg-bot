"""Telling the people who run the bot that something needs them.

One place, because there are now two callers and there will be more: the sync
watchdog (§5.5) and the outbox shelf (§6.3). Both want the same three
properties — reach every admin, never let one unreachable admin cost the others
their alert, and never let a failed alert take down the loop that raised it.

English, like everything else an admin reads: it sits next to the logs, the
runbook and the backup alerts.
"""
from __future__ import annotations

from aiogram import Bot
from loguru import logger


async def tell_admins(bot: Bot, admin_ids: list[int], text: str) -> int:
    """Send `text` to every admin. Returns how many received it.

    Failures are logged and swallowed: an alert that raises would take out the
    loop that noticed the problem, which turns one broken thing into two.
    """
    delivered = 0
    for chat_id in admin_ids:
        try:
            await bot.send_message(chat_id, text)
            delivered += 1
        except Exception as exc:  # noqa: BLE001 — one admin must not cost the others
            logger.warning("Alert not delivered to {}: {}", chat_id, exc)
    return delivered
