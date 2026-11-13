"""A broadcast post can be anything a person can send, and arrives as they sent it.

Three layers, tested where each one decides something:

* bot/post.py — what is stored, and which Telegram call it becomes. The checks
  are on the call: its type, its file id, its entities, and that the bot's own
  HTML parse mode is NOT applied on top. That last one is the bug this replaced:
  a lone «<» or «&» in a product name used to fail the send for everyone.
* bot/outbox.py — that a queued post goes through the transport with Telegram's
  failures translated exactly as a plain text's are.
* bot/handlers/broadcast.py — that the sender sees the post before anything is
  queued, that an album is gathered into one post, and that what lands in the
  outbox is the stored post rather than a reference to the sender's message.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import (SendAnimation, SendMediaGroup, SendMessage,
                             SendPhoto, SendRichMessage)
from aiogram.types import InputMediaPhoto, InputMediaVideo

import broadcast_fakes as fakes
from bot import post
from bot.callbacks import BroadcastAction
from bot.handlers import broadcast as mod
from bot.outbox import TelegramNotifier
from bot.states import BroadcastStates
from core.i18n import Texts
from core.ports.notifier import RateLimited
from core.repos import base as repos_base
from core.repos.broadcast import get_unfinished_broadcasts
from core.repos.outbox import claim
from core.repos.schema import init_db
from core.repos.users import save_user

STAFF = 1772368873
CUSTOMER = 555
UK = Texts("uk")
# Everything HTML would choke on, in a line a cosmetics shop really writes.
RISKY = "Нова rom&nd <3 вже тут"
BOLD_FIRST_WORD = [{"type": "bold", "offset": 0, "length": 4}]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    asyncio.run(save_user(CUSTOMER, "+380670000555"))


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    """The transport paces itself; these tests are about which calls happen."""
    monkeypatch.setattr("bot.outbox._PACE_SECONDS", 0)


def _deliver(content: dict, bot: fakes.RecordingBot | None = None):
    bot = bot or fakes.RecordingBot()
    asyncio.run(post.send(bot, CUSTOMER, content, silent=True))
    return bot


# --- bot/post.py ------------------------------------------------------------

def test_text_keeps_its_entities_and_is_never_parsed_as_html():
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(bot, STAFF, text=RISKY,
                                        entities=BOLD_FIRST_WORD))

    [call] = _deliver(content).calls
    assert isinstance(call, SendMessage)
    assert call.text == RISKY
    assert [e.type for e in call.entities] == ["bold"]
    assert call.parse_mode is None
    assert call.disable_notification is True


LINK = "https://telegram.org"
LINK_TEXT = f"Посилання: {LINK}"
LINK_ENTITY = [{"type": "url", "offset": 11, "length": len(LINK)}]


def test_a_post_with_a_link_can_be_stored():
    """The first live post with a link failed before the preview.

    Telegram describes the preview it built as `link_preview_options` naming
    only the url; aiogram fills the options left out with a `Default(...)`
    sentinel that resolves only inside a Bot and cannot be written as JSON.
    """
    bot = fakes.RecordingBot()
    sent = fakes.message(bot, STAFF, text=LINK_TEXT, entities=LINK_ENTITY,
                         link_preview_options={"url": LINK})

    content = post.single(sent)

    assert json.loads(json.dumps(content)) == content
    [call] = _deliver(content).calls
    assert isinstance(call, SendMessage)
    assert call.link_preview_options.url == LINK
    assert [e.type for e in call.entities] == ["url"]


def test_a_preview_the_sender_arranged_is_kept():
    """Small picture above the text is a choice made in the client, and
    send_copy carries the original's options, so the stored post has to."""
    bot = fakes.RecordingBot()
    sent = fakes.message(bot, STAFF, text=LINK_TEXT, entities=LINK_ENTITY,
                         link_preview_options={"url": LINK,
                                               "prefer_small_media": True,
                                               "show_above_text": True})

    [call] = _deliver(post.single(sent)).calls

    options = call.link_preview_options
    assert (options.prefer_small_media, options.show_above_text) == (True, True)


