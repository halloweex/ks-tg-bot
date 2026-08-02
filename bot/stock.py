"""Back-in-stock watcher: poll KeyCRM, notify whoever was waiting.

Reads `offers/stocks`, which is live, rather than the analytics service's parquet
files — those are a by-product of a weekly DuckDB compaction, are deleted and
rebuilt every Sunday, and would make a "it's back" notification up to a week late.

A restock is a transition: a sku that had nothing free to sell now has some. Only
transitions notify, so a product that simply stays in stock never fires, and the
very first poll only records the baseline — otherwise every available sku would
look like it had just come back.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from loguru import logger

from bot.analytics import track
from core.repos.stock import (clear_subscriptions, get_stock_levels, save_stock_levels,
                              subscribers_for)
from core.repos.users import get_user_language
from core.i18n import customer_texts
from core.domain.quiet import is_quiet_now
from core.adapters.keycrm.client import KeyCRMClient
from core.texts import shorten_name

# A full sweep costs ~14s and 18 requests. Every 15 minutes is far inside the
# rate limit and well below the resolution anyone cares about for a restock.
POLL_INTERVAL_SECONDS = 15 * 60


def restocked(previous: dict[str, int], current: dict[str, int]) -> list[str]:
    """Skus that went from nothing available to something.

    A sku absent from `previous` is new to us and never counts: on the first
    poll that would mean the entire in-stock catalogue.
    """
    return [
        sku for sku, available in current.items()
        if available > 0 and previous.get(sku, 1) <= 0 and sku in previous
    ]


# Telegram's 🎉 message effect. The one moment in this bot that is unambiguously
# good news for the person reading it — their product is back — and the only
# place an effect is not noise. Sent best-effort: an id Telegram stops
# recognising must not cost anybody their notification (see _send).
CONFETTI_EFFECT_ID = "5046509860389126442"


async def _send(bot: Bot, chat_id: int, text: str) -> None:
    """Send the news: confetti, and silent if it is the middle of the night."""
    silent = is_quiet_now()
    try:
        await bot.send_message(
            chat_id, text,
            message_effect_id=CONFETTI_EFFECT_ID,
            disable_notification=silent,
        )
    except TelegramBadRequest as exc:
        logger.debug("Message effect rejected ({}), sending plain", exc.message)
        await bot.send_message(chat_id, text, disable_notification=silent)


async def _notify(bot: Bot, chat_id: int, names: list[str]) -> bool:
    """Tell one person their products are back. False if the chat is gone."""
    t = customer_texts(await get_user_language(chat_id))
    lines = [t.MSG_BACK_IN_STOCK_HEADER, ""]
    lines += [f"• {shorten_name(name, 60)}" for name in names]
    try:
        await _send(bot, chat_id, "\n".join(lines))
        return True
    except TelegramForbiddenError:
        logger.info("Back-in-stock: chat {} blocked the bot", chat_id)
        return False
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await _send(bot, chat_id, "\n".join(lines))
            return True
        except Exception as retry_exc:  # noqa: BLE001
            logger.warning("Back-in-stock retry failed for {}: {}", chat_id, retry_exc)
            return False
    except Exception as exc:  # noqa: BLE001 — one bad chat must not stop the sweep
        logger.warning("Back-in-stock send failed for {}: {}", chat_id, exc)
        return False


async def check_once(bot: Bot, keycrm: KeyCRMClient) -> int:
    """One sweep. Returns how many people were notified."""
    current = await keycrm.get_stock()
    if not current:
        # get_stock() returns {} rather than a partial read, so there is nothing
        # to compare against and nothing to record.
        logger.warning("Back-in-stock: empty stock snapshot, skipping this round")
        return 0

    previous = await get_stock_levels()
    back = restocked(previous, current)
    await save_stock_levels(current)

    if not previous:
        logger.info("Back-in-stock: baseline recorded for {} skus", len(current))
        return 0
    if not back:
        return 0

    waiting = await subscribers_for(back)
    if not waiting:
        logger.info("Back-in-stock: {} sku(s) returned, nobody waiting", len(back))
        return 0

    # One message per person, not per product: several favourites can return in
    # the same sweep and that should not read as a burst of notifications.
    by_chat: dict[int, list[tuple[str, str]]] = {}
    for chat_id, sku, name in waiting:
        by_chat.setdefault(chat_id, []).append((sku, name))

    fulfilled: list[tuple[int, str]] = []
    notified = 0
    for chat_id, items in by_chat.items():
        ok = await _notify(bot, chat_id, [name for _sku, name in items])
        # Clear either way: a blocked chat will never receive it, and keeping the
        # row would retry forever on every future restock of the same sku.
        fulfilled += [(chat_id, sku) for sku, _name in items]
        if ok:
            notified += 1
            track(chat_id, "back_in_stock_notified", products=len(items))
        await asyncio.sleep(0.05)  # stay under Telegram's ~20 msg/sec

    await clear_subscriptions(fulfilled)
    logger.info(
        "Back-in-stock: {} sku(s) returned, notified {} of {} waiting chats",
        len(back), notified, len(by_chat),
    )
    return notified


async def watch(bot: Bot, keycrm: KeyCRMClient) -> None:
    """Poll forever. Never lets one bad round kill the loop."""
    logger.info("Back-in-stock watcher started ({}s interval)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await check_once(bot, keycrm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Back-in-stock sweep failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
