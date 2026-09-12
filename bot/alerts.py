"""Telling the people who run the bot that something needs them.

One place, because there are several callers now: the sync watchdog (§5.5), the
outbox shelf (§6.3), the support relay, and the startup check at the bottom of
this file. All want the same three properties — reach every admin, never let
one unreachable admin cost the others their alert, and never let a failed alert
take down the loop that raised it.

English, like everything else an admin reads: it sits next to the logs, the
runbook and the backup alerts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger

# When the same thing keeps breaking, the alert about it is worth sending once
# in a while rather than once per customer: an alert repeating faster than
# anybody can act on it is how alerts get muted. Same reasoning, and the same
# interval, as the outbox shelf.
_REPEAT_AFTER = timedelta(minutes=10)
_last_told: dict[str, datetime] = {}


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


async def tell_admins_once(bot: Bot, admin_ids: list[int], key: str,
                           text: str) -> int:
    """Alert about `key`, unless the same key was alerted about just now.

    In memory on purpose: a restart is exactly when an alert should be allowed
    through again, because a restart is when the thing may have been fixed.
    """
    now = datetime.now(timezone.utc)
    last = _last_told.get(key)
    if last is not None and now - last < _REPEAT_AFTER:
        logger.debug("Alert {} suppressed, last sent {}", key, last)
        return 0
    _last_told[key] = now
    return await tell_admins(bot, admin_ids, text)


async def resolve_support_link(bot: Bot, support_chat_id: int) -> str:
    """The public link to the support chat, derived from its id.

    A customer who taps «💬 Менеджер» is sent here to write directly, because
    that is where the answer comes from: the manager reads the relay and then
    opens her own chat with the person. The bot used to promise «відповімо тут»
    and the reply arrived somewhere else entirely, which from the customer's
    side is indistinguishable from being ignored.

    Derived rather than configured so `support_chat_id` stays the single thing
    to change. An account with no public username has no t.me link, and this
    returns "" — the relay then stays as it was rather than the button pointing
    nowhere.

    Never raises: a link is a convenience and the bot must start without one.
    """
    try:
        chat = await bot.get_chat(support_chat_id)
    except TelegramAPIError as exc:
        logger.warning("Could not resolve a link for support chat {}: {}",
                       support_chat_id, exc)
        return ""
    username = getattr(chat, "username", "")
    if not username:
        logger.warning("Support chat {} has no public username, so the manager "
                       "button keeps relaying through the bot instead of "
                       "linking to it", support_chat_id)
        return ""
    logger.info("Support chat {} resolves to t.me/{}", support_chat_id, username)
    return f"https://t.me/{username}"


async def check_support_chat(bot: Bot, support_chat_id: int,
                             admin_ids: list[int]) -> bool:
    """At startup, find out whether the support chat can be written to at all.

    Not with getChat: that answered happily for an account this bot was
    forbidden from messaging, which is exactly how a broken relay reached
    production unnoticed. sendChatAction is the probe instead — it creates no
    message, shows a "typing" flicker at worst, and fails the way a real send
    would ("bot can't initiate conversation with a user", "PEER_ID_INVALID").

    A bot cannot open a conversation with a person. If support_chat_id names a
    user account, that account has to press Start once; a group the bot is in
    has no such rule, which is why a group is the sturdier configuration.

    Never raises: a bot that refuses to start because an alert failed is worse
    than one that starts with a broken relay and says so.
    """
    try:
        await bot.send_chat_action(support_chat_id, "typing")
        logger.info("Support chat {} is reachable", support_chat_id)
        return True
    except TelegramAPIError as exc:
        logger.error("Support chat {} cannot be written to: {}", support_chat_id, exc)
        await tell_admins_once(
            bot, admin_ids, "support_relay",
            f"⚠️ The support chat cannot be written to: {exc}\n\n"
            f"support_chat_id={support_chat_id}. Nothing a customer sends to "
            f"support, and no discount request, will reach anybody until this "
            f"is fixed: that account must open the bot and press Start, or the "
            f"id must name a group the bot is a member of.",
        )
        return False
    except Exception as exc:  # noqa: BLE001 — a check must not stop the bot
        logger.warning("Support chat check failed unexpectedly: {}", exc)
        return False
