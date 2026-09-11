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

    lines += [
        "",
        "<i>Step 1 failing means this bot cannot use the emoji at all and "
        "steps 2 and 3 answer nothing. Steps 2 and 3 succeeding mean the rule "
        "in bot/rich.py is ours to relax. Them failing with a wording the "
        "detector does not match means a net on _is_emoji_refusal would never "
        "fire.</i>",
    ]
    await bot.send_message(chat_id, "\n".join(lines))