def _wire(value) -> dict:
    """What Telegram receives, after a real Bot has serialised the value and
    filled the gaps from its own defaults — this bot's turn link previews off,
    which is what the copies used to inherit."""
    bot = Bot("42:TEST", default=DefaultBotProperties(link_preview_is_disabled=True))
    return json.loads(bot.session.prepare_value(value, bot=bot, files={}))


def test_a_link_card_the_sender_saw_reaches_the_recipients():
    bot = fakes.RecordingBot()
    sent = fakes.message(bot, STAFF, text=LINK_TEXT, entities=LINK_ENTITY,
                         link_preview_options={"url": LINK})

    [call] = _deliver(post.single(sent)).calls

    assert _wire(call.link_preview_options) == {
        "is_disabled": False, "url": LINK}


def test_a_preview_the_sender_turned_off_stays_off():
    bot = fakes.RecordingBot()
    sent = fakes.message(bot, STAFF, text=LINK_TEXT, entities=LINK_ENTITY,
                         link_preview_options={"is_disabled": True})

    [call] = _deliver(post.single(sent)).calls

    assert _wire(call.link_preview_options)["is_disabled"] is True


def test_a_photo_goes_out_large_with_its_spoiler_and_caption_placement():
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(
        bot, STAFF, **fakes.photo(caption=RISKY, caption_entities=BOLD_FIRST_WORD,
                                  has_media_spoiler=True,
                                  show_caption_above_media=True)))

    [call] = _deliver(content).calls
    assert isinstance(call, SendPhoto)
    assert call.photo == "photo-big"
    assert call.caption == RISKY and call.parse_mode is None
    assert [e.type for e in call.caption_entities] == ["bold"]
    assert call.has_spoiler is True
    assert call.show_caption_above_media is True
    # Only the size that is sent again is stored, once per recipient.
    assert len(content["message"]["photo"]) == 1


def test_a_gif_stays_a_gif():
    bot = fakes.RecordingBot()
    gif = {"file_id": "gif-1", "file_unique_id": "g", "width": 320, "height": 240,
           "duration": 3}
    content = post.single(fakes.message(bot, STAFF, animation=gif,
                                        document={"file_id": "gif-1",
                                                  "file_unique_id": "g"}))

    [call] = _deliver(content).calls
    assert isinstance(call, SendAnimation)
    assert call.animation == "gif-1"


def test_an_album_goes_out_as_one_album_in_the_order_it_was_sent():
    bot = fakes.RecordingBot()
    first = fakes.message(bot, STAFF, media_group_id="g1",
                          **fakes.photo("p1", caption=RISKY,
                                        caption_entities=BOLD_FIRST_WORD))
    second = fakes.message(bot, STAFF, media_group_id="g1",
                           video={"file_id": "v1", "file_unique_id": "v",
                                  "width": 1, "height": 1, "duration": 1})
    content = post.album([second, first])

    [call] = _deliver(content).calls
    assert isinstance(call, SendMediaGroup)
    assert [type(m) for m in call.media] == [InputMediaPhoto, InputMediaVideo]
    assert [m.media for m in call.media] == ["p1", "v1"]
    assert call.media[0].caption == RISKY
    assert call.media[0].parse_mode is None
    assert [e.type for e in call.media[0].caption_entities] == ["bold"]


def test_what_cannot_be_rebuilt_is_refused_before_anything_is_queued():
    bot = fakes.RecordingBot()
    story = fakes.message(bot, STAFF, story={"chat": {"id": 1, "type": "channel"},
                                             "id": 5})
    assert post.problem(post.single(story))
    assert post.problem(post.single(fakes.message(bot, STAFF, text="ok"))) is None


