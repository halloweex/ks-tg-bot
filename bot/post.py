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

**A rich message goes out as one too.** Headings, tables, lists and quotes
composed in the client arrive as `rich_message` blocks, and aiogram's
`send_copy` does not know them — it refuses. They are rebuilt here as
`sendRichMessage`: the received blocks and the blocks it takes are the same
shapes except for two things, a media block names its file as received rather
than as an `InputMedia`, and a list item carries a printed label the sender
does not send (`_rich_input`).

**Link buttons are the post's own.** A person cannot put an inline keyboard on
a message in the client, so the sender types them after the post (`parse_buttons`)
and they are stored beside it, `{"message": {...}, "buttons": [[{text, url}]]}`.
Every send puts them back as the message's `reply_markup`. An album cannot
carry them — `sendMediaGroup` takes no keyboard — and is refused with them.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import ForwardMessage, SendRichMessage
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaAudio, InputMediaDocument, InputMediaPhoto,
                           InputMediaVideo, InputRichMessage, LinkPreviewOptions,
                           Message)
from pydantic import ValidationError
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

# Rich blocks that hold a file, by block type. The file sits under a key named
# like the type, and in an InputMedia of the same type when the block is sent.
_RICH_MEDIA = ("photo", "video", "animation", "audio", "document", "voice_note")
# Of those, the ones whose blur is set on the InputMedia rather than the block.
_RICH_SPOILER = ("photo", "video", "animation")

# Link buttons under a post. Well inside Telegram's own limits: past three in a
# row the labels are cut to a few letters on a phone.
BUTTON_ROWS = 8
BUTTONS_PER_ROW = 3
BUTTON_TEXT_LIMIT = 64
# A link written with its scheme, wherever it starts — «Каталог-https://…»
# has no space to split on.
_SCHEMED_URL = re.compile(r"(?:https?|tg)://\S+$", re.IGNORECASE)
# A host written without one, «instagram.com/shop»: a dot and a word after it,
# so «20%» or «5.5» in a button's text is never taken for a link.
_BARE_HOST = re.compile(r"^[^\s/@]+\.[^\W\d_]{2,}$")


class ButtonsError(ValueError):
    """Typed buttons that cannot be read. `line` is the one that failed, or
    None when there are simply too many."""

    def __init__(self, line: str | None) -> None:
        super().__init__(line or "too many buttons")
        self.line = line


def capture(message: Message) -> dict[str, Any]:
    """The message as it will be stored: JSON-ready, content only.

    Photos keep only their largest size, since that is the one sent again and
    the rest are thumbnails Telegram regenerates anyway.

    Only what Telegram actually sent (`exclude_unset`). A message with a link
    carries `link_preview_options` naming just the url, and aiogram fills the
    options left out with a `Default(...)` sentinel that resolves only inside a
    Bot and cannot be written as JSON — the first live post with a link failed
    right here. Dropping them is also what they mean: rebuilt, they come back
    as the same sentinels. bot/rich.py stringifies them instead, which suits a
    counter but would store a string where a flag is read back.
    """
    data = message.model_dump(mode="json", exclude_none=True, exclude_unset=True,
                              exclude=_NOT_CONTENT)
    if data.get("photo"):
        data["photo"] = data["photo"][-1:]
    return data


def single(message: Message) -> dict[str, Any]:
    return {"message": capture(message)}


def album(messages: list[Message]) -> dict[str, Any]:
    """Parts in the order they were sent, which is the order of their ids."""
    ordered = sorted(messages, key=lambda m: m.message_id)
    return {"album": [capture(m) for m in ordered]}


def parse_buttons(typed: str) -> list[list[dict[str, str]]]:
    """Buttons as the sender typed them: one row per line, «text - link» each,
    several in a row separated by «|».

    Raises ButtonsError naming the line it could not read, so the sender is
    told which one to fix rather than to start again.
    """
    rows = []
    for line in typed.splitlines():
        line = line.strip()
        if not line:
            continue
        row = [_button(part.strip(), line) for part in line.split("|") if part.strip()]
        rows.append(row)
        if len(row) > BUTTONS_PER_ROW or len(rows) > BUTTON_ROWS:
            raise ButtonsError(None)
    return rows


def _button(part: str, line: str) -> dict[str, str]:
    found = _SCHEMED_URL.search(part)
    if found:
        text, url = part[:found.start()], found.group()
    else:
        text, _, last = part.rpartition(" ")
        if not _BARE_HOST.match(urlsplit("https://" + last).netloc):
            raise ButtonsError(line)
        url = "https://" + last
    text = text.strip().rstrip("-–—:").strip()
    if not text or len(text) > BUTTON_TEXT_LIMIT:
        raise ButtonsError(line)
    return {"text": text, "url": url}


