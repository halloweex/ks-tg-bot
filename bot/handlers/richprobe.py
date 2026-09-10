"""One live conversation with Telegram, to answer what reading could not.

Seven questions about rich messages survived a long read of the API reference,
the changelog and the whole of aiogram 3.31, and every one of them is about
what the *server* does. None can be settled locally: a well-formed request is
not an accepted request, and an accepted request is not a drawn screen.

So this sends the questions instead of arguing about them. `/richprobe` runs a
scripted exchange in the caller's own chat and reports what Telegram actually
answered — verbatim, including error text, because the exact wording is the
finding. Two of the seven cannot be answered by an API response at all and are
left to the person watching: this file says so rather than guessing.

Admin only, like `/demo` and `/stats`, and English for the same reason — it is
an operator surface that sits next to the logs.

**Delete this file once the answers are written down.** It exists to be run a
handful of times; a probe kept around becomes a command somebody finds.
"""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputMediaPhoto,
                           InputRichBlockPhoto, InputRichMessage, Message,
                           RichTextBold)
from loguru import logger

from bot import rich
from bot.handlers.orders import favourite_products
from core import texts
from core.config import AppConfig
from core.repos.catalogue import get_offers
from core.repos.orders import get_cached_orders

router = Router()

PROBE_CALLBACK = "richprobe:tapped"


def _blocks(tag: str) -> list:
    """A screen using every construct the orders screen actually needs."""
    return [
        rich.heading(f"Rich probe · {tag}", size=2),
        rich.para(["status: ", RichTextBold(text=tag)]),
        rich.details("unfold me", [
            rich.para("inside a details block"),
            rich.bullets(["first item", "second item"]),
        ], is_open=False),
        rich.divider(),
        # The question the owner refused once, on a different object: an order
        # history as a real table rather than as spaces in a monospace font.
        rich.table([
            [rich.cell("Дата", header=True), rich.cell("Сума", header=True, align="right"),
             rich.cell("Товарів", header=True, align="right")],
            [rich.cell("23.08.2026"), rich.cell("2 800 грн", align="right"), rich.cell("2", align="right")],
            [rich.cell("04.08.2026"), rich.cell("5 920 грн", align="right"), rich.cell("8", align="right")],
            [rich.cell("22.06.2026"), rich.cell("1 550 грн", align="right"), rich.cell("2", align="right")],
        ], caption="Твої замовлення"),
        rich.divider(),
        rich.buttons(rich.button("tap me", callback_data=PROBE_CALLBACK)),
    ]


async def _ask(question: str, coro) -> str:
    """Run one step and report what Telegram said, not what we hoped.

    Escaped on the way in: the report goes out as HTML, and an error text is
    arbitrary. Unescaped, the probe would fail to deliver exactly the answers
    worth having — the same trap that cost `bot/errors.py` its admin alerts.
    """
    try:
        result = await coro
        detail = type(result).__name__
        if isinstance(result, Message):
            detail += f" id={result.message_id}"
            # Does an accepted rich message come back carrying its blocks? If
            # it does, an edit could in principle be built from what we were
            # given rather than rebuilt from the order rows.
            got = getattr(result, "rich_message", None)
            detail += f", rich_message={'present' if got else 'absent'}"
        return f"✅ {question}\n   → {detail}"
    except TelegramAPIError as exc:
        # The wording is the finding: our double-tap guard in bot/screen.py
        # matches on "message is not modified", and whether Telegram says the
        # same thing for a rich edit is one of the open questions.
        return f"❌ {question}\n   → {type(exc).__name__}: {escape(exc.message)}"
    except Exception as exc:  # noqa: BLE001 — a probe must report, not crash
        return f"💥 {question}\n   → {type(exc).__name__}: {escape(str(exc))}"