def test_the_journal_gets_words_or_says_what_it_was():
    bot = fakes.RecordingBot()
    assert post.summary(post.single(fakes.message(bot, STAFF, text="Привіт"))) == "Привіт"
    assert post.summary(post.single(fakes.message(
        bot, STAFF, **fakes.photo(caption="Знижки")))) == "Знижки"
    assert post.summary(post.single(fakes.message(bot, STAFF, **fakes.photo()))) == "[photo]"
    two = [fakes.message(bot, STAFF, media_group_id="g", **fakes.photo(f"p{i}"))
           for i in range(2)]
    assert post.summary(post.album(two)) == "[2 × photo]"


def test_an_effect_telegram_refuses_costs_the_effect_not_the_message():
    sender = fakes.RecordingBot()
    content = post.single(fakes.message(sender, STAFF, text="🎉",
                                        effect_id="5104841245755180586"))
    bot = fakes.RecordingBot(TelegramBadRequest(method=None,
                                                message="Bad Request: EFFECT_ID_INVALID"))

    _deliver(content, bot)
    assert [c.message_effect_id for c in bot.calls] == ["5104841245755180586", None]


# What the client sends when a post is composed with headings, a table, a
# numbered list and a photo under a spoiler — the shapes of Bot API 10.3.
RICH = {"blocks": [
    {"type": "heading", "text": "Новинки тижня", "size": 1},
    {"type": "paragraph", "text": ["Тільки ", {"type": "bold", "text": "до неділі"}]},
    {"type": "table", "is_bordered": True, "cells": [
        [{"align": "left", "valign": "top", "text": "Товар", "is_header": True},
         {"align": "right", "valign": "top", "text": "Ціна", "is_header": True}],
        [{"align": "left", "valign": "top", "text": "COSRX Snail"},
         {"align": "right", "valign": "top", "text": "520 ₴"}]]},
    {"type": "list", "items": [
        {"label": "1.", "blocks": [{"type": "paragraph", "text": "Перше"}]},
        {"label": "2.", "blocks": [{"type": "paragraph", "text": "Друге"}]}]},
    {"type": "photo", "has_spoiler": True,
     "photo": [{"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
               {"file_id": "big", "file_unique_id": "b", "width": 1280, "height": 1280}],
     "caption": {"text": "Під спойлером"}},
]}


def test_a_rich_post_goes_out_as_a_rich_message():
    """aiogram's send_copy refuses a rich message outright; it is rebuilt as
    sendRichMessage, block for block."""
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(bot, STAFF, rich_message=RICH))

    assert post.problem(content) is None
    assert json.loads(json.dumps(content)) == content
    [call] = _deliver(content).calls
    assert isinstance(call, SendRichMessage)
    assert call.disable_notification is True
    sent = _wire(call.rich_message)["blocks"]
    assert [b["type"] for b in sent] == ["heading", "paragraph", "table", "list", "photo"]
    assert sent[1]["text"] == RICH["blocks"][1]["text"]
    assert sent[2]["cells"] == RICH["blocks"][2]["cells"]


def test_a_file_in_a_rich_post_is_sent_by_id_and_keeps_its_blur():
    """The two places the received blocks differ from the ones sent."""
    bot = fakes.RecordingBot()
    [call] = _deliver(post.single(fakes.message(bot, STAFF, rich_message=RICH))).calls

    sent = _wire(call.rich_message)["blocks"]
    photo = sent[4]
    assert photo["photo"] == {"type": "photo", "media": "big", "has_spoiler": True}
    assert "has_spoiler" not in photo
    assert photo["caption"] == {"text": "Під спойлером"}
    # The client printed «1.» and «2.»; Telegram numbers the items itself.
    assert [set(item) for item in sent[3]["items"]] == [{"blocks"}, {"blocks"}]


def test_a_rich_post_this_cannot_rebuild_is_refused_not_crashed_on():
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(bot, STAFF, rich_message=RICH))
    content["message"]["rich_message"]["blocks"][4]["photo"] = []

    assert post.problem(content)


