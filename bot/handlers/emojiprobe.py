"""One live conversation with Telegram about custom emoji inside rich blocks.

`bot/rich.py` carries a rule in its own docstring: nothing built for a block may
carry a custom emoji, because `bot/middlewares.py::DropCustomEmoji` retries a
refused send with the logos stripped and it looks only at `text`, `caption` and
`reply_markup` — never inside `rich_message.blocks`. The rule is obeyed and a
test enforces it, so nothing is broken today.

What is not known is the size of the hole under it, and two facts decide that.
Neither can be settled by reading:

  1. **Does Telegram accept a `RichTextCustomEmoji` inside a block at all?**
     If it refuses every one of them, the rule is permanent and no net is worth
     building. If it accepts them, the rule is ours to relax and the middleware
     is what stands in the way.

  2. **If it refuses, what does it say?** `_is_emoji_refusal` matches the prose
     "custom emoji" / "custom_emoji". The 500-block ceiling came back as
     `RICH_MESSAGE_BLOCKS_TOO_MANY` — a code, not prose — so a block-level
     refusal may well be worded nothing like the text-level one. Building a net
     that cannot fire is worse than the rule, because the rule is at least
     honest about what it is.

So this asks, rather than arguing. The emoji used is the Nova Poshta logo the
bot already sends successfully in ordinary text every day, which makes the
first step a control: if the plain carrier goes and the block carrier does not,
the refusal is about blocks and not about this bot's right to the emoji.

Admin only, like `/demo` and `/stats`, and English for the same reason: an
operator surface that sits next to the logs.

**Delete this file once the answers are in `docs/rich-messages.md`.** The one
before it (`/richprobe`) was deleted the day it answered, and for the reason
that applies here too: a probe kept around becomes a command somebody finds.
"""
from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InputRichMessage, Message, RichTextCustomEmoji

from bot import rich
from bot.middlewares import _is_emoji_refusal
from core import texts
from core.config import AppConfig

router = Router()

# The logo the bot already puts in the Довідка delivery page and in the plain
# orders screen. Using a known-good id is what makes step 1 a control rather
# than a second unknown.
LOGO = texts.NOVA_POSHTA
FALLBACK = "🚚"


def _verdict(exc: TelegramAPIError) -> str:
    """What our own net would make of this refusal.

    The whole question in one line: a refusal the detector does not recognise
    is a refusal the retry never sees, and the customer gets the raw failure.
    """
    if not isinstance(exc, TelegramBadRequest):
        return "not a BadRequest, so the retry path is not involved at all"
    if _is_emoji_refusal(exc):
        return "_is_emoji_refusal MATCHES — a net built on it would fire"
    return ("_is_emoji_refusal does NOT match — a net built on it would never "
            "fire, and this is the finding")


async def _step(question: str, coro) -> str:
    try:
        result = await coro
    except TelegramAPIError as exc:
        return (f"❌ {question}\n   → {type(exc).__name__}: {escape(exc.message)}"
                f"\n   → {_verdict(exc)}")
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not crash
        return f"💥 {question}\n   → {type(exc).__name__}: {escape(str(exc))}"

    detail = type(result).__name__
    if isinstance(result, Message):
        detail += f" id={result.message_id}"
        got = getattr(result, "rich_message", None)
        detail += f", rich_message={'present' if got else 'absent'}"
    return f"✅ {question}\n   → {detail}"


@router.message(Command("emojiprobe"))
async def cmd_emojiprobe(message: Message, config: AppConfig) -> None:
    """Ask the two questions and print exactly what came back."""
    if message.from_user is None or message.from_user.id not in config.env.admin_ids:
        return

    bot = message.bot
    chat_id = message.chat.id
    lines: list[str] = ["<b>Custom emoji in blocks</b>", ""]

    # 1. The control. This is what the bot sends today, so a failure here means
    #    the problem is the bot's right to the emoji and not the block at all,
    #    and every answer below would be about the wrong thing.
    lines.append(await _step(
        "1. the logo in ordinary text (control: this ships today)",
        bot.send_message(chat_id,
                         f"{texts.custom_emoji(LOGO, FALLBACK)} Нова Пошта")))

    # 2. The question. Same emoji, same chat, same bot — only the carrier
    #    differs, so whatever comes back is about blocks.
    emoji = RichTextCustomEmoji(custom_emoji_id=LOGO, alternative_text=FALLBACK)
    lines.append(await _step(
        "2. the same logo inside a block paragraph",
        bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(blocks=[
                rich.heading("Custom emoji in a block", size=2),
                rich.para([emoji, " Нова Пошта"]),
            ]))))

    # 3. Whether it survives a heading too, because the Довідка delivery page
    #    carries its logo inside a bold heading and that is the shape a real
    #    migration would need.
    lines.append(await _step(
        "3. the same logo inside a section heading",
        bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(blocks=[
                rich.heading([emoji, " Нова Пошта"], size=2),
                rich.para("a heading carrying a logo, as Довідка would need"),
            ]))))

    # 4 and 5. The question the three steps above could not reach: nothing
    #    refused, so nothing showed us how a refusal is worded — and that was
    #    the half that decided whether a net is worth building.
    #
    #    An id Telegram does not know is the closest observable stand-in for the
    #    day this bot loses its right to custom emoji. It is a stand-in and not
    #    the thing itself: a lapsed right and an unknown id may well be
    #    different errors. What it does settle is the shape — whether a
    #    block-level emoji complaint comes back as prose, which
    #    `_is_emoji_refusal` matches, or as a RICH_MESSAGE_* code, which it does
    #    not. The 500-block ceiling came back as a code, which is why this is
    #    worth asking at all.
    bogus = RichTextCustomEmoji(custom_emoji_id="1", alternative_text=FALLBACK)
    lines.append(await _step(
        "4. an id Telegram does not know, inside a block",
        bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(blocks=[
                rich.para([bogus, " unknown id in a block"]),
            ]))))

    # 5. The same bad id in ordinary text, so the two wordings can be compared.
    #    If they differ, the detector was written against one of them and the
    #    other is the hole.
    lines.append(await _step(
        "5. the same unknown id in ordinary text (for comparison)",
        bot.send_message(chat_id,
                         f'{texts.custom_emoji("1", FALLBACK)} unknown id in text')))

    lines += [
        "",
        "<i>1-3 say whether a logo may live in a block at all. 4 and 5 say how "
        "a refusal is worded, which is what decides whether a net built on "
        "_is_emoji_refusal could ever fire. An unknown id is a stand-in for a "
        "lapsed right, not the same thing: it settles the shape of the error, "
        "not its exact text.</i>",
    ]
    await bot.send_message(chat_id, "\n".join(lines))
