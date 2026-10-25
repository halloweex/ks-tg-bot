"""Every press, recorded as what was pressed — never as what was written.

The owner asked a question the event log could not answer: *after which button
do people stop using the bot?* The log held about twenty deliberate events
(registered, orders_viewed, support_opened …), written where a handler chose
to write them. A press on the menu, a page turned in the order digest, a
"back" — none of it left a trace, so the last thing anybody did before going
quiet was, for most people, invisible.

This middleware sits in front of every message and every button press in a
private chat and writes one `tap` event for each, with two fields:

* `what` — a stable identifier: `menu:orders`, `ord:page`, `key:orders`,
  `/start`, `text`, `contact`. For an inline button it is the first two
  segments of its callback data, which are the factory's prefix and its action
  (bot/callbacks.py); the fields after them — an order id, a page, a sku — are
  dropped, because they are not *which button* and some of them identify an
  order.
* `label` — what the person saw on the button, for a human reading the
  dashboard: the button's own text, found in the message the press came from,
  with digits blanked so "Замовлення 18.09" and "Замовлення 24.10" are one row.
  For a reply-keyboard key it is the key's Ukrainian label, whatever language
  the person uses, so one key is one row.

**What is never written.** The text of a message. Somebody who types to the
manager, types their phone number, or types anything else is recorded as
`text` and nothing more. A phone number, a name or a question to support does
not belong in an analytics table, and the identifier is enough to answer the
owner's question.

Group chats are skipped: the support group is where managers work, and their
presses are not a customer's. Admins are recorded like everybody else and left
out when the readout is computed, the same way every other count here treats
them.

Fire-and-forget through `track()`, and wrapped so that a failure here can
never cost the update itself: the handler runs whatever this middleware does.
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from loguru import logger

from bot.analytics import track
from core import texts
from core.i18n import FORMS, SUPPORTED, Texts

TAP = "tap"
MAX_LABEL = 40

#: Labels for what is not a button. Ukrainian, like every other label here:
#: the dashboard reads them to the owner and the marketers.
OTHER_LABELS = {
    "text": "✍️ Текстове повідомлення",
    "contact": "📱 Контакт",
    "photo": "📎 Фото",
    "document": "📎 Файл",
    "sticker": "🙂 Стікер",
    "voice": "🎤 Голосове",
    "video": "📎 Відео",
    "video_note": "🎥 Кружечок",
    "location": "📍 Геолокація",
    "web_app_data": "🧩 Mini App",
}


def _reply_keys() -> dict[str, tuple[str, str]]:
    """Every reply-keyboard label, in every language and form → (what, label)."""
    names = [n for n in dir(texts) if n.startswith("BTN_")
             and isinstance(getattr(texts, n), str) and "{" not in getattr(texts, n)]
    keys: dict[str, tuple[str, str]] = {}
    for name in names:
        what = "key:" + name[len("BTN_"):].lower()
        label = getattr(texts, name)
        for lang in SUPPORTED:
            for form in FORMS:
                value = getattr(Texts(lang, form), name, None)
                if isinstance(value, str) and value:
                    keys.setdefault(value.strip(), (what, label))
    return keys


REPLY_KEYS = _reply_keys()


def _clean(label: str) -> str:
    label = re.sub(r"\d", "#", label.strip())
    return label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1] + "…"


def _button_text(callback: Any) -> str | None:
    """The visible text of the button this press came from, if it can be found."""
    markup = getattr(getattr(callback, "message", None), "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or ():
        for button in row:
            if getattr(button, "callback_data", None) == callback.data:
                return button.text
    return None


def what_was_pressed(event: Any) -> tuple[int, str, str] | None:
    """(chat id, what, label) for a press worth recording, or None.

    Duck-typed on purpose: a Message has `chat` and `content_type`, a
    CallbackQuery has `data` and `from_user`. Tests hand in plain objects.
    """
    data = getattr(event, "data", None)
    if data is not None and hasattr(event, "from_user"):      # a button press
        chat = getattr(getattr(event, "message", None), "chat", None)
        if chat is not None and getattr(chat, "type", "private") != "private":
            return None
        what = ":".join(str(data).split(":")[:2]) or "callback"
        label = _button_text(event) or what
        return event.from_user.id, what, _clean(label)

    chat = getattr(event, "chat", None)
    if chat is None or getattr(chat, "type", None) != "private":
        return None
    text = (getattr(event, "text", None) or "").strip()
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        return chat.id, command, command
    if text and text in REPLY_KEYS:
        what, label = REPLY_KEYS[text]
        return chat.id, what, _clean(label)
    if text:
        return chat.id, "text", OTHER_LABELS["text"]
    kind = getattr(event, "content_type", None) or "other"
    kind = str(getattr(kind, "value", kind))
    return chat.id, kind, OTHER_LABELS.get(kind, "📎 " + kind)


class TapJournal(BaseMiddleware):
    """Write one `tap` per message or button press in a private chat."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            pressed = what_was_pressed(event)
            if pressed is not None:
                chat_id, what, label = pressed
                track(chat_id, TAP, what=what, label=label)
        except Exception as exc:  # noqa: BLE001 — a journal may never cost an update
            logger.warning("Tap journal skipped an update: {}", exc)
        return await handler(event, data)