def test_the_journal_gets_the_words_of_a_rich_post():
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(bot, STAFF, rich_message=RICH))

    assert post.summary(content).startswith(
        "Новинки тижня Тільки до неділі Товар Ціна COSRX Snail 520 ₴ Перше Друге")


# --- markup a client leaves as text -------------------------------------------

# The owner's test post as Telegram Desktop sent it on 2026-11-13: the inline
# formatting already turned into entities, the headings, table and quote not.
MARKUP = ("# 🧪 Тестова розсилка\n\n"
          "Жирний, курсив, закреслений, моноширинний, прихований текст\n\n"
          "## Новинки тижня\n\n"
          "| Товар | Ціна |\n|---|---|\n| COSRX Snail Mucin | 520 ₴ |\n\n"
          "1. Перший пункт\n\n- маркер\n\n"
          "> Цитата: <3 & co\n\n"
          "https://telegram.org")


def _entity(text: str, part: str, kind: str) -> dict:
    """An entity over `part`, in the UTF-16 units Telegram counts — the
    emoji in the heading is two of them."""
    def units(i: int) -> int:
        return len(text[:i].encode("utf-16-le")) // 2
    start = text.index(part)
    return {"type": kind, "offset": units(start),
            "length": units(start + len(part)) - units(start)}


MARKUP_ENTITIES = [_entity(MARKUP, "Жирний", "bold"),
                   _entity(MARKUP, "курсив", "italic"),
                   _entity(MARKUP, "закреслений", "strikethrough"),
                   _entity(MARKUP, "моноширинний", "code"),
                   _entity(MARKUP, "прихований текст", "spoiler"),
                   _entity(MARKUP, "https://telegram.org", "url")]


def _markup_post(bot=None) -> dict:
    bot = bot or fakes.RecordingBot()
    return post.single(fakes.message(bot, STAFF, text=MARKUP, entities=MARKUP_ENTITIES))


def test_headings_a_table_and_a_quote_typed_as_text_become_rich_markdown():
    source = post.from_markup(_markup_post())["markdown"]

    # The block markup reaches Telegram as typed…
    for line in ("# 🧪 Тестова розсилка", "## Новинки тижня", "| Товар | Ціна |",
                 "|---|---|", "1. Перший пункт", "- маркер"):
        assert line in source.splitlines()
    # …the client's own formatting as inline HTML, which rich Markdown reads…
    assert ("<b>Жирний</b>, <i>курсив</i>, <s>закреслений</s>, "
            "<code>моноширинний</code>, <tg-spoiler>прихований текст</tg-spoiler>"
            in source)
    # …and the quote stays a quote: only «<» and «&» are escaped, never «>».
    assert "> Цитата: &lt;3 &amp; co" in source.splitlines()


def test_a_rich_markdown_post_goes_out_as_one_with_its_buttons():
    content = post.with_buttons(post.from_markup(_markup_post()),
                                post.parse_buttons("Каталог - https://ks.example"))

    assert post.problem(content) is None
    [call] = _deliver(content).calls
    assert isinstance(call, SendRichMessage)
    assert call.rich_message.markdown == content["markdown"]
    assert call.reply_markup.inline_keyboard[0][0].url == "https://ks.example"
    assert call.disable_notification is True


@pytest.mark.parametrize("text", [
    "Знижки тижня:\n- COSRX\n- Anua\n1. Перше\n#хештег",   # lists and a hashtag
    "Ціна 520 ₴ -> 480 ₴",                                    # an arrow mid-line
])
def test_ordinary_text_stays_an_ordinary_message(text):
    bot = fakes.RecordingBot()
    assert post.from_markup(post.single(fakes.message(bot, STAFF, text=text))) is None


# The staff guide as Telegram for macOS sent it on 2026-11-13: «---» turned into
# «—» on sending, and the line with ``` dropped together with the line break
# before it, so the code block is glued to the sentence above.
SENT_GUIDE = ("## 2. Кому надіслати\n\n"
              "| Фільтр | Варіанти |\n|—|—|\n| 🛍 Покупки | усі, жодної |\n\n"
              "Напиши кнопки: текст, тире, посилання."
              "Обрати - https://a.example/\nInstagram - https://b.example/\n"
              "Дві кнопки в один ряд розділяє знак «|».")
