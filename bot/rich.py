"""Rich messages, and the plain message that goes beside one.

Bot API 10.1 gave bots structured messages — headings, collapsible sections,
lists, tables, buttons that live *inside* a block instead of in a slab under
the whole thing. Most of the shape of `bot/handlers/orders.py` exists to work
around not having them: the digest with one card and the rest as lines, the
`shown_id` that says which single order is unfolded, the paging that is there
because 4096 characters ran out rather than because anybody wanted pages.

**Blocks, not HTML.** `InputRichMessage` takes an `html` string, a `markdown`
string or a list of `blocks`, and this module builds blocks. The reason is the
failure mode of the other road: an unsupported tag is a 400, and a 400 means
the message does not arrive at all, so an HTML builder needs a whitelist kept
in step with Telegram by hand. Constructing a block that does not exist fails
here, in our process, at the call site.

That guarantee is narrower than it first looks, and the limits are worth
naming because a plan was once built on the wide version:

* pydantic checks the **shape of one block**. It does not check the message:
  `InputRichMessage()` with nothing in it, or with `html` and `blocks` both
  set, is accepted locally and refused by the server.
* Whether the server likes a particular `style` on a button, our nesting, or a
  list where one string was expected is not knowable here. Verified locally
  means the request is well-formed, not that Telegram will take it.

**The plain screen is not a fallback for old clients.** `send` falls back when
Telegram *refuses* the rich message. A client too old to draw blocks is a
different case entirely and this module cannot help with it: the server
accepts the message, answers 200, and the degrading happens on the device. So
every screen keeps a plain form that is a real screen, and what an old client
shows instead of blocks is a product question, not an error path.

**Custom emoji do not survive here yet.** `bot/middlewares.py::DropCustomEmoji`
retries a refused message with the logos stripped, and it looks only at `text`,
`caption` and `reply_markup` — never inside `rich_message.blocks`. Until it
walks blocks too, nothing built here may carry a custom emoji, or the day the
owner's Premium lapses is the day these screens stop arriving.
"""
from __future__ import annotations

from html import escape
from typing import Sequence, Union

from aiogram import Bot
from aiogram.enums import InputRichBlockType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (InlineKeyboardMarkup, InputRichBlock,
                           InputRichBlockButtons,
                           InputRichBlockDetails, InputRichBlockDivider,
                           InputRichBlockList, InputRichBlockListItem,
                           InputRichBlockParagraph, InputRichBlockSectionHeading,
                           InputRichBlockTable, InputRichMessage, Message,
                           RichBlockTableCell, RichMessageButton)
from loguru import logger

from bot.alerts import tell_admins_once

# What a rich text field accepts: a string, one of the RichText* objects, or a
# list mixing them. Typed as an alias rather than `Any` because the failure it
# prevents is silent — a *block* handed to a text field is not rejected, it is
# serialised as `[["type","paragraph"],["text","x"]]` and the customer gets a
# screen full of debris. `bullets` below is the obvious way to make that
# mistake, so it checks.
RichTextLike = Union[str, object, Sequence["RichTextLike"]]

# The three ceilings, each from the API rather than from guesswork, and each
# held well short of the real number. They replaced a single invented budget
# measured against the serialised length of the JSON — the wrong axis: eight
# orders of thirty items each is 530 blocks and only ~18k of JSON, so the cap
# that was supposed to stop it never fired.
#
# Which one bites first depends on the screen. For the order list it is almost
# always the block count.
BLOCK_BUDGET = 400        # of 500
TEXT_BUDGET = 24_000      # of 32768, counted in UTF-8 bytes
DEPTH_BUDGET = 12         # of 16

_BLOCK_TYPES = {member.value for member in InputRichBlockType}


def heading(text: RichTextLike, size: int = 2) -> InputRichBlockSectionHeading:
    """A real heading, where the screens used to bold a line and hope."""
    return InputRichBlockSectionHeading(text=text, size=size)


def para(text: RichTextLike) -> InputRichBlockParagraph:
    return InputRichBlockParagraph(text=text)


def divider() -> InputRichBlockDivider:
    return InputRichBlockDivider()


def bullets(items: Sequence[RichTextLike]) -> InputRichBlockList:
    """One list, one item per entry. Items are rich text, not blocks."""
    for item in items:
        if isinstance(item, (InputRichBlockParagraph, InputRichBlockSectionHeading,
                             InputRichBlockDetails, InputRichBlockList,
                             InputRichBlockButtons, InputRichBlockDivider)):
            raise TypeError(
                "bullets() takes rich text, not blocks — a block here would be "
                f"serialised as debris rather than refused (got {type(item).__name__})"
            )
    return InputRichBlockList(
        items=[InputRichBlockListItem(blocks=[para(item)]) for item in items]
    )


