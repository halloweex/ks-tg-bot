"""What the customer sees when a handler raises.

Until this existed, an exception was silence. aiogram caught it, wrote a
traceback to the log, and the person who tapped the button saw nothing happen
at all — no error, no apology, just a screen that did not change.

The first version of this file answered a tap with `callback.answer(text)` and
was reviewed into the ground for it, correctly: **a callback query is answered
once.** Twenty-two of this bot's twenty-five callback handlers call
`callback.answer()` as their first line, to take the spinner off the button
before doing anything that can fail — so by the time we get here the query is
spent and a second answer reaches nobody. That put the silence straight back on
the one surface that matters, since navigation here is all callbacks.

So a tap is answered with a **message**. It costs a line in the chat, which is
a real price against the one-live-screen rule in `bot/screen.py`, and it is
worth paying: the alternative is the customer learning nothing at all. The bare
`answer()` before it only releases the spinner, and its outcome is ignored on
purpose.

Two more rules hold this together:

* **It never raises.** Everything, the logging line included, sits inside a
  try — and delivery has its own, so a customer who blocked the bot cannot cost
  the admins their alert.
* **It tells the admins once per kind, not once per customer.** A broken sync
  means every tap raises, and an alert per tap is how alerting gets muted.

**What it deliberately does not do** is tell "the CRM is down" from "we have a
bug". `ERR_API_UNAVAILABLE` exists and stays unused, because no adapter lets an
httpx error out: all three clients catch it and return `None`, `[]` or `{}`.
Classifying exceptions here would be a branch that never runs. That is the §5
invariant in `docs/components.md` — an error must not become an empty result —
and it is recorded in `docs/found-during-move.md` rather than faked here.
"""
from __future__ import annotations

from html import escape

from aiogram import Bot
from aiogram.types import CallbackQuery, ErrorEvent, InlineQuery, Update, User
from loguru import logger

from bot.alerts import tell_admins_once
from core.config import AppConfig
from core.i18n import DEFAULT_LANG, Texts, normalize
from core.repos.users import get_user_language


class _Audience:
    """Who was on the other end, and how they can be reached."""

    __slots__ = ("chat_id", "callback", "inline", "user")

    def __init__(self, chat_id: int | None, callback: CallbackQuery | None,
                 inline: InlineQuery | None, user: User | None) -> None:
        self.chat_id = chat_id
        self.callback = callback
        self.inline = inline
        self.user = user


def _audience(update: Update) -> _Audience:
    """Pick the reachable party out of whatever kind of update this was.

    The chat id comes from `callback.message.chat` where there is one: a tap in
    a group is answered in that group, and `from_user.id` would open a private
    chat the person never asked for.
    """
    callback = update.callback_query
    if callback is not None:
        chat_id = (callback.message.chat.id if callback.message is not None
                   else callback.from_user.id)
        return _Audience(chat_id, callback, None, callback.from_user)
    if update.inline_query is not None:
        # No chat to write into — an inline query can come from a chat this bot
        # has never been in. All that can be done is stop the panel spinning.
        return _Audience(None, None, update.inline_query,
                         update.inline_query.from_user)
    message = update.message or update.edited_message
    if message is not None:
        return _Audience(message.chat.id, None, None, message.from_user)
    return _Audience(None, None, None, None)


async def _language(user: User | None) -> str:
    """The same precedence the middleware uses: a stored choice, else Telegram's.

    Resolved again here rather than taken from the handler's injected `t`,
    because the handler is the thing that just failed and there is no promise
    its arguments were ever assembled.
    """
    if user is None:
        return DEFAULT_LANG
    stored = ""
    try:
        stored = await get_user_language(user.id)
    except Exception:  # noqa: BLE001 — the database can be why we are here
        pass
    return stored or normalize(user.language_code)


async def on_error(event: ErrorEvent, bot: Bot, config: AppConfig) -> bool:
    """Apologise to whoever hit this, and tell the admins what it was."""
    exc = event.exception
    try:
        logger.opt(exception=exc).error("Unhandled error: {}", exc)
    except Exception:  # noqa: BLE001 — a logger that throws must not eat the update
        pass

    try:
        who = _audience(event.update)
        try:
            await _apologise(bot, who)
        except Exception:  # noqa: BLE001 — delivery must never cost us the alert
            logger.debug("Could not deliver the apology", exc_info=True)
        await _alert(bot, config, exc)
    except Exception:  # noqa: BLE001 — the handler of last resort handles itself
        logger.exception("The error handler itself failed")
    return True


async def _apologise(bot: Bot, who: _Audience) -> None:
    """Say sorry on whichever surface the person is actually looking at."""
    if who.inline is not None:
        # An inline answer carries results, not errors, so there is no apology
        # to give — but an unanswered query leaves the panel spinning until it
        # times out, and an empty answer at least ends that.
        await who.inline.answer(results=[], cache_time=0)
        return

    if who.chat_id is None:
        return

    t = Texts(await _language(who.user))
    if who.callback is not None:
        # Best effort, result ignored on purpose: if the handler already
        # answered — and most of them do — this reaches nobody, and the message
        # below is what the customer actually reads.
        try:
            await who.callback.answer()
        except Exception:  # noqa: BLE001 — the spinner is cosmetic
            pass
    await bot.send_message(who.chat_id, t.ERR_GENERIC)


async def _alert(bot: Bot, config: AppConfig, exc: BaseException) -> None:
    """Tell the admins, at most once per ten minutes per kind of failure.

    Keyed by the exception's type rather than its message: one broken call
    raises the same class every time with a different id in the text, and
    keying on the text would let it through on every tap.

    Escaped because the bot's default parse mode is HTML and an exception's
    text is arbitrary: `KeyError: '<b'` would come back as a 400, `tell_admins`
    swallows those, and the alert would vanish for exactly the failures whose
    text is most worth reading.
    """
    kind = type(exc).__name__
    await tell_admins_once(
        bot, config.env.admin_ids, f"handler-error:{kind}",
        f"⚠️ A handler raised {escape(kind)}: {escape(str(exc))}\n\n"
        f"See the log for the traceback.",
    )
