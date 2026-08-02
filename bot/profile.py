"""What Telegram shows about the bot before anyone taps a button.

None of this was set: typing "/" listed no commands, the menu button beside the
input field did nothing useful, and the screen a new customer sees *before*
/start was blank. All of it is set once on startup and localised the same way
the messages are.

Admin commands are registered only in the admins' own chats, so a customer
typing "/" never sees /broadcast or /stats at all.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import (BotCommand, BotCommandScopeAllPrivateChats,
                           BotCommandScopeChat, MenuButtonCommands)
from loguru import logger

from core.i18n import DEFAULT_LANG

# Telegram matches these against the user's language_code; the entry without a
# language is the fallback for everyone else.
CUSTOMER_COMMANDS = {
    "uk": [("start", "Головне меню"), ("stop", "Відписатися від розсилки")],
    "en": [("start", "Main menu"), ("stop", "Unsubscribe from updates")],
}

# Operator surface, English by convention.
ADMIN_COMMANDS = [
    ("start", "Main menu"),
    ("stats", "Funnel and event stats"),
    ("broadcast", "Send a message to all subscribers"),
    ("demo", "Seed demo orders (/demo clear to remove)"),
    ("chatid", "Show this chat's id"),
]

SHORT_DESCRIPTION = {
    "uk": "Ваші замовлення Korean Story — статус, доставка й улюблені засоби. Завжди поруч 🌸",
    "en": "Your Korean Story orders — status, delivery and favourites. Always at hand 🌸",
}

# Shown on the empty screen before the first /start — for many people this is
# the first thing they ever see of the shop inside Telegram.
DESCRIPTION = {
    "uk": (
        "Вітаємо у Korean Story 🌸\n\n"
        "Тут ви за секунду дізнаєтесь, де ваше замовлення й коли на нього чекати. "
        "Побачите історію покупок і улюблені засоби, зможете попросити знижку "
        "на них або підписатися, щоб ми написали, щойно товар знову зʼявиться.\n\n"
        "Потрібна людина — менеджер відповість тут же.\n\n"
        "Натисніть «Почати», і ми познайомимось."
    ),
    "en": (
        "Welcome to Korean Story 🌸\n\n"
        "Find out in seconds where your order is and when to expect it. See your "
        "purchase history and favourite products, ask for a discount on them, or "
        "subscribe and we'll message you the moment something is back in stock.\n\n"
        "Need a person? A manager replies right here.\n\n"
        "Tap Start and let's get acquainted."
    ),
}


async def ensure_menu_button(bot: Bot, chat_id: int) -> None:
    """Make sure *this* chat shows the commands button in the input row.

    `apply()` sets the default for every chat, but a per-chat setting overrides
    the default and outlives whatever set it, so a chat that ever had one keeps
    it. Setting it explicitly on /start is the only way to be sure the button
    is there for the person in front of us.

    The button lives in the input row and opens the command list underneath it.
    A reply keyboard takes that slot away, which is why the bot sends none.
    """
    try:
        await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())
    except Exception as exc:  # noqa: BLE001 — cosmetic, never block /start
        logger.debug("Could not set the menu button for chat {}: {}", chat_id, exc)


async def apply(bot: Bot, admin_ids: list[int]) -> None:
    """Publish commands, menu button and profile texts. Best-effort.

    A failure here must never stop the bot from starting: the profile is
    cosmetic, answering customers is not.
    """
    try:
        for lang, commands in CUSTOMER_COMMANDS.items():
            await bot.set_my_commands(
                [BotCommand(command=c, description=d) for c, d in commands],
                scope=BotCommandScopeAllPrivateChats(),
                # The default language entry carries no language_code, so people
                # whose language we do not support still get a command list.
                language_code=None if lang == DEFAULT_LANG else lang,
            )

        for admin_id in admin_ids:
            try:
                await bot.set_my_commands(
                    [BotCommand(command=c, description=d) for c, d in ADMIN_COMMANDS],
                    scope=BotCommandScopeChat(chat_id=admin_id),
                )
            except Exception as exc:  # noqa: BLE001 — an admin who never started the bot
                logger.debug("Admin commands not set for {}: {}", admin_id, exc)

        # The button left of the input field: opens the command list.
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

        for lang, text in SHORT_DESCRIPTION.items():
            await bot.set_my_short_description(
                short_description=text,
                language_code=None if lang == DEFAULT_LANG else lang,
            )
        for lang, text in DESCRIPTION.items():
            await bot.set_my_description(
                description=text,
                language_code=None if lang == DEFAULT_LANG else lang,
            )

        logger.info("Bot profile published (commands, menu button, descriptions)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not publish bot profile: {}", exc)