def with_buttons(post: dict[str, Any], rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    """The same post with these buttons under it; no rows means none."""
    changed = {key: value for key, value in post.items() if key != "buttons"}
    if rows:
        changed["buttons"] = rows
    return changed


def button_count(post: dict[str, Any] | None) -> int:
    return sum(len(row) for row in (post or {}).get("buttons") or [])


def _markup(post: dict[str, Any]) -> InlineKeyboardMarkup | None:
    rows = post.get("buttons")
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row]
        for row in rows])


def problem(post: dict[str, Any]) -> str | None:
    """Why this post cannot be sent, as a short English reason, or None.

    Asked before anything is queued, so the sender hears it at once instead of
    every recipient's row failing one by one.
    """
    if post.get("buttons"):
        if "album" in post:
            return "buttons on an album"
        try:
            _markup(post)
        except (ValidationError, KeyError, TypeError):
            return "buttons in a form this bot cannot send"
    if "message" in post:
        if post["message"].get("rich_message"):
            try:
                _rich_input(post["message"]["rich_message"])
            except (ValidationError, KeyError, TypeError, IndexError):
                return "rich message in a form this bot cannot rebuild"
            return None
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
        words = (part.get("text") or part.get("caption")
                 or _rich_words(part.get("rich_message")))
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
        await _send_message(bot, chat_id, post["message"], silent=silent,
                            markup=_markup(post))
        return
    media = [_media(_rebuild(part)) for part in post.get("album") or []]
    await bot.send_media_group(chat_id=chat_id, media=media,
                               disable_notification=silent)


async def _send_message(bot: Bot, chat_id: int, stored: dict[str, Any], *,
                        silent: bool,
                        markup: InlineKeyboardMarkup | None = None) -> None:
    message = _rebuild(stored)
    if stored.get("rich_message"):
        # Checked before send_copy, which would refuse it — or, if Telegram
        # ever sends a plain-text twin alongside, quietly send that instead.
        method = SendRichMessage(chat_id=chat_id,
                                 rich_message=_rich_input(stored["rich_message"]),
                                 disable_notification=silent,
                                 message_effect_id=message.effect_id,
                                 reply_markup=markup)
        await _with_effect_fallback(bot, method, message)
        return
    method = message.send_copy(chat_id=chat_id, disable_notification=silent,
                               reply_markup=markup)
    # send_copy forgets two things the sender can see: the blur on a media
    # spoiler and a caption placed above the picture.
    extra = {}
    if message.has_media_spoiler and "has_spoiler" in type(method).model_fields:
        extra["has_spoiler"] = True
    if (message.show_caption_above_media
            and "show_caption_above_media" in type(method).model_fields):
        extra["show_caption_above_media"] = True
    # A link card the sender saw has to reach the recipients too. Telegram
    # leaves "disabled" out when the preview is on, aiogram reads the gap as
    # the bot's default, and this bot turns previews off for its own messages
    # (bot/__main__.py) — so every copy lost its card. Only a sender who turned
    # the preview off sends `is_disabled`, and that stays as it was.
    options = getattr(method, "link_preview_options", None)
    if isinstance(options, LinkPreviewOptions) and isinstance(options.is_disabled, Default):
        extra["link_preview_options"] = options.model_copy(update={"is_disabled": False})
    if extra:
        method = method.model_copy(update=extra)
    await _with_effect_fallback(bot, method, message)


async def _with_effect_fallback(bot: Bot, method, message: Message) -> None:
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


def _rich_input(received: dict[str, Any]) -> InputRichMessage:
    """The blocks as sendRichMessage takes them, from the blocks as received.

    Validated here rather than at the send, so a shape this does not know is
    refused when the sender is still looking at the preview.
    """
    return InputRichMessage.model_validate({
        "blocks": [_input_node(block) for block in received.get("blocks") or []],
        **({"is_rtl": True} if received.get("is_rtl") else {}),
    })


def _input_node(node):
    if isinstance(node, list):
        return [_input_node(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _input_node(value) for key, value in node.items()}
    kind = out.get("type")
    if kind in _RICH_MEDIA and kind in out:
        received = node[kind]
        # A photo arrives as its sizes; the largest is the one to send.
        file = received[-1] if isinstance(received, list) else received
        media = {"type": kind, "media": file["file_id"]}
        if kind in _RICH_SPOILER and out.pop("has_spoiler", None):
            media["has_spoiler"] = True
        out[kind] = media
    if kind == "list":
        # The bullet or number the client printed; Telegram prints it again.
        for item in out.get("items") or []:
            if isinstance(item, dict):
                item.pop("label", None)
    return out


def _rich_words(received: dict[str, Any] | None) -> str:
    """The words of a rich message, for the journal's one line about it."""
    words: list[str] = []

    def walk(node, is_text: bool) -> None:
        if isinstance(node, str):
            if is_text:
                words.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item, is_text)
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, key == "text")

    walk(received or {}, False)
    return " ".join(" ".join(words).split())


def _kind(stored: dict[str, Any]) -> str:
    for kind in ("rich_message", "text", "photo", "video", "animation", "document", "audio",
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