@router.message(Command("richprobe"))
async def cmd_richprobe(message: Message, config: AppConfig) -> None:
    """Ask Telegram the seven questions and print the answers."""
    chat_id = message.chat.id
    if message.from_user is None or message.from_user.id not in config.env.admin_ids:
        return

    bot = message.bot
    lines: list[str] = ["<b>Rich probe</b>", ""]

    # 1. Does a rich message go at all, with our blocks and a keyboard beside
    #    the in-block buttons?
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="slab button", callback_data=PROBE_CALLBACK)]])
    sent: Message | None = None
    try:
        sent = await bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(blocks=_blocks("first")),
            reply_markup=keyboard)
        lines.append(f"✅ 1. sendRichMessage + reply_markup\n   → id={sent.message_id}, "
                     f"rich_message={'present' if getattr(sent, 'rich_message', None) else 'absent'}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"❌ 1. sendRichMessage + reply_markup\n   → {type(exc).__name__}: {escape(str(exc))}")

    if sent is not None:
        # 2. Can it be redrawn as different blocks? This is the whole of the
        #    one-live-screen rule under rich.
        lines.append(await _ask(
            "2. edit the rich message with different blocks",
            rich.edit(sent, _blocks("second"))))

        # 3. Redrawn with the SAME blocks — does Telegram use the wording our
        #    double-tap guard matches on?
        lines.append(await _ask(
            "3. edit again with identical blocks (double tap)",
            rich.edit(sent, _blocks("second"))))

        # 4. The question nothing in the documentation answers: what becomes of
        #    the blocks when a rich message is edited as ordinary text.
        lines.append(await _ask(
            "4. edit the rich message with plain text only",
            sent.edit_text("plain text, no rich_message — what happened to the blocks?")))

    # 5. And the reverse, which the one-live-screen rule needs just as much:
    #    a screen that starts plain and becomes rich when the customer opens a
    #    section.
    plain = await bot.send_message(chat_id, "plain to start with")
    lines.append(await _ask(
        "5. edit a plain message into a rich one",
        rich.edit(plain, _blocks("was plain"))))

    # 6. Over the ceiling on purpose, to learn what refusal looks like — the
    #    error text is undocumented for every method, so the only way to know
    #    is to earn one.
    lines.append(await _ask(
        "6. deliberately oversized (600 blocks, over the 500 limit)",
        bot.send_rich_message(
            chat_id=chat_id,
            rich_message=InputRichMessage(
                blocks=[rich.para(f"block {i}") for i in range(600)]))))

    lines += [
        "",
        "<b>Not answerable from here — please look:</b>",
        "7. Does the screen above <i>render</i> — heading, unfoldable section, "
        "list, divider, buttons? An accepted message is not a drawn one.",
        "8. What does an older client show instead? Same chat, older app.",
        "",
        "Tap either button above; the reply says what arrived.",
    ]
    await bot.send_message(chat_id, "\n".join(lines))


@router.message(Command("favprobe"))
async def cmd_favprobe(message: Message, config: AppConfig) -> None:
    """What the favourites screen would become, sent rather than described.

    The question it exists to answer is the owner's, and it is a product one:
    media in a rich message can only be its own block — "Media can be specified
    only as a separate block" — so the row this screen is made of today, a
    thumbnail on the left with the name and price beside it, does not exist in
    blocks. Favourites in rich is a vertical ribbon of full-width cards, which
    is a longer screen that scrolls differently, not the same screen drawn
    better. That is worth looking at rather than reading about.

    It answers one technical unknown on the way: whether Telegram will fetch a
    picture from cdn.shopify.com. The thumbnails in the inline panel prove
    nothing about it — there the URL is handed to the client, not to Telegram's
    servers.
    """
    chat_id = message.chat.id
    if message.from_user is None or message.from_user.id not in config.env.admin_ids:
        return
    bot = message.bot

    cached = await get_cached_orders(chat_id)
    favourites = favourite_products(cached)[:3]
    if not favourites:
        await bot.send_message(chat_id, "No favourites cached — run /demo first.")
        return

    offers = await get_offers([f.get("sku", "") for f in favourites])

    blocks: list = [rich.heading("⭐ Те, що ти купуєш найчастіше", size=2)]
    for item in favourites:
        offer = offers.get(str(item.get("sku") or ""))
        if offer and offer.image_url:
            blocks.append(InputRichBlockPhoto(
                photo=InputMediaPhoto(media=offer.image_url)))
        name = texts.product_label(item["name"], 60)
        blocks.append(rich.para([RichTextBold(text=name)]))
        if offer:
            blocks.append(rich.para(f"{offer.price} грн"))
            blocks.append(rich.buttons(rich.button(
                f"🛒 Замовити ще раз", callback_data=PROBE_CALLBACK)))
        blocks.append(rich.divider())

    lines = [await _ask(
        "favourites as a vertical ribbon of cards",
        rich.send(bot, chat_id, blocks,
                  plain="(the plain favourites screen would go here)"))]
    lines += [
        "",
        "<b>Look at the screen above and compare it with ⭐ Улюблені:</b>",
        "· is the ribbon worth the length it costs?",
        "· did the photos load at all? They come from cdn.shopify.com, and "
        "whether Telegram fetches from there was never established.",
        "",
        "There is no thumbnail-left row in blocks. That is the whole trade.",
    ]
    await bot.send_message(chat_id, "\n".join(lines))


@router.callback_query(F.data == PROBE_CALLBACK)
async def probe_tapped(callback: CallbackQuery) -> None:
    """What a tap on a rich button actually delivers.

    `bot/screen.py::render` is built entirely on `callback.message` being a
    real, editable Message. Whether a button living inside a block delivers one
    — and whether that message still carries its blocks — decides whether the
    existing navigation survives the migration or has to be rebuilt.
    """
    msg = callback.message
    kind = type(msg).__name__ if msg is not None else "None"
    blocks = getattr(msg, "rich_message", None) if msg is not None else None
    report = (
        "<b>Tap arrived</b>\n"
        f"callback.message: {kind}\n"
        f"editable: {isinstance(msg, Message)}\n"
        f"carries rich_message: {'yes' if blocks else 'no'}\n"
        f"data: {escape(str(callback.data))}"
    )
    await callback.answer()
    if msg is not None:
        await callback.bot.send_message(msg.chat.id, report)
    logger.info("Rich probe tap: {}", report.replace("\n", " | "))