SENT_CODE = "Обрати - https://a.example/\nInstagram - https://b.example/\n"


def _guide_source() -> str:
    bot = fakes.RecordingBot()
    sent = fakes.message(bot, STAFF, text=SENT_GUIDE,
                         entities=[_entity(SENT_GUIDE, SENT_CODE, "pre")])
    return post.from_markup(post.single(sent))["markdown"]


def test_a_table_whose_hyphens_became_a_dash_is_a_table_again():
    lines = _guide_source().splitlines()

    assert "|---|---|" in lines
    assert "| Фільтр | Варіанти |" in lines


@pytest.mark.parametrize("sent, restored", [
    ("|:—|:—:|—:|", "|:---|:---:|---:|"),     # alignment kept
    ("| — | — |", "| --- | --- |"),
    ("|——|–|", "|---|---|"),                 # longer runs, an en dash
    ("—", "---"),                            # a divider typed as ---
])
def test_dashes_go_back_to_hyphens_only_where_markdown_needs_them(sent, restored):
    bot = fakes.RecordingBot()
    text = f"# Заголовок\n\n| A | B |\n{sent}\n\nЦіна — 520 ₴, термін – 3 дні"
    source = post.from_markup(post.single(fakes.message(bot, STAFF, text=text)))["markdown"]

    assert restored in source.splitlines()
    # A dash in a sentence is the client's typography, and stays.
    assert "Ціна — 520 ₴, термін – 3 дні" in source.splitlines()


def test_a_table_alone_is_still_noticed_with_its_dashes():
    bot = fakes.RecordingBot()
    text = "| Товар | Ціна |\n|—|—|\n| COSRX | 520 ₴ |"
    content = post.from_markup(post.single(fakes.message(bot, STAFF, text=text)))

    assert content and "|---|---|" in content["markdown"].splitlines()


def test_a_code_block_glued_to_a_sentence_gets_lines_of_its_own():
    source = _guide_source()

    assert ("Напиши кнопки: текст, тире, посилання.\n<pre>Обрати - https://a.example/\n"
            in source)
    assert "</pre>\n\nДві кнопки в один ряд" in source


def test_a_quote_from_the_client_is_a_block_and_ends_before_what_follows():
    """A <blockquote> block runs until a blank line: without one the heading
    after it would be swallowed as raw HTML."""
    bot = fakes.RecordingBot()
    text = "Вступ.Цитата клієнтки\n## Далі"
    sent = fakes.message(bot, STAFF, text=text,
                         entities=[_entity(text, "Цитата клієнтки", "blockquote")])
    source = post.from_markup(post.single(sent))["markdown"]

    assert "Вступ.\n<blockquote>Цитата клієнтки</blockquote>\n\n\n## Далі" in source


def test_a_code_block_keeps_its_language_where_telegram_reads_it():
    bot = fakes.RecordingBot()
    text = "# Код\nprint(1)"
    pre = {**_entity(text, "print(1)", "pre"), "language": "python"}
    source = post.from_markup(post.single(fakes.message(bot, STAFF, text=text,
                                                        entities=[pre])))["markdown"]

    assert '<pre><code class="language-python">print(1)</code></pre>' in source


def test_a_caption_with_markup_is_left_alone():
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(bot, STAFF, **fakes.photo(caption="# Знижки")))
    assert post.from_markup(content) is None


# --- link buttons ------------------------------------------------------------

def test_buttons_are_read_the_way_people_type_them():
    typed = ("Каталог - https://ks.example/catalog\n"
             "\n"
             "Instagram — instagram.com/shop | Канал: t.me/koreanstory\n"
             "Сайт-https://ks.example")
    assert post.parse_buttons(typed) == [
        [{"text": "Каталог", "url": "https://ks.example/catalog"}],
        [{"text": "Instagram", "url": "https://instagram.com/shop"},
         {"text": "Канал", "url": "https://t.me/koreanstory"}],
        [{"text": "Сайт", "url": "https://ks.example"}],
    ]


