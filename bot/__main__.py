"""Bot entry point — run with `python -m bot`."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from core.config import load_config
from core.repos.base import configure as configure_db
from core.repos.catalogue import SqliteOfferCache
from core.repos.outbox import SqliteMessageQueue
from core.repos.stock import SqliteRestockWatchlist, SqliteStockSnapshot
from core.repos.users import SqliteKnownBirthdays, SqliteLanguageChoice
from core.repos.schema import init_db
from bot.fsm_storage import SQLiteStorage
from bot.alerts import check_support_chat
from bot.logs import setup_logging
from bot.handlers.broadcast import router as broadcast_router
from bot.handlers.common import router as common_router
from bot.handlers.demo import router as demo_router
from bot.handlers.info import router as info_router
from bot.handlers.inline import router as inline_router
from bot.handlers.menu import router as menu_router
from bot.handlers.onboarding import router as onboarding_router
from bot.handlers.orders import router as orders_router
from bot.handlers.settings import router as settings_router
from bot.handlers.support import router as support_router
from core.adapters.keycrm.client import KeyCRMClient
from core.adapters.shopify.catalog import ShopifyStorefront
from core.adapters.telegram.profile import TelegramProfiles
from core.adapters.novaposhta.client import NovaPoshtaClient
from bot.middlewares import DropCustomEmoji, LanguageMiddleware
from bot import profile
from bot.outbox import watch as watch_outbox
from bot.birthdays import watch as watch_birthdays
from bot.handlers.common import REFERRAL_PREFIX
from bot.referrals import watch as watch_referrals
from bot.catalogue import watch as watch_catalogue
from bot.stock import watch as watch_stock
from bot.sync import watch as watch_orders, watch_for_silence
from bot.tasks import drain, spawn


async def main() -> None:
    """Initialize all components and start polling."""
    # Before anything else: loguru's default handler prints local variables in
    # tracebacks, and load_config() has secrets in its frame.
    setup_logging()

    # Load config (reads .env + config.yaml)
    config = load_config()
    setup_logging(config.env.log_level, phone_salt=config.env.log_phone_salt)
    if not config.env.log_phone_salt:
        logger.warning(
            "LOG_PHONE_SALT is not set — phone digests in the log are "
            "brute-forceable and must not leave this machine"
        )
    configure_db(config.env.bot_db_path)
    logger.info("Config loaded. Brand: {}", config.brand_name)

    # Create Bot instance with HTML parse mode
    bot = Bot(
        token=config.env.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            # The tracking link would otherwise pull a Nova Poshta preview card
            # onto every order message, dwarfing the order itself.
            link_preview_is_disabled=True,
        ),
    )

    # Create Dispatcher. State goes to SQLite, not to memory: a deploy used to
    # drop every conversation in flight, and a customer typing to support then
    # matched no handler at all and got silence.
    dp = Dispatcher(storage=SQLiteStorage())

    # Dependency injection via dp workflow_data
    dp["config"] = config
    dp["keycrm"] = KeyCRMClient(api_key=config.env.keycrm_api_key)
    # The shop's public product feed. No credentials: /products.json is served
    # to anyone, which is the only reason the buy button is possible at all —
    # no Shopify Admin token has ever been configured for this store.
    dp["storefront"] = ShopifyStorefront(config.website_url)

    # Conditional Nova Poshta client
    np_keys = config.env.novaposhta_keys
    if np_keys:
        dp["novaposhta"] = NovaPoshtaClient(np_keys)
        logger.info("Nova Poshta client initialized ({} key(s))", len(np_keys))
    else:
        dp["novaposhta"] = None
        logger.warning("Nova Poshta API key not configured — delivery tracking will use CRM data only")

    # Startup hook: initialize the SQLite database
    loops: list[asyncio.Task] = []

    @dp.startup()
    async def on_startup() -> None:
        await init_db()
        # The bot's own @name, for the deep links a shared card carries. Asked
        # rather than configured: a username in a config file is a username
        # that can quietly stop being true.
        config.bot_username = (await bot.get_me()).username or ""
        # Nothing to resume any more: a broadcast interrupted by a redeploy is
        # rows in the outbox, and the sender picks them up on its next pass.
        # Commands, menu button and the text shown before the first /start.
        await profile.apply(bot, config.env.admin_ids)
        # Can the support chat be written to? Asked here because the answer
        # changes with configuration, not with code, and the way it used to be
        # discovered was a customer saying "nobody answered me".
        await check_support_chat(bot, config.support_chat_id, config.env.admin_ids)
        # Poll KeyCRM for restocks and queue a message for whoever subscribed.
        # No bot argument any more: since stage 6 the sweep queues and the
        # outbox sends, so nothing in that path knows about Telegram. The four
        # storage handles are picked here for the same reason the catalogue
        # cache below is: this is the only place that knows which engine is
        # underneath.
        loops.append(spawn(
            watch_stock(dp["keycrm"], SqliteStockSnapshot(),
                        SqliteRestockWatchlist(), SqliteLanguageChoice(),
                        SqliteMessageQueue()),
            name="stock_watcher"))
        # Keep the storefront's offers fresh, so the favourites screen can
        # offer to buy one and address the cart link to the right variant.
        # The cache is chosen here, next to the storefront it mirrors — this is
        # as close to a composition root as the entry point currently gets.
        loops.append(
            spawn(watch_catalogue(dp["storefront"], SqliteOfferCache()),
                  name="catalogue_watcher")
        )
        # Ask Telegram who has a birthday, and greet whoever is celebrating.
        # The profile reader is an adapter like any other, which is what keeps
        # the sweep itself testable without a bot.
        loops.append(spawn(
            watch_birthdays(TelegramProfiles(bot), SqliteKnownBirthdays(),
                            SqliteLanguageChoice(), SqliteMessageQueue(),
                            config.birthday_card_url),
            name="birthday_watcher"))
        # Pay for a recommendation once the friend it brought has ordered.
        loops.append(spawn(
            watch_referrals(bot, REFERRAL_PREFIX, config, config.env.admin_ids),
            name="referral_watcher"))
        # Pull whatever changed in the CRM into the local cache, and — as a
        # separate task, so it survives that one dying — watch that it keeps
        # happening (docs/architecture.md §5.5).
        loops.append(spawn(watch_orders(dp["keycrm"]), name="order_sync"))
        loops.append(
            spawn(watch_for_silence(bot, config.env.admin_ids), name="sync_watchdog")
        )
        # Everything the bot sends on its own initiative leaves through here
        # (§6). One sender, which is what makes the capture in
        # core/repos/outbox.py correct on SQLite.
        loops.append(
            spawn(watch_outbox(bot, config.env.admin_ids), name="outbox_sender")
        )
        logger.info("Bot started successfully")

    # Shutdown hook: let outstanding background tasks finish before exit.
    @dp.shutdown()
    async def on_shutdown() -> None:
        # These loop forever; cancel them or drain() just waits out its timeout
        # on every shutdown.
        for task in loops:
            task.cancel()
        await drain()

    # Resolve each user's language before any handler runs, so every handler
    # can just use the injected `t`.
    dp.message.middleware(LanguageMiddleware())
    dp.callback_query.middleware(LanguageMiddleware())

    # On the way out, not on the way in: the one place every API call passes
    # through, so a lapsed Premium subscription costs the logos in a message
    # rather than the message (bot/middlewares.py).
    bot.session.middleware(DropCustomEmoji())
    # The inline panel is a third kind of update and needs the same `t`: it is
    # answered without a message and without a callback.
    dp.inline_query.middleware(LanguageMiddleware())

    # Register routers (order matters: commands first, callbacks second, FSM last)
    dp.include_router(common_router)
    dp.include_router(broadcast_router)
    dp.include_router(demo_router)
    dp.include_router(menu_router)
    dp.include_router(orders_router)
    dp.include_router(info_router)
    dp.include_router(inline_router)
    dp.include_router(support_router)
    dp.include_router(settings_router)
    dp.include_router(onboarding_router)  # FSM catch-all — ALWAYS last

    # Start long-polling
    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
