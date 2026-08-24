"""The schedule around the catalogue sweep, and nothing else.

Same shape as bot/stock.py, and for the same reason: a loop belongs to the
process, the sweep belongs to the scenario, and keeping them apart is what lets
the sweep be tested without waiting an hour.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from core.ports.catalog import Storefront
from core.usecases.sync_catalogue import refresh_once

# Three requests an hour against a public feed. Prices and publication state
# move on the scale of a working day, not of minutes — the restock watcher is
# the one that has to be quick, and it polls the CRM every fifteen.
POLL_INTERVAL_SECONDS = 60 * 60


async def watch(storefront: Storefront) -> None:
    """Poll forever. Never lets one bad round kill the loop."""
    logger.info("Catalogue watcher started ({}s interval)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await refresh_once(storefront)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Catalogue sweep failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
