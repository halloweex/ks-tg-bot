"""Middleware that binds each update to the right language and form.

Precedence for the language: an explicit choice stored in the DB wins; otherwise
the language Telegram reports for the user's app; otherwise Ukrainian. Handlers
receive the result as `t` and never resolve it themselves.

Their name rides along as `customer_name`, for the one string that uses it, and
for the same reason: `event_from_user` is the sender of any update, while the
message a callback hands back was sent by the bot.

The form — which gender the Ukrainian copy addresses the reader in — rides the
same rail and for the same reason: a handler that had to resolve it would be a
handler that can forget to. It comes from one column, read in the same query as
the language, and an empty one means the feminine form every string in this
project was written in (core/domain/gender.py says why that, and not the
unmarked one).
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

from core.domain.gender import form
from core.repos.users import get_user_voice
from core.i18n import DEFAULT_LANG, Texts, normalize


class LanguageMiddleware(BaseMiddleware):
    """Inject a language- and form-bound `t` (and the resolved code as `lang`)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        lang = DEFAULT_LANG
        stored_form = None
        if user is not None:
            try:
                stored, stored_form = await get_user_voice(user.id)
            except Exception:  # noqa: BLE001 — a DB hiccup must not eat the update
                stored, stored_form = None, None
            lang = stored or normalize(user.language_code)

        data["lang"] = lang
        # What to call them, for the one line that addresses a person rather
        # than a screen. Resolved here for the same reason the language is: the
        # settings screen renders the menu from a callback, where the obvious
        # `callback.message.from_user` is the **bot**, and `event_from_user` is
        # the only field that is the sender whatever the update is.
        data["customer_name"] = (user.first_name or "") if user else ""
        # `form` turns a missing value into the default rather than raising or
        # guessing, which is what keeps a database hiccup above from changing
        # how anybody is addressed.
        data["t"] = Texts(lang, form(stored_form))
        # The language Telegram reports, so the "switch to your app language"
        # offer knows what to offer even after a choice has been stored.
        data["tg_lang"] = normalize(user.language_code) if user else DEFAULT_LANG

        return await handler(event, data)


class DropCustomEmoji(BaseRequestMiddleware):
    """Send the message without its logos rather than not at all.

    Custom emoji are allowed to this bot because the owner has Telegram
    Premium (core/texts.py:100-123 quotes the rule, and notes that the ids are
    only stable while the pack they come from is). That is a subscription, and
    a subscription can lapse — on the day it does, every message carrying a
    `<tg-emoji>` would start failing, and the ones that matter here are a
    customer's delivery screen and their info pages.

    So the refusal is caught once, at the only place every outgoing call passes
    through, the tags are reduced to the plain emoji they already carry, and the
    call is made again.

    **What the retry's own failure means.** If it comes back "not modified", the
    screen already shows the stripped version and that is a success — it is
    re-raised so the caller's double-tap guard can see it. Any other refusal
    means the logos were not the problem, so the FIRST error is raised instead:
    it is the true diagnosis, and the retry is ours rather than the customer's.

    Only `TelegramBadRequest` is caught around the retry. A rate limit or a
    network failure on the second attempt is a real answer about the second
    attempt, and the outbox knows how to wait on those — swapping it for a stale
    400 would cost a message that was only ever going to need a pause.
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
            logger.debug("Refused ({}), trying without the logos: {}",
                         exc.message, type(method).__name__)
            try:
                result = await make_request(bot, plain)
            except TelegramBadRequest as retried:
                if _NOT_MODIFIED in retried.message.lower():
                    # **The one answer from the retry that must survive.**
                    #
                    # Once a strip has succeeded, the screen already shows the
                    # message without its logos. The next identical redraw is
                    # refused for the logos again, and the stripped retry is
                    # then refused as "not modified" — because it is, byte for
                    # byte, what is already there.
                    #
                    # That is a success from the customer's side, and
                    # `bot/screen.py::render` recognises this exact wording as
                    # its double-tap guard. Replacing it with the first refusal
                    # makes the guard miss, and render answers by sending a NEW
                    # message: one duplicate screen per tap, on exactly the day
                    # this net exists for.
                    raise
                # Dropping the logos did not help, so they were not the problem.
                # The first refusal is the true one and the caller needs it,
                # not a second-hand version of the same failure.
                raise exc from None
            # Logged after rather than before, because the outcome is the only
            # new knowledge here. "Refused, and stripping fixed it" says the
            # emoji were the cause; the refusal alone says nothing that the
            # exception would not have said on its way up.
            logger.warning("Custom emoji were the problem after all: {} ({})",
                           exc.message, type(method).__name__)
            return result


# The one refusal that must never trigger a retry without the logos.
#
# «message is not modified» is Telegram saying the new content equals the old.
# Strip the emoji and the content is no longer equal, so the retry **succeeds**
# — and the screen silently loses its logos for no reason at all. Every other
# 400 either fails again (and the original is raised) or was genuinely the
# emoji. This one is the only one that would quietly do damage.
#
# `bot/screen.py::render` also matches this string, for the double-tap guard.
_NOT_MODIFIED = "message is not modified"


def _is_emoji_refusal(exc: TelegramBadRequest) -> bool:
    """Whether this refusal is worth retrying without the logos.

    **It used to match the words "custom emoji", and that was a guess that
    never held.** `/emojiprobe` asked the live API on 2026-09-12: an unusable
    custom emoji in ordinary text comes back as `Bad Request: DOCUMENT_INVALID`,
    which says nothing about emoji at all. So the net did not fire, and the
    error reached the customer instead of the message without its logos. The
    tests did not show it because they asserted `CUSTOM_EMOJI_INVALID` — the
    same guess the code was written from.

    Guessing a second wording would be the same mistake with a longer list, and
    the wording for the case this exists for — a lapsed Premium — is still
    unmeasured. So the question is turned around: not "is this about emoji" but
    "could dropping them help, and can trying hurt".

    It cannot hurt, because of what the caller does with the answer.
    `_without_custom_emoji` returns None for a call carrying no custom emoji, so
    nothing that has no logos is ever retried. A call that does carry them and
    was refused is already failing; one extra request on that path is cheap, and
    if it fails too the original refusal is what gets raised.

    The single exception is above: the one 400 whose retry would succeed for the
    wrong reason.
    """
    return _NOT_MODIFIED not in exc.message.lower()


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
