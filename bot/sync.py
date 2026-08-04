"""The two-minute sweep, and something separate that watches whether it happens.

The loop is the same shape as bot/stock.py: one round in a try, a bad round
never kills it, and the scenario itself lives in core.usecases where it can be
tested without a bot. It runs in this process rather than in a `worker`
container — docs/architecture.md §4.1 puts it in its own — because deferring
stage 3 left "there is exactly one process" holding the things Postgres was
going to hold (§4.2, and the price table in docs/postgres-migration.md). A
second container writing to the same SQLite file is precisely the assumption
that deferral rests on. Moving it out later is a `worker/__main__.py` and a line
in docker-compose, which is why the loop is this thin.

**Why the watchdog is a separate task.** §5.5: the alert has to fire on the
absence of success, not on the presence of an error, because the failures that
matter raise nothing at all — a cancelled task, a loop that stopped being
scheduled, a sweep that hangs on a socket. None of those reach an `except`, and
a watchdog inside the same loop shares their fate. Separate, it survives the
sweep dying and reports it.

What it still cannot see is this process not existing, and nothing inside the
process can. That gap closes with the daily digest in §10 or an external check,
and is written down here rather than left to be discovered during an outage.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from loguru import logger

from core.ports.crm import ChangedOrderFeed
from core.repos.sync_state import get_state
from core.usecases.sync_incremental import (SOURCE, read_stamp,
                                            sync_changed_orders)

# §5.1. Two minutes against a measured 269 orders changed in two days: most
# sweeps read one page and find nothing new, which is the intended shape — the
# cost of being current is one request, not one request per customer.
POLL_INTERVAL_SECONDS = 2 * 60

# How long the data may stand still before the owner is told. Seven sweeps'
# worth: long enough that a redeploy, a slow reconciliation or one failed round
# stays quiet, short enough that "the orders are stale" is still news.
SILENCE_AFTER = timedelta(minutes=15)

# How often the watchdog looks. It reads one row.
WATCHDOG_INTERVAL_SECONDS = 60

# While it stays broken, say so once an hour rather than every minute. An alert
# that repeats faster than anybody can act on it is the reason alerts get muted,
# and a muted alert is worse than none — it looks like coverage.
REALERT_AFTER = timedelta(hours=1)


async def watch(keycrm: ChangedOrderFeed) -> None:
    """Sweep the changed-orders window forever."""
    logger.info("Order sync started ({}s interval)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await sync_changed_orders(keycrm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — already recorded in sync_state
            logger.exception("Order sync sweep failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def silence(state: dict | None, *, now: datetime, since: datetime) -> timedelta:
    """How long the data has been standing still.

    Measured from the last success, or from `since` — when the watcher started —
    if there has never been one. Without that fallback a bot whose sweep never
    ran once would show a silence of zero forever, which is the failure this is
    for rather than an edge case of it.
    """
    last = read_stamp((state or {}).get("last_success_at"))
    return now - (last or since)


def _alert_text(state: dict | None, quiet_for: timedelta) -> str:
    """English, like everything else an admin reads next to the logs."""
    minutes = int(quiet_for.total_seconds() // 60)
    last = (state or {}).get("last_success_at") or "never"
    lines = [
        f"⚠️ Order sync has not succeeded for {minutes} min.",
        f"Last success: {last} UTC",
    ]
    error = (state or {}).get("last_error")
    if error:
        lines.append(f"Last error: {error}")
    else:
        # No error and no success is the interesting case: nothing failed, so
        # nothing is being attempted. That points at the loop, not at the CRM.
        lines.append("No error recorded — the sweep is not running at all.")
    return "\n".join(lines)


async def _tell(bot: Bot, admin_ids: list[int], text: str) -> None:
    for chat_id in admin_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001 — one admin must not cost the others
            logger.warning("Sync alert not delivered to {}: {}", chat_id, exc)


async def watch_for_silence(bot: Bot, admin_ids: list[int]) -> None:
    """Tell the admins when the orders stop moving, and when they move again."""
    if not admin_ids:
        logger.warning("No admin ids configured — a stalled sync will be silent")
        return

    since = datetime.now(timezone.utc)
    alerted_at: datetime | None = None
    logger.info("Sync watchdog started (alerts after {})", SILENCE_AFTER)

    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            now = datetime.now(timezone.utc)
            state = await get_state(SOURCE)
            quiet_for = silence(state, now=now, since=since)

            if quiet_for >= SILENCE_AFTER:
                if alerted_at is None or now - alerted_at >= REALERT_AFTER:
                    await _tell(bot, admin_ids, _alert_text(state, quiet_for))
                    alerted_at = now
            elif alerted_at is not None:
                await _tell(
                    bot, admin_ids,
                    "✅ Order sync is back. Last success: "
                    f"{(state or {}).get('last_success_at')} UTC",
                )
                alerted_at = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the watchdog outlives its own bugs
            logger.exception("Sync watchdog round failed: {}", exc)
