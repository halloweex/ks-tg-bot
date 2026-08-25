"""The loop around the referral sweep, and telling a manager about it.

The sweep itself knows nothing about Telegram — it queues a message for the
customer and returns. What lives here is the schedule and the one thing that
needs a bot: a note in the support chat. Who writes the promo code depends on
the configuration — a Shopify token with rights to discounts and a percentage,
and the sweep issues one per reward; without them, a person does.

Every quarter of an hour rather than daily: the friend's order arrives in the
cache within two minutes of being placed, and a programme that pays out the
next morning feels like a form rather than a thank-you.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger

from bot.alerts import tell_admins_once
from bot.customer import describe
from bot.keyboards import discount_url
from core.config import AppConfig
from core.ports.discounts import DiscountCodes
from core.i18n import operator_texts
from core.ports.outbox import MessageQueue
from core.ports.repositories import ReferralLedger
from core.ports.users import LanguageChoice
from core.usecases.referrals import check_once

POLL_INTERVAL_SECONDS = 15 * 60


async def watch(
    bot: Bot,
    prefix: str,
    config: AppConfig,
    admin_ids: list[int],
    ledger: ReferralLedger,
    languages: LanguageChoice,
    queue: MessageQueue,
    discounts: DiscountCodes | None = None,
) -> None:
    """Poll forever. Never lets one bad round kill the loop.

    With `discounts` and a percentage configured, each reward gets its own
    single-use code and nobody writes one by hand. The note to the support chat
    stays either way: a round that paid somebody is worth seeing.
    """
    logger.info("Referral watcher started ({}s interval, own codes: {})",
                POLL_INTERVAL_SECONDS,
                bool(discounts and config.referral_percent > 0))
    link = (discount_url(config.website_url, config.referral_code)
            if config.referral_code else "")
    while True:
        try:
            swept = await check_once(prefix, ledger, languages, queue,
                                     code=config.referral_code,
                                     reward=config.referral_reward,
                                     discount_link=link,
                                     discounts=discounts,
                                     percent=config.referral_percent,
                                     link_for=lambda issued: discount_url(
                                         config.website_url, issued))
            # Somebody still writes the codes while there is no code to hand
            # out automatically; with one, this is the line that says how many
            # went out.
            if swept.earned:
                await _tell_support(bot, swept.earned, config.support_chat_id,
                                    admin_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Referral sweep failed: {}", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _tell_support(bot: Bot, earned: int, support_chat_id: int,
                        admin_ids: list[int]) -> None:
    """One line per round, so somebody writes the promo codes.

    Deliberately a count and not a list of names: the customers have already
    been told, and what the manager needs is the prompt plus a place to look.
    A failure here is loud, for the same reason every other support-chat send
    is — a note nobody receives is how a promise quietly stops being kept.
    """
    op = operator_texts()
    try:
        await bot.send_message(
            support_chat_id,
            op.MSG_REFERRAL_ADMIN_SUMMARY.format(count=earned),
            parse_mode="HTML",
        )
    except TelegramAPIError as exc:
        logger.error("Referral note to chat {} failed: {}", support_chat_id, exc)
        await tell_admins_once(
            bot, admin_ids, "support_relay",
            f"Referral notes are not reaching support: {exc}",
        )
