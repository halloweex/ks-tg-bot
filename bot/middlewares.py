"""Middleware that binds each update to the right language.

Precedence: an explicit choice stored in the DB wins; otherwise the language
Telegram reports for the user's app; otherwise Ukrainian. Handlers receive the
result as `t` and never resolve it themselves.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from core.repos.users import get_user_language
from core.i18n import DEFAULT_LANG, Texts, normalize


class LanguageMiddleware(BaseMiddleware):
    """Inject a language-bound `t` (and the resolved code as `lang`)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        lang = DEFAULT_LANG
        if user is not None:
            try:
                stored = await get_user_language(user.id)
            except Exception:  # noqa: BLE001 — a DB hiccup must not eat the update
                stored = None
            lang = stored or normalize(user.language_code)

        data["lang"] = lang
        data["t"] = Texts(lang)
        # The language Telegram reports, so the "switch to your app language"
        # offer knows what to offer even after a choice has been stored.
        data["tg_lang"] = normalize(user.language_code) if user else DEFAULT_LANG

        return await handler(event, data)
