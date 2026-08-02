"""When a message may buzz someone's phone, and when it must arrive quietly.

Nothing the bot sends on its own initiative — a restock, a broadcast — is worth
waking a customer for. Telegram can deliver a message without a sound, so at
night it does; the message is waiting in the morning, unread and unresented.

Times are the shop's, not the server's: the VPS runs on UTC and the customers
are in Ukraine.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from loguru import logger

QUIET_FROM = time(22, 0)
QUIET_UNTIL = time(9, 0)


def _shop_tz():
    """Europe/Kyiv, or a fixed +03:00 if the image has no timezone database.

    The fallback is off by an hour in winter, which moves the edge of quiet
    hours — a far smaller problem than the watcher dying on an import.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Kyiv")
    except Exception as exc:  # noqa: BLE001
        logger.warning("No tz database ({}), assuming UTC+03:00 for quiet hours", exc)
        return timezone(timedelta(hours=3))


SHOP_TZ = _shop_tz()


def is_quiet_now(now: datetime | None = None) -> bool:
    """True when it is night in Ukraine and notifications should be silent."""
    local = (now or datetime.now(timezone.utc)).astimezone(SHOP_TZ).time()
    return local >= QUIET_FROM or local < QUIET_UNTIL