@pytest.mark.parametrize("line", [
    "Знижка 20%",                 # no link at all
    "Ціна 5.5",                   # a number is not a host
    "https://ks.example",         # a link with nothing to tap on
])
def test_a_line_that_is_not_a_button_is_named(line):
    with pytest.raises(post.ButtonsError) as caught:
        post.parse_buttons(f"Каталог - https://ks.example\n{line}")
    assert caught.value.line == line


def test_too_many_buttons_is_its_own_complaint():
    with pytest.raises(post.ButtonsError) as caught:
        post.parse_buttons("\n".join(f"Б{i} - https://ks.example/{i}"
                                      for i in range(post.BUTTON_ROWS + 1)))
    assert caught.value.line is None


@pytest.mark.parametrize("typed, label", [
    # What the owner sent on 2026-11-13, and was turned away for.
    ("https://www.instagram.com/korean_story_official/", "Instagram"),
    ("instagram.com/korean_story_official", "Instagram"),
    ("t.me/koreanstory", "Telegram"),
    ("https://m.youtube.com/@koreanstory", "YouTube"),
])
def test_a_bare_link_to_a_network_everybody_knows_is_named_after_it(typed, label):
    [[button]] = post.parse_buttons(typed)
    assert button["text"] == label
    assert button["url"].endswith(typed.removeprefix("https://"))


def test_any_other_bare_link_asks_for_words_and_carries_the_link():
    with pytest.raises(post.ButtonsError) as caught:
        post.parse_buttons("https://koreanstory.com.ua/collections/sale")
    assert caught.value.line == "https://koreanstory.com.ua/collections/sale"
    assert caught.value.url == "https://koreanstory.com.ua/collections/sale"


def test_buttons_go_out_under_any_kind_of_post():
    bot = fakes.RecordingBot()
    rows = post.parse_buttons("Каталог - https://ks.example | Instagram - instagram.com/shop")
    posts = [fakes.message(bot, STAFF, text=RISKY),
             fakes.message(bot, STAFF, **fakes.photo(caption="Знижки")),
             fakes.message(bot, STAFF, rich_message=RICH)]

    for sent in posts:
        content = post.with_buttons(post.single(sent), rows)
        assert post.problem(content) is None
        [call] = _deliver(content).calls
        keyboard = call.reply_markup.inline_keyboard
        assert [[(b.text, b.url) for b in row] for row in keyboard] == [
            [("Каталог", "https://ks.example"),
             ("Instagram", "https://instagram.com/shop")]]


def test_no_rows_means_no_buttons():
    bot = fakes.RecordingBot()
    content = post.single(fakes.message(bot, STAFF, text="ok"))
    with_some = post.with_buttons(content, post.parse_buttons("A - https://a.example"))

    assert post.with_buttons(with_some, []) == content
    assert post.button_count(with_some) == 1 and post.button_count(content) == 0


def test_an_album_with_buttons_is_refused():
    """sendMediaGroup takes no keyboard: the buttons would silently vanish."""
    bot = fakes.RecordingBot()
    two = [fakes.message(bot, STAFF, media_group_id="g", **fakes.photo(f"p{i}"))
           for i in range(2)]
    content = post.with_buttons(post.album(two), post.parse_buttons("A - https://a.example"))

    assert post.problem(content)


# --- a promo code under the post ------------------------------------------------

@pytest.mark.parametrize("typed, code", [
    ("NOV20", "NOV20"), (" nov-2026_vip ", "nov-2026_vip"),
    ("N", None), ("NOV 20", None), ("NOV:20", None), ("<b>20</b>", None),
    ("ЗНИЖКА20", None), ("A" * 41, None),
])
def test_a_promo_code_is_held_to_the_shape_shops_write(typed, code):
    assert post.promo_code(typed) == code