def details(summary: RichTextLike, blocks: Sequence[object], *,
            is_open: bool = False) -> InputRichBlockDetails:
    """A section the reader unfolds — the thing `shown_id` was standing in for.

    The old screen could keep exactly one order unfolded, because unfolding was
    a callback that redrew the whole message and the state had to fit in the
    button. Here it is the client's business and any number can be open at once.
    """
    return InputRichBlockDetails(summary=summary, blocks=list(blocks),
                                 is_open=is_open)


def cell(text: RichTextLike = "", *, header: bool = False,
         align: str = "left") -> RichBlockTableCell:
    """One table cell. `align` and `valign` are required by the API — the
    allowed values are 'left'/'center'/'right' and 'top'/'middle'/'bottom',
    and a cell with no text at all is drawn invisible rather than empty."""
    return RichBlockTableCell(align=align, valign="middle", text=text,
                              is_header=header or None)


def table(rows: Sequence[Sequence[RichBlockTableCell]], *,
          compact: bool = True, striped: bool = False,
          caption: RichTextLike | None = None) -> InputRichBlockTable:
    """A real table.

    Worth its own helper because the last time a table was proposed for the
    orders screen it was refused, and correctly: what was on offer then was
    spaces and a monospace font, which falls apart on a narrow phone. This is
    a different object, drawn by the client, and the refusal was about the
    other one.
    """
    return InputRichBlockTable(cells=[list(r) for r in rows],
                               is_compact=compact or None,
                               is_striped=striped or None,
                               caption=caption)


def buttons(*specs: RichMessageButton) -> InputRichBlockButtons:
    """Buttons belonging to the block above them rather than to the message."""
    return InputRichBlockButtons(buttons=list(specs))


def button(text: str, *, callback_data: str | None = None,
           url: str | None = None, style: str | None = None) -> RichMessageButton:
    return RichMessageButton(text=text, callback_data=callback_data, url=url,
                             style=style)


# ---------------------------------------------------------------------------
# Measuring, against the three ceilings rather than one invented number
# ---------------------------------------------------------------------------

def _walk(value, depth: int = 1):
    """Every mapping in a serialised block tree, with how deep it sits."""
    if isinstance(value, dict):
        yield value, depth
        for item in value.values():
            yield from _walk(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item, depth)


def _dumped(blocks: Sequence[object]) -> list:
    """The blocks as plain data, for the counters to walk.

    `fallback` because aiogram fills unset fields of a media block with a
    `Default(...)` sentinel that only resolves inside a Bot: without it every
    counter — and so `fits` — raises on any screen carrying a photo, which is
    every favourites screen. The sentinel stringifies to something no counter
    cares about; what matters is that it does not stop the walk.
    """
    return [b.model_dump(exclude_none=True, mode="json", fallback=str)
            for b in blocks]


def count_blocks(blocks: Sequence[object]) -> int:
    """How many actual blocks, nested ones included.

    Counted on the models rather than on their serialised form, because the
    serialised form cannot be told apart reliably. Two errors came of trying:

    * `InputRichBlockListItem` is the only block type that does not inherit
      `InputRichBlock`, and its `type` field defaults to None — so a list item
      is dumped as a bare `{"blocks": [...]}` and was skipped. `bullets()`
      makes one per product, so a screen of 25 orders with 30 items each
      counted 399 while carrying 759. `fits()` said yes to every one of them.
    * Inline `RichTextAnchor` and `RichTextMathematicalExpression` carry a
      `type` that happens to exist in `InputRichBlockType`, so they counted as
      blocks. Not used on this screen yet, but loaded.

    Whether Telegram counts a list item toward its 500 is not documented and
    the probe did not ask — it sent 600 flat paragraphs. Counting them is the
    conservative reading and matches the measurement this module's budget was
    set from ("eight orders of thirty items each is 530 blocks").
    """
    total = 0

    def walk(node) -> None:
        nonlocal total
        if isinstance(node, (InputRichBlock, InputRichBlockListItem)):
            total += 1
            for name in type(node).model_fields:
                walk(getattr(node, name, None))
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(list(blocks))
    return total


# Keys whose string value is structure rather than something anybody reads:
# the discriminator, cell alignment, a button's style and where it leads, and
# the language tag on a code block.
_NOT_TEXT = frozenset({"type", "align", "valign", "style", "callback_data",
                       "url", "language"})


