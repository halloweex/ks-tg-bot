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
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import (SendAnimation, SendMediaGroup, SendMessage,
                             SendPhoto)
from aiogram.types import InputMediaPhoto, InputMediaVideo

import broadcast_fakes as fakes
from bot import post
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


def test_what_is_queued_is_the_post_itself_not_a_pointer_to_it(db):
    """The original can be deleted the moment the sender confirms: every
    recipient's row carries the file id and the entities, not a message id in
    somebody's private chat."""
    bot, state = fakes.RecordingBot(), fakes.State()
    state.state = BroadcastStates.waiting_message.state
    _take(bot, state, **fakes.photo(caption=RISKY, caption_entities=BOLD_FIRST_WORD))
    asyncio.run(mod.process_broadcast_confirm_text(
        fakes.message(bot, STAFF, text="так"), _config(), state, None))

    [job] = asyncio.run(get_unfinished_broadcasts())
    assert job["text"] == RISKY
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
