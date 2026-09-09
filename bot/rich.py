"""Rich messages, and the plain message that goes when one cannot.

Bot API 10.1 gave bots structured messages — headings, collapsible sections,
lists, tables, buttons that live *inside* a block instead of in a slab under
the whole thing. Most of the shape of `bot/handlers/orders.py` exists to work
around not having them: the digest with one card and the rest as lines, the
`shown_id` that says which single order is unfolded, the paging that is there
because 4096 characters ran out rather than because anybody wanted pages.

**Blocks, not HTML.** `InputRichMessage` takes either an `html` string or a
list of `blocks`, and this module builds blocks. The reason is the failure mode
of the other road: an unsupported tag is a 400, and a 400 means the message
does not arrive at all — so an HTML builder needs a whitelist that has to be
kept in step with Telegram by hand, and a bug in it costs the customer the
whole screen. A block that does not exist cannot be constructed: pydantic
refuses it here, in our process, at the call site.

**The plain version is not a fallback, it is half the feature.** `send` below
takes both and sends the second whenever the first is refused. Every screen
that grows a rich form keeps its plain one, and the plain one stays a real
screen rather than the wreckage of a richer one.
"""
from __future__ import annotations

from typing import Any, Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (InlineKeyboardMarkup, InputRichBlockButtons,
                           InputRichBlockDetails, InputRichBlockDivider,
                           InputRichBlockList, InputRichBlockListItem,
                           InputRichBlockParagraph, InputRichBlockSectionHeading,
                           InputRichMessage, Message, RichMessageButton)
from loguru import logger

# Telegram counts a rich message against a far larger budget than the 4096 of
# an ordinary one, but "far larger" is not "unbounded" and the exact number is
# not something to discover in production. This is the point at which a screen
# stops adding orders and says how many it left out — generous enough that
# almost nobody meets it, small enough to be safe.
TEXT_BUDGET = 20_000


def heading(text: Any, size: int = 2) -> InputRichBlockSectionHeading:
    """A real heading, where the screens used to bold a line and hope."""
    return InputRichBlockSectionHeading(text=text, size=size)


def para(text: Any) -> InputRichBlockParagraph:
    return InputRichBlockParagraph(text=text)


def divider() -> InputRichBlockDivider:
    return InputRichBlockDivider()


def bullets(items: Sequence[Any]) -> InputRichBlockList:
    """One list, one item per entry. Items are plain rich text, not blocks."""
    return InputRichBlockList(
        items=[InputRichBlockListItem(blocks=[para(item)]) for item in items]
    )


def details(summary: Any, blocks: Sequence[Any], *,
            is_open: bool = False) -> InputRichBlockDetails:
    """A section the reader unfolds — the thing `shown_id` was standing in for.

    The old screen could keep exactly one order unfolded, because unfolding was
    a callback that redrew the whole message and the state had to fit in the
    button. Here it is the client's business and any number can be open at once.
    """
    return InputRichBlockDetails(summary=summary, blocks=list(blocks),
                                 is_open=is_open)


def buttons(*specs: RichMessageButton) -> InputRichBlockButtons:
    """Buttons belonging to the block above them rather than to the message."""
    return InputRichBlockButtons(buttons=list(specs))


def button(text: str, *, callback_data: str | None = None,
           url: str | None = None, style: str | None = None) -> RichMessageButton:
    return RichMessageButton(text=text, callback_data=callback_data, url=url,
                             style=style)


def weigh(blocks: Sequence[Any]) -> int:
    """Roughly how much of the budget a list of blocks spends.

    Serialised length rather than a character count of the text: the structure
    is what is sent, and a table of one-word cells is not cheap.
    """
    return sum(len(str(b.model_dump(exclude_none=True, mode="json")))
               for b in blocks)


async def send(bot: Bot, chat_id: int, blocks: Sequence[Any], *,
               plain: str, reply_markup: InlineKeyboardMarkup | None = None,
               ) -> Message | None:
    """Send the rich screen, or the plain one if Telegram will not have it.

    Both are built by the caller. Falling back is not an error path to be
    tidied away later — a client too old to draw blocks is a real reader of
    this bot, and so is the day Telegram changes what it accepts.
    """
    try:
        return await bot.send_rich_message(
            chat_id=chat_id, rich_message=InputRichMessage(blocks=list(blocks)))
    except TelegramBadRequest as exc:
        logger.warning("Rich message refused ({}), sending the plain screen",
                       exc.message)
    except AttributeError:
        # An aiogram older than 3.31 has no such method. Worth surviving
        # rather than crashing a screen over a dependency version.
        logger.warning("This aiogram cannot send rich messages; sending plain")
    return await bot.send_message(chat_id, plain, reply_markup=reply_markup)