def test_the_promo_button_comes_first_and_the_links_after_it():
    bot = fakes.RecordingBot()
    rows = post.parse_buttons("Instagram - instagram.com/shop")
    for sent in (fakes.message(bot, STAFF, text=RISKY),
                 fakes.message(bot, STAFF, **fakes.photo(caption="Знижки")),
                 fakes.message(bot, STAFF, rich_message=RICH)):
        content = post.with_promo(post.with_buttons(post.single(sent), rows),
                                  "NOV20", "🎁 Забрати промокод")
        assert post.problem(content) is None
        [call] = _deliver(content).calls
        first, second = call.reply_markup.inline_keyboard
        assert [(b.text, b.callback_data) for b in first] == [
            ("🎁 Забрати промокод", "promo:NOV20")]
        assert [b.url for b in second] == ["https://instagram.com/shop"]


def test_a_promo_alone_is_a_keyboard_and_none_takes_it_away():
    bot = fakes.RecordingBot()
    plain = post.single(fakes.message(bot, STAFF, text="ok"))
    with_code = post.with_promo(plain, "NOV20", "🎁 Забрати промокод")

    [call] = _deliver(with_code).calls
    assert call.reply_markup.inline_keyboard[0][0].callback_data == "promo:NOV20"
    assert post.promo_of(with_code) == "NOV20"
    assert post.with_promo(with_code, None) == plain


def test_an_album_with_a_promo_is_refused():
    bot = fakes.RecordingBot()
    two = [fakes.message(bot, STAFF, media_group_id="g", **fakes.photo(f"p{i}"))
           for i in range(2)]
    assert post.problem(post.with_promo(post.album(two), "NOV20", "🎁"))


# --- bot/outbox.py ------------------------------------------------------------

def test_the_outbox_sends_a_stored_post():
    sender = fakes.RecordingBot()
    content = post.single(fakes.message(sender, STAFF, **fakes.photo()))
    bot = fakes.RecordingBot()

    asyncio.run(TelegramNotifier(bot).send(CUSTOMER, {"post": content}, silent=False))
    [call] = bot.calls
    assert isinstance(call, SendPhoto) and call.chat_id == CUSTOMER


def test_a_rate_limit_on_a_post_is_still_a_rate_limit():
    sender = fakes.RecordingBot()
    content = post.single(fakes.message(sender, STAFF, text="x"))
    bot = fakes.RecordingBot(TelegramRetryAfter(method=None, message="Too Many Requests",
                                                retry_after=7))
    with pytest.raises(RateLimited) as caught:
        asyncio.run(TelegramNotifier(bot).send(CUSTOMER, {"post": content}, silent=False))
    assert caught.value.retry_after == 7


# --- the flow -------------------------------------------------------------------

def _config():
    return fakes.config(staff=[STAFF])


def _take(bot, state, **content) -> None:
    asyncio.run(mod.process_broadcast_message(
        fakes.message(bot, STAFF, **content), _config(), state, None))


def test_the_sender_sees_the_post_before_being_asked(db):
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, **fakes.photo(caption=RISKY))

    preview, question = bot.to(STAFF)
    assert isinstance(preview, SendPhoto) and preview.caption == RISKY
    assert question.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=1)
    assert state.state == BroadcastStates.waiting_confirm.state
    assert asyncio.run(get_unfinished_broadcasts()) == []


def test_a_rich_post_is_previewed_as_one(db):
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, rich_message=RICH)

    preview, question = bot.to(STAFF)
    assert isinstance(preview, SendRichMessage)
    assert question.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=1)
    assert state.state == BroadcastStates.waiting_confirm.state


def test_markup_is_previewed_and_kept_as_a_rich_message(db):
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, text=MARKUP, entities=MARKUP_ENTITIES)

    preview, question = bot.to(STAFF)
    assert isinstance(preview, SendRichMessage)
    assert state.data["broadcast_post"]["markdown"] == preview.rich_message.markdown
    assert state.state == BroadcastStates.waiting_confirm.state


