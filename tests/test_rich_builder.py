"""The block builder, and the budget that decides what fits on a screen.

The numbers here are not guesses: Telegram refused 600 blocks with
RICH_MESSAGE_BLOCKS_TOO_MANY, which is how the 500 ceiling and the 400 we hold
ourselves to are known (docs/rich-messages.md).
"""
from __future__ import annotations

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
