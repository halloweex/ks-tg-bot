"""Bot entry point — run with `python -m bot`."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger

from bot.config import load_config
from bot.db import init_db
from bot.handlers.common import router as common_router
from bot.handlers.onboarding import router as onboarding_router
from bot.services.keycrm import KeyCRMClient
from bot.services.shopify import ShopifyClient


async def main() -> None:
    """Initialize all components and start polling."""
    # Load config (reads .env + config.yaml)
    config = load_config()
    logger.info("Config loaded. Brand: {}", config.brand_name)

    # Create Bot instance with HTML parse mode
    bot = Bot(
        token=config.env.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Create Dispatcher
    dp = Dispatcher()

    # Dependency injection via dp workflow_data
    dp["config"] = config
    dp["keycrm"] = KeyCRMClient(api_key=config.env.keycrm_api_key)

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
    @dp.startup()
    async def on_startup() -> None:
        await init_db()
        logger.info("Bot started successfully")

    # Register routers (order matters: CommandStart first, FSM states second)
    dp.include_router(common_router)
    dp.include_router(onboarding_router)

    # Start long-polling
    logger.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