def text_bytes(blocks: Sequence[object]) -> int:
    """The text as Telegram counts it, near enough: UTF-8 bytes of every string.

    Bytes rather than characters on purpose. The limit is quoted in characters
    and Ukrainian is two bytes a letter, so counting bytes is the conservative
    reading, and being conservative costs nothing at these sizes.

    It walks values rather than reusing `_walk`, which yields only mappings.
    That cost the first version both halves of its accuracy: a bare string in a
    mixed rich text — `["Статус: ", Bold("Прибув")]`, which is how every line of
    the orders screen is built — was never reached at all, while "paragraph"
    and "bold" were counted as if somebody read them. On the orders screen the
    two errors happened to lean opposite ways and nearly cancel, which is worse
    than being wrong: a screen made mostly of such lines would undercount and
    sail past the real ceiling with the budget still saying yes.
    """
    total = 0

    def walk(value, key=None) -> None:
        nonlocal total
        if isinstance(value, str):
            if key not in _NOT_TEXT:
                total += len(value.encode("utf-8"))
        elif isinstance(value, dict):
            for k, item in value.items():
                walk(item, k)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, key)

    walk(_dumped(blocks))
    return total


def depth(blocks: Sequence[object]) -> int:
    """Deepest nesting in the tree — details inside details inside a list."""
    return max((d for _n, d in _walk(_dumped(blocks))), default=0)


def fits(blocks: Sequence[object]) -> bool:
    """Whether one more section can still go on this screen."""
    return (count_blocks(blocks) <= BLOCK_BUDGET
            and text_bytes(blocks) <= TEXT_BUDGET
            and depth(blocks) <= DEPTH_BUDGET)


# ---------------------------------------------------------------------------
# Sending and editing
# ---------------------------------------------------------------------------

async def send(bot: Bot, chat_id: int, blocks: Sequence[object], *,
               plain: str, reply_markup: InlineKeyboardMarkup | None = None,
               disable_notification: bool | None = None,
               message_effect_id: str | None = None,
               admins: Sequence[int] | None = None) -> Message | None:
    """Send the rich screen, or the plain one if Telegram will not have it.

    `reply_markup` rides along with the blocks: `sendRichMessage` takes one,
    and the first version of this function quietly dropped it — which made
    every rich screen a screen with no way back, and looked from the outside
    like something Telegram did not support.

    `disable_notification` and `message_effect_id` are here for the same
    reason: quiet hours and the 🎉 on a restock are properties of the message,
    not of its shape, and a screen that loses them on the way to rich is a
    regression nobody asked for.
    """
    logger.info("rich.send: {} block(s) going to {}", len(list(blocks)), chat_id)
    try:
        return await bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(blocks=list(blocks)),
            reply_markup=reply_markup,
            disable_notification=disable_notification,
            message_effect_id=message_effect_id)
    except TelegramBadRequest as exc:
        # Loud, because the alternative is invisible. A refusal here does not
        # break anything — the customer gets the plain screen, which is a real
        # screen — and that is exactly the danger: every customer would go on
        # getting the old screen while the log filled up and nobody read it.
        # The whole migration could be dead in production and look identical to
        # working.
        logger.warning("Rich message refused ({}), sending the plain screen",
                       exc.message)
        if admins:
            await tell_admins_once(
                bot, list(admins), "rich-refused",
                f"⚠️ Telegram refused a rich screen: {escape(exc.message)}\n\n"
                f"Customers are getting the plain one. This does not raise and "
                f"does not fail a test — it only shows up here.")
    except AttributeError:
        # An aiogram older than 3.31 has no such method. Worth surviving
        # rather than crashing a screen over a dependency version.
        logger.warning("This aiogram cannot send rich messages; sending plain")
    # The fallback carries them too. Without this the docstring above was a
    # promise the function broke on the one path where it mattered: a restock
    # that Telegram refused as rich went out loud, at night, with no 🎉 —
    # losing exactly the two properties declared for it.
    return await bot.send_message(
        chat_id, plain, reply_markup=reply_markup,
        disable_notification=disable_notification,
        message_effect_id=message_effect_id)


async def edit(message: Message, blocks: Sequence[object], *,
               reply_markup: InlineKeyboardMarkup | None = None) -> Message | bool:
    """Redraw an existing message as these blocks.

    The three explicit `None`s are not tidiness. `editMessageText` carries
    `parse_mode`, `link_preview_options` and `disable_web_page_preview`, and
    the bot sets the first two as defaults for every call
    (`bot/__main__.py`) — so a rich edit goes out asking Telegram to parse HTML
    in a request that has no text at all. `sendRichMessage` has no such fields
    and cannot catch this, which is why only the edit path needs the guard.
    What Telegram makes of that combination is untested; not sending it is free.
    """
    return await message.edit_text(
        rich_message=InputRichMessage(blocks=list(blocks)),
        reply_markup=reply_markup,
        parse_mode=None,
        link_preview_options=None,
        disable_web_page_preview=None)
