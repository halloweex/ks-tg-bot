"""The decoration: a reaction, a vanishing message, an effect, a brand logo.

All four are cosmetic, and the tests are mostly about that word — every one of
them has to fail into a plain, delivered message rather than into a broken
flow. A chat that looks slightly duller is never worth a customer not hearing
from the shop.

The custom emoji are the sharpest case: the permission to send them is the
owner's Telegram Premium subscription, so it can end on a date nobody here
knows about.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage

from bot import screen
from bot.middlewares import DropCustomEmoji
from core import texts


class _Method:
    """A stand-in for one aiogram call, recording what it was given."""

    def __init__(self, raises: Exception | None = None, returns=None) -> None:
        self.calls: list[dict] = []
        self._raises = raises
        self._returns = returns

    async def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._returns


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=SimpleNamespace(), message=message)


# --- the reaction -----------------------------------------------------------

def test_a_message_is_marked_with_one_emoji():
    react = _Method()
    asyncio.run(screen.seen(SimpleNamespace(react=react)))
    assert [item.emoji for item in react.calls[0]["args"][0]] == ["👀"]


def test_a_refused_reaction_is_not_an_error_anybody_hears_about():
    """Telegram allows reactions only from a fixed set, and the set changes.
    The message was delivered either way — that is the part that matters."""
    react = _Method(raises=_bad_request("Bad Request: REACTION_INVALID"))
    asyncio.run(screen.seen(SimpleNamespace(react=react)))  # does not raise


# --- the message that goes away ---------------------------------------------

def test_a_message_takes_itself_back_after_it_has_been_read(monkeypatch):
    scheduled = []
    monkeypatch.setattr(screen, "spawn",
                        lambda coro, name=None: scheduled.append(coro))
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(screen.asyncio, "sleep", sleep)

    delete = _Method()
    answer = _Method(returns=SimpleNamespace(delete=delete))

    async def run():
        await screen.ephemeral(SimpleNamespace(answer=answer), "ok", seconds=30)
        await scheduled[0]

    asyncio.run(run())
    assert answer.calls[0]["args"] == ("ok",)
    assert slept == [30]
    assert len(delete.calls) == 1


def test_a_message_already_gone_is_not_deleted_twice(monkeypatch):
    """The customer may have cleared the chat, or a redeploy may have outlived
    the timer. Neither is worth a line in the error log."""
    async def sleep(seconds):
        return None

    monkeypatch.setattr(screen.asyncio, "sleep", sleep)
    delete = _Method(raises=_bad_request("Bad Request: message to delete not found"))
    asyncio.run(screen._forget(SimpleNamespace(delete=delete), 1))  # does not raise


# --- the effect --------------------------------------------------------------

def test_an_effect_rides_along_when_telegram_takes_it():
    answer = _Method()
    asyncio.run(screen.with_effect(SimpleNamespace(answer=answer), "hi", "42"))
    assert answer.calls[0]["message_effect_id"] == "42"


def test_a_rejected_effect_costs_the_animation_and_not_the_message():
    """The ids are undocumented and read off a client (core/effects.py), so one
    of them going stale has to cost exactly this much."""
    sent: list[dict] = []

    async def answer(text, **kwargs):
        sent.append({"text": text, **kwargs})
        if "message_effect_id" in kwargs:
            raise _bad_request("Bad Request: MESSAGE_EFFECT_ID_INVALID")
        return SimpleNamespace()

    asyncio.run(screen.with_effect(SimpleNamespace(answer=answer), "hi", "42"))
    assert [call.get("message_effect_id") for call in sent] == ["42", None]
    assert sent[1]["text"] == "hi"


def test_anything_else_the_send_says_is_still_an_error():
    """Only the effect is optional. A message Telegram refuses for any other
    reason must not be swallowed by the fallback."""
    answer = _Method(raises=_bad_request("Bad Request: chat not found"))
    with pytest.raises(TelegramBadRequest):
        asyncio.run(screen.with_effect(SimpleNamespace(answer=answer), "hi", "42"))


# --- custom emoji, and what happens when they are refused -------------------

def test_a_custom_emoji_carries_the_plain_one_it_replaces():
    """Telegram draws the fallback wherever the logo cannot go — a system
    notification, a chat list preview, a forward by a non-premium reader."""
    assert texts.custom_emoji("42", "🚚") == '<tg-emoji emoji-id="42">🚚</tg-emoji>'
    assert texts.strip_custom_emoji(texts.custom_emoji("42", "🚚")) == "🚚"


def test_stripping_leaves_the_rest_of_the_message_alone():
    before = f"<b>{texts.custom_emoji('42', '🚚')} Ваші відправлення</b>"
    assert texts.strip_custom_emoji(before) == "<b>🚚 Ваші відправлення</b>"


def test_a_refused_logo_costs_the_logo_and_not_the_message():
    """The permission is the owner's Premium subscription, which can lapse
    without anybody here doing anything. The delivery screen must not go with
    it.

    Note the wording below is the one this test was written with, and it is NOT
    the one Telegram actually sends — see the tests at the end of this file.
    It is kept because it must keep working, not because it was ever observed.
    """
    sent = []

    async def make_request(bot, method):
        sent.append(method.text)
        if "tg-emoji" in method.text:
            raise _bad_request("Bad Request: CUSTOM_EMOJI_INVALID")
        return "sent"

    method = SendMessage(chat_id=1, text=f"{texts.custom_emoji('42', '🚚')} Ваші")
    result = asyncio.run(DropCustomEmoji()(make_request, None, method))

    assert result == "sent"
    assert sent[1] == "🚚 Ваші", "the second try is the same message, plain"


def test_a_message_without_logos_is_never_retried():
    """Only the emoji are optional. Retrying anything else would turn some
    other 400 into a silent second attempt."""
    calls = []

    async def make_request(bot, method):
        calls.append(method.text)
        raise _bad_request("Bad Request: CUSTOM_EMOJI_INVALID")

    with pytest.raises(TelegramBadRequest):
        asyncio.run(DropCustomEmoji()(
            make_request, None, SendMessage(chat_id=1, text="плаский текст")))
    assert len(calls) == 1


def test_any_other_refusal_travels_up_untouched():
    async def make_request(bot, method):
        raise _bad_request("Bad Request: chat not found")

    with pytest.raises(TelegramBadRequest):
        asyncio.run(DropCustomEmoji()(
            make_request, None,
            SendMessage(chat_id=1, text=texts.custom_emoji("42", "🚚"))))


def test_every_custom_emoji_id_is_a_telegram_id():
    """A typo here is a logo that never draws — the middleware strips the tag,
    the message goes out plain, and nobody hears about it. These four were read
    back from the packs through getStickerSet and checked against the live bot;
    what this guards is a hand edit."""
    for name in ("NOVA_POSHTA", "VISA", "MASTERCARD", "MONOBANK", "PRIVAT24"):
        value = getattr(texts, name)
        assert value.isdigit() and len(value) >= 18, name


# --- logos on the buttons ---------------------------------------------------

def _keyboard(icon: str | None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Де посилка?", callback_data="x", icon_custom_emoji_id=icon)]])


def test_a_refused_button_icon_costs_the_icon_and_not_the_button():
    """A button icon is the same privilege as a logo in the text, and fails the
    same way. «Де посилка?» is usually the point of the message it is on."""
    sent = []

    async def make_request(bot, method):
        icon = method.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id
        sent.append(icon)
        if icon:
            raise _bad_request("Bad Request: CUSTOM_EMOJI_INVALID")
        return "sent"

    method = SendMessage(chat_id=1, text="картка", reply_markup=_keyboard("42"))
    assert asyncio.run(DropCustomEmoji()(make_request, None, method)) == "sent"
    assert sent == ["42", None], "the second try is the same button, plain"


def test_a_keyboard_without_icons_is_not_a_reason_to_retry():
    calls = []

    async def make_request(bot, method):
        calls.append(method)
        raise _bad_request("Bad Request: CUSTOM_EMOJI_INVALID")

    with pytest.raises(TelegramBadRequest):
        asyncio.run(DropCustomEmoji()(
            make_request, None,
            SendMessage(chat_id=1, text="картка", reply_markup=_keyboard(None))))
    assert len(calls) == 1


def test_an_unreachable_anchor_gets_a_new_screen_instead_of_a_crash():
    """Telegram hands back an InaccessibleMessage when the anchor is older than
    it keeps, or was deleted. It carries an id and a chat and nothing else, so
    edit_text raised AttributeError — which reached the customer as silence,
    not as the new screen render() exists to draw."""
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text))
            return "new screen"

    inaccessible = SimpleNamespace(chat=SimpleNamespace(id=77), message_id=1)
    callback = SimpleNamespace(message=inaccessible, bot=_Bot())

    result = asyncio.run(screen.render(callback, "нова версія екрана"))
    assert sent == [(77, "нова версія екрана")]
    assert result == "new screen"


# --- render knows the shape of the screen it is drawing on --------------------
#
# Measured against the live API (docs/rich-messages.md): plain text written
# over a rich message succeeds, keeps the message id, and silently drops the
# blocks. Seventeen call sites reach render(), so a half-migrated screen would
# decay on the first tap with nothing in the log to say why.


def _anchor(monkeypatch, *, rich: bool):
    """A real Message — render() checks isinstance before it edits — with
    edit_text intercepted so nothing goes near Telegram."""
    from aiogram.types import Message as _M
    raw = {"message_id": 9, "date": 0, "chat": {"id": 5, "type": "private"},
           "text": "before"}
    if rich:
        raw["rich_message"] = {"blocks": [{"type": "paragraph", "text": "x"}]}
    msg = _M.model_validate(raw)
    done: list = []

    async def fake_edit(self, text=None, reply_markup=None, **kw):
        done.append(("plain", text))
        return self

    monkeypatch.setattr(_M, "edit_text", fake_edit)
    return msg, done


def _said(fn):
    """What loguru wrote while fn ran. caplog cannot see it: loguru does not
    go through the standard logging module."""
    from loguru import logger
    said: list[str] = []
    sink = logger.add(lambda m: said.append(str(m)), level="ERROR")
    try:
        fn()
    finally:
        logger.remove(sink)
    return "\n".join(said)


def test_plain_over_a_rich_screen_is_reported_before_it_is_done(monkeypatch):
    """It is done anyway: refusing would leave the customer tapping a screen
    that never changes, and silence is what this bot has been removing."""
    msg, done = _anchor(monkeypatch, rich=True)
    callback = SimpleNamespace(message=msg, bot=None)

    said = _said(lambda: asyncio.run(screen.render(callback, "плоский текст")))

    assert done == [("plain", "плоский текст")], "the edit still happens"
    assert "rich screen" in said, (
        "a migration that missed a caller must not fail silently"
    )


def test_a_plain_anchor_says_nothing(monkeypatch):
    msg, done = _anchor(monkeypatch, rich=False)
    callback = SimpleNamespace(message=msg, bot=None)
    said = _said(lambda: asyncio.run(screen.render(callback, "плоский текст")))
    assert done == [("plain", "плоский текст")]
    assert "rich screen" not in said


def test_blocks_reach_a_plain_anchor(monkeypatch):
    """This is the regression that cost a day, so it is asserted from the
    outside: blocks offered, plain anchor, and the edit must carry them.

    `render` used to drop the blocks whenever the anchor was plain. That gave
    the admin gate in `orders_screen` teeth while the gate existed; it was
    removed on 2026-09-10 and the line outlived it. The menu message is plain,
    the menu is the only entrance to the orders screen when the bottom keyboard
    is off — so every tap rebuilt six blocks and drew the old plain screen, with
    nothing in the log to say so."""
    from bot import rich as R

    msg, _done = _anchor(monkeypatch, rich=False)
    captured = {}

    async def spy(self, text=None, reply_markup=None, **kw):
        captured["rich"] = kw.get("rich_message")
        captured["text"] = text
        return self

    from aiogram.types import Message as _M
    monkeypatch.setattr(_M, "edit_text", spy)

    callback = SimpleNamespace(message=msg, bot=None)
    asyncio.run(screen.render(callback, "плоский текст", None,
                              blocks=[R.para("то, ради чего всё затевалось")]))

    assert captured["rich"] is not None, (
        "blocks were built and render threw them away — the screen the "
        "customer sees is decided by the screen, not by the message it "
        "replaces"
    )
    assert captured["text"] is None, "a rich edit carries no text field"


def test_a_deliberate_move_to_a_plain_screen_says_nothing(monkeypatch):
    """«📋 Меню» leaves the orders screen for the menu, and the menu is plain.
    Without a way to say so the warning fired on the commonest tap in the bot,
    and a detector that cries wolf every second tap is not a detector."""
    msg, done = _anchor(monkeypatch, rich=True)
    callback = SimpleNamespace(message=msg, bot=None)
    said = _said(lambda: asyncio.run(
        screen.render(callback, "меню", plain_ok=True)))
    assert done == [("plain", "меню")], "the move still happens"
    assert "rich screen" not in said


def test_an_unreachable_anchor_still_gets_its_blocks(monkeypatch):
    """The third exit. There is nothing to edit, so render sends — and for as
    long as blocks existed it sent the plain screen and dropped them, with the
    rich screen's slab still attached. The same defect as the anchor rule
    removed above, in the branch written before blocks existed."""
    from aiogram.types import InaccessibleMessage
    from bot import rich as R

    msg = InaccessibleMessage.model_validate(
        {"message_id": 12, "date": 0, "chat": {"id": 5, "type": "private"}})
    calls: list = []

    class _Bot:
        async def send_message(self, chat_id, text, reply_markup=None, **kw):
            calls.append(("plain", text))
            return "new"

        async def send_rich_message(self, chat_id, rich_message=None,
                                    reply_markup=None, **kw):
            calls.append(("rich", len(rich_message.blocks)))
            return "new"

    callback = SimpleNamespace(message=msg, bot=_Bot())
    asyncio.run(screen.render(callback, "плоский текст", None,
                              blocks=[R.para("один"), R.para("два")]))

    assert calls == [("rich", 2)], (
        f"blocks were built and dropped on the way out: {calls}")


def test_an_unreachable_anchor_with_no_blocks_still_sends_the_plain_screen(monkeypatch):
    from aiogram.types import InaccessibleMessage

    msg = InaccessibleMessage.model_validate(
        {"message_id": 12, "date": 0, "chat": {"id": 5, "type": "private"}})
    calls: list = []

    class _Bot:
        async def send_message(self, chat_id, text, reply_markup=None, **kw):
            calls.append(text)
            return "new"

    callback = SimpleNamespace(message=msg, bot=_Bot())
    asyncio.run(screen.render(callback, "плоский текст"))
    assert calls == ["плоский текст"]


# --- the detector must not be muted in advance -------------------------------


def test_plain_ok_is_earned_in_exactly_one_place():
    """`plain_ok=True` tells render() that landing plain text on a rich screen
    is meant here rather than a caller the migration missed. It is earned only
    where the anchor can ACTUALLY be rich.

    It stood in twelve places and was earned in one. The other eleven were
    reachable only from plain anchors, where the warning could never have
    fired — so the flag bought nothing and silenced the detector in advance, on
    exactly the paths any future migration would travel.

    Which anchors can be rich is not a matter of opinion: enumerate the
    callbacks the two rich screens actually carry. Today that is `menu:menu`
    and `disc:ask:` and nothing else, so `back_to_menu` is the one caller that
    can meet a rich screen.

    A new `plain_ok=True` should fail this test. That is the point: adding one
    is a claim about which screens are rich, and the claim belongs here."""
    import re
    import subprocess

    from tests.conftest import REPO_ROOT

    found = subprocess.run(
        ["grep", "-rn", "--include=*.py", "plain_ok=True", "bot"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.splitlines()

    callers = set()
    for line in found:
        path, lineno, _rest = line.split(":", 2)
        source = (REPO_ROOT / path).read_text().splitlines()
        for above in reversed(source[:int(lineno)]):
            match = re.match(r"\s*(?:async )?def ([a-z_]+)", above)
            if match:
                callers.add(match.group(1))
                break

    assert callers == {"back_to_menu"}, (
        f"plain_ok=True is used by {sorted(callers)}. Leaving the rich orders "
        f"or favourites screen for the menu is the one place it is earned; "
        f"everywhere else the anchor is always plain and the flag only turns "
        f"the detector off ahead of time.")


# --- the wording was a guess, and the guess was wrong ------------------------
#
# `/emojiprobe` asked the live API on 2026-09-12. An unusable custom emoji in
# ordinary text comes back as `Bad Request: DOCUMENT_INVALID`, which says
# nothing about emoji at all — so the net did not fire and the refusal reached
# the customer. Every test above asserted `CUSTOM_EMOJI_INVALID`, the same guess
# the code was written from, which is why the suite was green the whole time.


def _carrying_a_logo() -> SendMessage:
    return SendMessage(chat_id=1, text=f"{texts.custom_emoji('42', '🚚')} Ваші")


def _refusing(wording: str, *, then=None):
    """A transport that refuses the first call and does `then` on the second."""
    calls: list = []

    async def make_request(bot, method):
        calls.append(getattr(method, "text", None))
        if len(calls) == 1:
            raise _bad_request(wording)
        if then is not None:
            raise _bad_request(then)
        return "sent"

    return make_request, calls


def test_the_wording_telegram_actually_sends_is_retried():
    """The measured one. This is the assertion the suite was missing."""
    make_request, calls = _refusing("Bad Request: DOCUMENT_INVALID")
    result = asyncio.run(DropCustomEmoji()(make_request, None, _carrying_a_logo()))

    assert result == "sent"
    assert calls[1] == "🚚 Ваші"


def test_a_wording_nobody_has_seen_yet_is_retried_too():
    """The wording for the case this net exists for — a lapsed Premium — has
    still never been observed. Matching on words means guessing it, and guessing
    is what put the hole here. The question is turned around instead: the call
    carries logos and was refused, so dropping them is worth one attempt."""
    make_request, calls = _refusing("Bad Request: SOMETHING_NOBODY_WROTE_DOWN")
    result = asyncio.run(DropCustomEmoji()(make_request, None, _carrying_a_logo()))

    assert result == "sent"
    assert calls[1] == "🚚 Ваші"


def test_not_modified_never_costs_a_screen_its_logos():
    """The one refusal whose retry would succeed for the wrong reason.

    «message is not modified» means the new content equals the old. Strip the
    logos and it no longer does, so the retry goes through and the screen
    quietly loses them — a silent downgrade caused by a double tap."""
    make_request, calls = _refusing("Bad Request: message is not modified")
    method = EditMessageText(chat_id=1, message_id=2,
                             text=f"{texts.custom_emoji('42', '🚚')} Ваші")

    with pytest.raises(TelegramBadRequest):
        asyncio.run(DropCustomEmoji()(make_request, None, method))
    assert len(calls) == 1, "a double tap must not be retried without the logos"


def test_a_call_with_no_logos_is_never_retried():
    """What keeps the wider net safe: nothing that has no custom emoji in it is
    tried a second time, whatever Telegram said."""
    make_request, calls = _refusing("Bad Request: chat not found")
    method = SendMessage(chat_id=1, text="звичайний текст")

    with pytest.raises(TelegramBadRequest):
        asyncio.run(DropCustomEmoji()(make_request, None, method))
    assert len(calls) == 1


def test_when_dropping_the_logos_does_not_help_the_first_refusal_is_raised():
    """The logos were not the problem, so the first refusal is the true
    diagnosis. Raising the second would describe the retry instead of the
    failure, and the retry is ours rather than the customer's."""
    make_request, calls = _refusing("Bad Request: DOCUMENT_INVALID",
                                    then="Bad Request: chat not found")

    with pytest.raises(TelegramBadRequest) as raised:
        asyncio.run(DropCustomEmoji()(make_request, None, _carrying_a_logo()))

    assert "DOCUMENT_INVALID" in raised.value.message
    assert len(calls) == 2
