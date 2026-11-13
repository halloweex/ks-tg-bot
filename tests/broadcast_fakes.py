"""What the broadcast tests share: real messages, a bot that only records.

The flow takes aiogram `Message` objects and nothing narrower — it reads the
media group, dumps the message for storage and rebuilds it for the preview — so
a stand-in with `.text` and `.answer` would test a handler that no longer
exists. These are real messages bound to a bot that records every call instead
of making it, which is the one part of the stack a test cannot have.
"""
from __future__ import annotations

from itertools import count
from types import SimpleNamespace

from aiogram.methods import SendMediaGroup, SendMessage
from aiogram.types import CallbackQuery, Message

_ids = count(100)


class RecordingBot:
    """Every Telegram call, in order. Raises what it was told to, once each.

    `refuse` maps a chat to the error every call to it raises — what Telegram
    does with a chat that never pressed /start.
    """

    def __init__(self, *raises, refuse: dict | None = None) -> None:
        self.calls: list = []
        self._raises = list(raises)
        self._refuse = dict(refuse or {})

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if self._raises:
            raise self._raises.pop(0)
        chat = getattr(method, "chat_id", None)
        if chat in self._refuse:
            raise self._refuse[chat]
        return True

    async def send_message(self, chat_id: int, text: str, **kw):
        return await self(SendMessage(chat_id=chat_id, text=text, **kw))

    async def send_media_group(self, chat_id: int, media, **kw):
        return await self(SendMediaGroup(chat_id=chat_id, media=media, **kw))

    def to(self, chat_id: int) -> list:
        return [m for m in self.calls if getattr(m, "chat_id", None) == chat_id]

    def texts_to(self, chat_id: int) -> list[str]:
        return [m.text for m in self.to(chat_id) if isinstance(m, SendMessage)]


class State:
    """FSMContext, as much of it as the flow uses."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None

    async def set_state(self, state) -> None:
        self.state = getattr(state, "state", state)

    async def get_state(self):
        return self.state

    async def update_data(self, **kwargs) -> dict:
        self.data.update(kwargs)
        return dict(self.data)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


def message(bot: RecordingBot, user_id: int, **content) -> Message:
    """A private message from `user_id`, with whatever content is given."""
    raw = {"message_id": next(_ids), "date": 1762960000,
           "chat": {"id": user_id, "type": "private"},
           "from": {"id": user_id, "is_bot": False, "first_name": "Staff"},
           **content}
    return Message.model_validate(raw).as_(bot)


def photo(file_id: str = "photo-big", **extra) -> dict:
    return {"photo": [
        {"file_id": "photo-small", "file_unique_id": "s", "width": 90, "height": 90},
        {"file_id": file_id, "file_unique_id": file_id, "width": 1280, "height": 1280},
    ], **extra}


def config(admins=(), staff=()):
    return SimpleNamespace(
        env=SimpleNamespace(admin_ids=list(admins), broadcaster_ids=list(staff)),
        support_chat_id=-100,
        website_url="https://ks.example",
    )


def callback(bot: RecordingBot, user_id: int, data: str, *,
             screen_id: int = 1) -> CallbackQuery:
    """A tap on a button under the bot's message `screen_id` in a private chat."""
    raw = {"id": str(next(_ids)), "chat_instance": "c", "data": data,
           "from": {"id": user_id, "is_bot": False, "first_name": "Staff"},
           "message": {"message_id": screen_id, "date": 1762960000,
                       "chat": {"id": user_id, "type": "private"},
                       "from": {"id": 1, "is_bot": True, "first_name": "Bot"},
                       "text": "screen"}}
    return CallbackQuery.model_validate(raw, context={"bot": bot})
