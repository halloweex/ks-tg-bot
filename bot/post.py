"""A broadcast post as it is stored, and the one way it becomes a Telegram call.

A post used to be `message.text` and nothing else: no photo, no video, and no
formatting either, because the text arrived without its entities and went out
under the bot's HTML parse mode — so bold set in the Telegram client vanished,
and a lone «<» or «&» in a product name failed the send for every recipient.

**Stored, not referenced.** Telegram can copy a message by id, and the support
relay does exactly that (bot/outbox.py). A broadcast cannot lean on it: the
queue empties over minutes, and a sender who deletes or edits the original in
the meantime would change or break every copy still waiting. So the message is
stored whole — its file ids, which this bot may reuse for as long as the file
exists, and its entities — and rebuilt per recipient. Nothing depends on the
original after the sender confirms.

**Two shapes.** `{"message": {...}}` is one message of any kind aiogram can
rebuild: text, photo, video, GIF, document, audio, voice, video note, sticker,
poll, location, contact. `{"album": [{...}, ...]}` is a media group, which has
to go out as one `sendMediaGroup` — sent part by part it would arrive as
separate bubbles, the caption hanging under one of them.

**Formatting is sent as entities, never re-parsed.** The rebuilt call carries
the sender's own entities and an explicit `parse_mode=None`; left to the bot's
default, HTML would be applied on top and the first «<3» would fail again.
"""
from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import ForwardMessage
from aiogram.types import (InputMediaAudio, InputMediaDocument, InputMediaPhoto,
                           InputMediaVideo, Message)
from loguru import logger

# What a stored message does not need in order to be sent again. Only weight:
# a reply, a quote or a forward header is not part of what the recipient sees,
# and every byte here is written once per recipient into the outbox.
_NOT_CONTENT = {
    "from_user", "sender_chat", "reply_to_message", "external_reply", "quote",
    "forward_origin", "via_bot", "author_signature", "pinned_message",
    "reply_markup",
}

# The kinds Telegram allows inside one album.
_ALBUM_KINDS = ("photo", "video", "document", "audio")

# A media group holds at most this many items.
ALBUM_LIMIT = 10


def capture(message: Message) -> dict[str, Any]:
    """The message as it will be stored: JSON-ready, content only.

    Photos keep only their largest size, since that is the one sent again and
    the rest are thumbnails Telegram regenerates anyway.
    """
    data = message.model_dump(mode="json", exclude_none=True, exclude=_NOT_CONTENT)
    if data.get("photo"):
        data["photo"] = data["photo"][-1:]
    return data


def single(message: Message) -> dict[str, Any]:
    return {"message": capture(message)}


def album(messages: list[Message]) -> dict[str, Any]:
    """Parts in the order they were sent, which is the order of their ids."""
    ordered = sorted(messages, key=lambda m: m.message_id)
    return {"album": [capture(m) for m in ordered]}


def problem(post: dict[str, Any]) -> str | None:
    """Why this post cannot be sent, as a short English reason, or None.

    Asked before anything is queued, so the sender hears it at once instead of
    every recipient's row failing one by one.
    """
    if "message" in post:
        try:
            method = _rebuild(post["message"]).send_copy(chat_id=0)
        except TypeError:
            return "unsupported message type"
        if isinstance(method, ForwardMessage):
            # aiogram "copies" a story by forwarding it from the chat it was
            # sent in — here the sender's private chat, so every customer would
            # see it forwarded from a member of staff, and every copy would
            # depend on the original staying where it is.
            return "would be a forward from the sender's chat"
        return None
    parts = post.get("album") or []
    if not parts:
        return "empty post"
    if len(parts) > ALBUM_LIMIT:
        return "too many parts for one album"
    for part in parts:
        if not any(part.get(kind) for kind in _ALBUM_KINDS):
            return "unsupported album part"
    return None


def summary(post: dict[str, Any], limit: int = 200) -> str:
    """What the journal keeps about a post: its words, or what kind it was.

    The job row has a text column and the campaign report reads it, so a photo
    without a caption still needs something a person recognises later.
    """
    parts = post.get("album") or [post.get("message") or {}]
    for part in parts:
        words = part.get("text") or part.get("caption")
        if words:
            return words if len(words) <= limit else words[: limit - 1] + "…"
    kinds = [_kind(part) for part in parts]
    return f"[{len(kinds)} × {kinds[0]}]" if len(kinds) > 1 else f"[{kinds[0]}]"


async def send(bot: Bot, chat_id: int, post: dict[str, Any], *, silent: bool) -> None:
    """Deliver the post to one chat. Telegram's errors are left to the caller.

    The outbox translates them (bot/outbox.py), and the preview reports them to
    the sender; neither wants this function deciding what a refusal means.
    """
    if "message" in post:
        await _send_message(bot, chat_id, post["message"], silent=silent)
        return
    media = [_media(_rebuild(part)) for part in post.get("album") or []]
    await bot.send_media_group(chat_id=chat_id, media=media,
                               disable_notification=silent)


async def _send_message(bot: Bot, chat_id: int, stored: dict[str, Any], *,
                        silent: bool) -> None:
    message = _rebuild(stored)
    method = message.send_copy(chat_id=chat_id, disable_notification=silent)
    # send_copy forgets two things the sender can see: the blur on a media
    # spoiler and a caption placed above the picture.
    extra = {}
    if message.has_media_spoiler and "has_spoiler" in type(method).model_fields:
        extra["has_spoiler"] = True
    if (message.show_caption_above_media
            and "show_caption_above_media" in type(method).model_fields):
        extra["show_caption_above_media"] = True
    if extra:
        method = method.model_copy(update=extra)
    try:
        await bot(method)
    except TelegramBadRequest as exc:
        # The same fallback as every other send here: an effect Telegram no
        # longer recognises must not cost anybody the message.
        if not message.effect_id or "effect" not in exc.message.lower():
            raise
        logger.debug("Message effect rejected ({}), sending without it", exc.message)
        await bot(method.model_copy(update={"message_effect_id": None}))


def _rebuild(stored: dict[str, Any]) -> Message:
    return Message.model_validate(stored)


def _kind(stored: dict[str, Any]) -> str:
    for kind in ("text", "photo", "video", "animation", "document", "audio",
                 "voice", "video_note", "sticker", "poll", "location", "contact"):
        if stored.get(kind):
            return kind
    return "message"


def _media(message: Message):
    """One album part as the input sendMediaGroup takes."""
    caption = {"caption": message.caption,
               "caption_entities": message.caption_entities,
               "parse_mode": None}
    if message.photo:
        return InputMediaPhoto(media=message.photo[-1].file_id,
                               has_spoiler=message.has_media_spoiler or None,
                               show_caption_above_media=message.show_caption_above_media,
                               **caption)
    if message.video:
        return InputMediaVideo(media=message.video.file_id,
                               has_spoiler=message.has_media_spoiler or None,
                               show_caption_above_media=message.show_caption_above_media,
                               **caption)
    if message.document:
        return InputMediaDocument(media=message.document.file_id, **caption)
    if message.audio:
        return InputMediaAudio(media=message.audio.file_id, **caption)
    raise TypeError("This message cannot be part of an album")
