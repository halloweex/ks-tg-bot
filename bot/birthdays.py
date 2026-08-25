"""The loop around the birthday sweep, and nothing else.

Hourly rather than daily, for two reasons that have nothing to do with
birthdays: a deploy restarts the process, and a loop that fires once a day would
skip its turn whenever the restart happened to fall on it. The dedup key makes
the extra runs free — the second one of the day queues nothing.

Same shape as bot/stock.py, and for the same reason: what belongs to a process
is the schedule, and everything else belongs to the scenario.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from core.ports.profiles import BirthdaySource
from core.usecases.birthdays import check_once

POLL_INTERVAL_SECONDS = 60 * 60


async def watch(profiles: BirthdaySource, card_url: str = "") -> None:
    """Poll forever. Never lets one bad round kill the loop."""
    logger.info("Birthday watcher started ({}s interval)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await check_once(profiles, card_url=card_url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Birthday sweep failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
