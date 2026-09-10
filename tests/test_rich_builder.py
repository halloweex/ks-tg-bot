"""The block builder, and the budget that decides what fits on a screen.

The numbers here are not guesses: Telegram refused 600 blocks with
RICH_MESSAGE_BLOCKS_TOO_MANY, which is how the 500 ceiling and the 400 we hold
ourselves to are known (docs/rich-messages.md).
"""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest

from bot import rich


# --- the budget has to measure the thing it claims to ------------------------
#
# The first version walked mappings only. A bare string inside a mixed rich
# text was never reached, and "paragraph" and "bold" were counted as if
# somebody read them. On the orders screen the two errors leaned opposite ways
# and nearly cancelled, which is the worst way to be wrong.


def test_the_same_sentence_weighs_the_same_in_either_form():
    """This is the property. Every line of the orders screen is built as a
    mixed list, so a form that weighs less than its own text is a budget that
    lets the screen past the real ceiling while still saying yes."""
    from aiogram.types import RichTextBold

    plain = [rich.para("Статус: Прибув")]
    mixed = [rich.para(["Статус: ", RichTextBold(text="Прибув")])]
    assert rich.text_bytes(plain) == rich.text_bytes(mixed)


def test_bare_strings_in_a_mixed_list_are_counted():
    counted = rich.text_bytes([rich.para(["Дата: ", "23.08.2026"])])
    assert counted == len("Дата: 23.08.2026".encode("utf-8"))


def test_structure_is_not_text():
    """"paragraph" is not something anybody reads."""
    assert rich.text_bytes([rich.para("x")]) == 1
    assert rich.text_bytes([rich.divider()]) == 0


def test_a_button_weighs_its_label_and_not_its_destination():
    weighed = rich.text_bytes([rich.buttons(
        rich.button("Меню", callback_data="menu:menu:0:0"))])
    assert weighed == len("Меню".encode("utf-8"))


# --- the counter has to count what Telegram counts ---------------------------


def test_a_list_item_is_a_block():
    """InputRichBlockListItem is the one block type that does not inherit
    InputRichBlock, and its `type` defaults to None — so on the serialised form
    it looks like a bare {"blocks": [...]} and went uncounted. bullets() makes
    one per product, which is where the screen's blocks actually live."""
    # bullets() wraps every entry in a paragraph, so three products are the
    # list, three items, and three paragraphs — seven blocks for what reads as
    # three lines. That ratio is why the undercount mattered: the screen's
    # blocks live almost entirely inside product lists.
    assert rich.count_blocks([rich.bullets(["a", "b", "c"])]) == 7


def test_inline_markup_is_not_a_block():
    """The mirror error: RichTextAnchor carries a `type` that happens to exist
    in InputRichBlockType, so counting by `type` called it a block."""
    from aiogram.types import RichTextBold

    assert rich.count_blocks([rich.para(["a ", RichTextBold(text="b")])]) == 1


def test_the_counter_agrees_with_what_goes_on_the_wire():
    """The property the budget rests on. Before this the two disagreed by 45%
    on the module's own worked example, and fits() waved through screens of
    619 and 759 blocks against a ceiling of 500."""
    from aiogram.types.base import TelegramObject

    def on_the_wire(blocks) -> int:
        total = 0

        def walk(node):
            nonlocal total
            if isinstance(node, TelegramObject):
                if type(node).__name__.startswith("InputRichBlock"):
                    total += 1
                for name in type(node).model_fields:
                    walk(getattr(node, name, None))
            elif isinstance(node, (list, tuple)):
                for item in node:
                    walk(item)

        walk(list(blocks))
        return total

    screen = [
        rich.heading("Заголовок"),
        rich.details("Заказ", [rich.para("рядок"), rich.bullets(["a", "b", "c", "d"])]),
        rich.divider(),
        rich.buttons(rich.button("Меню", callback_data="menu:menu")),
    ]
    assert rich.count_blocks(screen) == on_the_wire(screen)


# --- the rule the safety net does not cover ----------------------------------

def test_the_fallback_keeps_quiet_hours_and_the_effect():
    """They are properties of the message, not of its shape. The rich call
    carried them and the plain fallback dropped them, so a restock Telegram
    refused went out loud, at night, with no 🎉."""
    import asyncio

    sent = {}

    class FakeBot:
        async def send_rich_message(self, **kw):
            raise TelegramBadRequest(method=None, message="nope")

        async def send_message(self, chat_id, text, **kw):
            sent.update(kw)
            return None

    asyncio.run(rich.send(FakeBot(), 1, [rich.para("x")], plain="x",
                          disable_notification=True, message_effect_id="5104"))
    assert sent.get("disable_notification") is True
    assert sent.get("message_effect_id") == "5104"


def test_no_block_may_carry_a_custom_emoji():
    """bot/middlewares.py::DropCustomEmoji retries a refused message with the
    logos stripped, and it looks at text, caption and reply_markup — never
    inside rich_message.blocks. So a custom emoji in a block has no safety net,
    and the day the owner's Premium lapses those screens stop arriving.

    The rule is written in bot/rich.py's docstring. This is what keeps it from
    being only written there: format_cached_order has a `rich` branch that adds
    the Nova Poshta logo, one caller away from breaking it."""
    import json

    from bot.handlers.orders import rich_orders_blocks
    from core.i18n import Texts

    row = {"id": 1, "order_name": "#20014", "status_name": "in_transit",
           "status_group_id": 1, "grand_total": 100, "currency": "грн",
           "ordered_at": "2026-01-01T00:00:00", "tracking_code": "59000123456",
           "shipping_status": "", "delivery_city": "Київ", "receive_point": "№12",
           "products_json": json.dumps([{"name": "Засіб", "qty": 1}])}

    for parcels in (None, {1: ["Статус: В дорозі", "🏤 Відділення №12"]}):
        blocks = rich_orders_blocks([row], Texts("uk"), parcels=parcels)
        dumped = json.dumps([b.model_dump(exclude_none=True, mode="json")
                             for b in blocks], ensure_ascii=False)
        assert "tg-emoji" not in dumped, f"with parcels={bool(parcels)}"


def test_a_refused_screen_tells_the_admins_rather_than_only_the_log():
    """The failure that looks exactly like success. A refusal costs nobody a
    screen — the plain one is a real screen — so every customer would go on
    getting the old one while the whole migration sat dead in production and
    the suite stayed green."""
    import asyncio

    told = []

    async def fake_alert(bot, admin_ids, key, text):
        told.append((key, text))
        return len(admin_ids)

    class FakeBot:
        async def send_rich_message(self, **kw):
            raise TelegramBadRequest(method=None, message="RICH_SOMETHING_WRONG")

        async def send_message(self, chat_id, text, **kw):
            return None

    original = rich.tell_admins_once
    rich.tell_admins_once = fake_alert
    try:
        asyncio.run(rich.send(FakeBot(), 1, [rich.para("x")], plain="x",
                              admins=[7]))
    finally:
        rich.tell_admins_once = original

    assert told, "a refusal that only reaches the log reaches nobody"
    assert "RICH_SOMETHING_WRONG" in told[0][1]
