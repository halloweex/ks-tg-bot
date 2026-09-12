"""The loop around the gender sweep, and nothing else.

Hourly, like the birthday sweep and for one of its reasons: a deploy restarts the
process, so a loop that fired once a day would skip its turn whenever a restart
fell on it. The other reason is its own — the table this reads has an
`override_by_human` column, which means somebody will correct a name and want to
see the bot stop calling a man «красуне». An hour is how long that takes.

Started only when a DSN is configured (bot/__main__.py). Without one there is no
loop, no form is written, and every customer is addressed the way core/texts.py
is written — which is the state this bot has been in since it was born.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from core.ports.gender import BuyerGenders
from core.ports.repositories import CustomerDirectory
from core.ports.users import GenderForm
from core.usecases.gender import refresh_forms

POLL_INTERVAL_SECONDS = 60 * 60


async def watch(
    source: BuyerGenders,
    directory: CustomerDirectory,
    forms: GenderForm,
) -> None:
    """Poll forever. Never lets one bad round kill the loop.

    A failed round is a warning and not an exception on purpose: the other
    database being unreachable is the expected failure here — it belongs to
    another compose project with its own deploys and its own restarts — and a
    stack trace an hour would train everyone to stop reading the logs. What it
    costs is that nobody's form changes until it comes back.
    """
    logger.info("Gender watcher started ({}s interval)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await refresh_forms(directory, source, forms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gender sweep failed, forms left as they are: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