def test_markup_telegram_will_not_read_falls_back_to_the_post_as_sent(db):
    bot = fakes.RecordingBot(TelegramBadRequest(method=None,
                                                message="Bad Request: can't parse rich message"))
    state = fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, text=MARKUP, entities=MARKUP_ENTITIES)

    refused, preview, question = bot.to(STAFF)
    assert isinstance(refused, SendRichMessage)
    assert isinstance(preview, SendMessage) and preview.text == MARKUP
    assert "markdown" not in state.data["broadcast_post"]
    assert state.state == BroadcastStates.waiting_confirm.state


def test_what_is_queued_is_the_post_itself_not_a_pointer_to_it(db):
    """The original can be deleted the moment the sender confirms: every
    recipient's row carries the file id and the entities, not a message id in
    somebody's private chat."""
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, **fakes.photo(caption=RISKY, caption_entities=BOLD_FIRST_WORD))
    send = BroadcastAction(action="send")
    asyncio.run(mod.process_broadcast_confirm(
        fakes.callback(bot, STAFF, send.pack()), send, _config(), state, None))
    asyncio.run(mod.process_broadcast_name(
        fakes.message(bot, STAFF, text="nov-2026-photo"), _config(), state, None))

    [job] = asyncio.run(get_unfinished_broadcasts())
    assert job["text"] == f"[nov-2026-photo] {RISKY}"
    [row] = asyncio.run(claim(50))
    stored = json.loads(row["payload"])["post"]["message"]
    assert stored["photo"][0]["file_id"] == "photo-big"
    assert stored["caption_entities"] == BOLD_FIRST_WORD
    assert "from" not in stored and "from_user" not in stored


def test_an_album_is_gathered_into_one_post(db, monkeypatch):
    monkeypatch.setattr(mod, "_ALBUM_SETTLE_SECONDS", 0.05)
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state

    async def scenario():
        for i in range(3):
            await mod.process_broadcast_message(
                fakes.message(bot, STAFF, media_group_id="alb",
                              **fakes.photo(f"p{i}", caption="Три" if i == 0 else None)),
                _config(), state, None)
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    preview, question = bot.to(STAFF)
    assert isinstance(preview, SendMediaGroup)
    assert [m.media for m in preview.media] == ["p0", "p1", "p2"]
    assert question.text == UK.MSG_BROADCAST_AUDIENCE.format(count=1, total=1)
    assert len(state.data["broadcast_post"]["album"]) == 3
    assert mod._albums == {}


def test_an_album_that_arrives_after_the_sender_left_is_dropped(db, monkeypatch):
    monkeypatch.setattr(mod, "_ALBUM_SETTLE_SECONDS", 0.05)
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state

    async def scenario():
        await mod.process_broadcast_message(
            fakes.message(bot, STAFF, media_group_id="late", **fakes.photo()),
            _config(), state, None)
        await state.clear()   # /start while the album was still arriving
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert bot.to(STAFF) == []
    assert mod._albums == {}


def test_a_post_telegram_refuses_is_reported_and_nothing_is_queued(db):
    bot = fakes.RecordingBot(TelegramBadRequest(method=None,
                                                message="Bad Request: wrong file id"))
    state = fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, **fakes.photo())

    assert bot.texts_to(STAFF) == [UK.MSG_BROADCAST_PREVIEW_FAILED]
    assert state.state == BroadcastStates.waiting_message.state
    assert asyncio.run(get_unfinished_broadcasts()) == []


def test_a_message_that_cannot_be_rebuilt_is_refused(db):
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, story={"chat": {"id": 1, "type": "channel"}, "id": 5})

    assert bot.texts_to(STAFF) == [UK.MSG_BROADCAST_UNSUPPORTED]
    assert state.state == BroadcastStates.waiting_message.state
