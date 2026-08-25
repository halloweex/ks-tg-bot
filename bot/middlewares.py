"""Middleware that binds each update to the right language.

Precedence: an explicit choice stored in the DB wins; otherwise the language
Telegram reports for the user's app; otherwise Ukrainian. Handlers receive the
result as `t` and never resolve it themselves.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import (BaseRequestMiddleware,
                                                     NextRequestMiddlewareType)
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import Response, TelegramMethod
from loguru import logger

from core import texts
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


class DropCustomEmoji(BaseRequestMiddleware):
    """Send the message without its logos rather than not at all.

    Custom emoji are allowed to this bot because the owner has Telegram
    Premium (core/emoji.py quotes the rule). That is a subscription, and a
    subscription can lapse — on the day it does, every message carrying a
    `<tg-emoji>` would start failing, and the ones that matter here are a
    customer's delivery screen and their info pages.

    So the refusal is caught once, at the only place every outgoing call passes
    through, the tags are reduced to the plain emoji they already carry, and the
    call is made again. Anything else Telegram says is re-raised untouched: this
    must never turn some other 400 into a silent retry.
    """

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType,
        bot,
        method: TelegramMethod,
    ) -> Response:
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            plain = _without_custom_emoji(method) if _is_emoji_refusal(exc) else None
            if plain is None:
                raise
            logger.warning("Custom emoji refused ({}), sending plain: {}",
                           exc.message, type(method).__name__)
            return await make_request(bot, plain)


def _is_emoji_refusal(exc: TelegramBadRequest) -> bool:
    """Whether Telegram is complaining about the custom emoji specifically."""
    said = exc.message.lower()
    return "custom emoji" in said or "custom_emoji" in said


def _without_custom_emoji(method: TelegramMethod) -> TelegramMethod | None:
    """The same call with every custom emoji taken out, or None if it had none.

    Two places carry them: the text — where a `<tg-emoji>` reduces to the plain
    emoji it already contains — and the buttons, where `icon_custom_emoji_id`
    simply goes and the label keeps whatever it says. Returning None for a call
    with neither is what keeps an unrelated failure from being retried.
    """
    update = {}
    for field in ("text", "caption"):
        value = getattr(method, field, None)
        if isinstance(value, str):
            plain = texts.strip_custom_emoji(value)
            if plain != value:
                update[field] = plain

    markup = getattr(method, "reply_markup", None)
    plain_markup = _markup_without_icons(markup)
    if plain_markup is not None:
        update["reply_markup"] = plain_markup

    return method.model_copy(update=update) if update else None


def _markup_without_icons(markup):
    """The same keyboard with the button icons dropped, or None if it had none.

    A refused icon fails the whole send, and the button it decorates is usually
    the point of the message — «Де посилка?» is not worth losing to a lapsed
    subscription.
    """
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None)
    if not rows:
        return None
    if not any(getattr(button, "icon_custom_emoji_id", None)
               for row in rows for button in row):
        return None
    stripped = [[button.model_copy(update={"icon_custom_emoji_id": None})
                 for button in row] for row in rows]
    field = "inline_keyboard" if hasattr(markup, "inline_keyboard") else "keyboard"
    return markup.model_copy(update={field: stripped})
