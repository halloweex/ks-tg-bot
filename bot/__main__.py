"""Bot entry point — run with `python -m bot`."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from core.config import load_config
from bot.db import configure as configure_db, init_db
from bot.fsm_storage import SQLiteStorage
from bot.logs import setup_logging
from bot.handlers.broadcast import resume_broadcasts, router as broadcast_router
from bot.handlers.common import router as common_router
from bot.handlers.demo import router as demo_router
from bot.handlers.info import router as info_router
from bot.handlers.menu import router as menu_router
from bot.handlers.onboarding import router as onboarding_router
from bot.handlers.orders import router as orders_router
from bot.handlers.settings import router as settings_router
from bot.handlers.support import router as support_router
from bot.services.keycrm import KeyCRMClient
from bot.services.novaposhta import NovaPoshtaClient
from bot.services.shopify import ShopifyClient
from bot.middlewares import LanguageMiddleware
from bot import profile
from bot.stock import watch as watch_stock
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

    # Conditional Nova Poshta client
    np_keys = config.env.novaposhta_keys
    if np_keys:
        dp["novaposhta"] = NovaPoshtaClient(np_keys)
        logger.info("Nova Poshta client initialized ({} key(s))", len(np_keys))
    else:
        dp["novaposhta"] = None
        logger.warning("Nova Poshta API key not configured — delivery tracking will use CRM data only")

    # Conditional Shopify client (graceful degradation)
    if config.env.shopify_api_token and config.env.shopify_store_url:
        dp["shopify"] = ShopifyClient(
            store_url=config.env.shopify_store_url,
            api_token=config.env.shopify_api_token,
        )
        logger.info("Shopify client initialized")
    else:
        dp["shopify"] = None
        logger.warning(
            "Shopify credentials not configured — running in KeyCRM-only mode"
        )

    # Startup hook: initialize the SQLite database
    stock_watcher: asyncio.Task | None = None

    @dp.startup()
    async def on_startup() -> None:
        nonlocal stock_watcher
        await init_db()
        # Continue any broadcast that a previous restart/redeploy interrupted.
        await resume_broadcasts(bot)
        # Commands, menu button and the text shown before the first /start.
        await profile.apply(bot, config.env.admin_ids)
        # Poll KeyCRM for restocks and notify whoever subscribed.
        stock_watcher = spawn(watch_stock(bot, dp["keycrm"]), name="stock_watcher")
        logger.info("Bot started successfully")

    # Shutdown hook: let outstanding background tasks finish before exit.
    @dp.shutdown()
    async def on_shutdown() -> None:
        # The watcher loops forever; cancel it or drain() just waits out its
        # timeout on every shutdown.
        if stock_watcher is not None:
            stock_watcher.cancel()
        await drain()

    # Resolve each user's language before any handler runs, so every handler
    # can just use the injected `t`.
    dp.message.middleware(LanguageMiddleware())
    dp.callback_query.middleware(LanguageMiddleware())

    # Register routers (order matters: commands first, callbacks second, FSM last)
    dp.include_router(common_router)
    dp.include_router(broadcast_router)
    dp.include_router(demo_router)
    dp.include_router(menu_router)
    dp.include_router(orders_router)
    dp.include_router(info_router)
    dp.include_router(support_router)
    dp.include_router(settings_router)
    dp.include_router(onboarding_router)  # FSM catch-all — ALWAYS last

    # Start long-polling
    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
