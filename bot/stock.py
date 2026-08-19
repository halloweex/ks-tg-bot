"""The loop around the back-in-stock sweep, and nothing else.

Everything this file used to do — building the message, sending it, retrying a
rate limit, catching a blocked chat, staying silent at night — went to the
outbox in stage 6, and the sweep itself went to `core.usecases.stock`. What is
left is the schedule, which is the only part that belongs to a process rather
than to a scenario.

There is no aiogram here any more. The same is true of the sweep it calls, which
is why a restock can now be tested end to end without a bot: the poll answers
from a port, the message lands in a table, and the transport is somebody else's
problem two modules away.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from core.ports.catalog import StockLevels
from core.usecases.stock import check_once

# A full sweep costs ~14s and 18 requests. Every 15 minutes is far inside the
# rate limit and well below the resolution anyone cares about for a restock.
POLL_INTERVAL_SECONDS = 15 * 60


async def watch(catalogue: StockLevels) -> None:
    """Poll forever. Never lets one bad round kill the loop."""
    logger.info("Back-in-stock watcher started ({}s interval)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await check_once(catalogue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Back-in-stock sweep failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
