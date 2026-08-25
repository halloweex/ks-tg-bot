"""Recommending a product to a friend, and counting who that brought.

Two halves that never touch each other. The sharing half answers an inline
query in **somebody else's chat**, which is why almost every test here is about
what it does *not* say: no history, no name, no number, nothing that is not
already on the shop's own shelf.

The receiving half is a deep link and one column. `users.source` answers "who
brought this customer" and has to keep answering it — written once, at
registration, and never overwritten by a later visit through another link.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from bot.handlers.common import REFERRAL_PREFIX
from bot.handlers.inline import FAVOURITES, ORDERS, SHARE, _card_kb, _route, inline_list
from core.domain.offer import Offer
from core.i18n import Texts
from core.repos import base as repos_base
from core.repos.catalogue import save_offers
from core.repos.schema import init_db
from core.repos.users import get_source, save_user

CHAT = 6060
FRIEND = 7070
SHOP = "https://koreanstory.com.ua"
T = Texts("uk")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


@pytest.fixture(autouse=True)
def no_tracking(monkeypatch):
    events = []
    monkeypatch.setattr("bot.handlers.inline.track",
                        lambda chat_id, event, **meta: events.append((event, meta)))
    return events


class _Query:
    """As much of an InlineQuery as the handler touches."""

    def __init__(self, text: str, *, chat_type: str = "group",
                 user_id: int = CHAT) -> None:
        self.query = text
        self.chat_type = chat_type
        self.from_user = SimpleNamespace(id=user_id)
        self.results = None
        self.kwargs = None

    async def answer(self, results, **kwargs):
        self.results = results
        self.kwargs = kwargs


def _config():
    return SimpleNamespace(website_url=SHOP, brand_name="Korean Story",
                           bot_username="koreanstory_bot", support_chat_id=-1)


def _offer(sku="1", *, available=True, title="Крем для обличчя"):
    return Offer(sku=sku, variant_id=99, handle="h", title=title, price="680.00",
                 available=available, image_url="https://cdn/x.jpg")


def _share(sku: str = "1", *, chat_type: str = "group") -> _Query:
    query = _Query(f"поділитися {sku}", chat_type=chat_type)
    asyncio.run(inline_list(query, T, _config()))
    return query


# --- which mode a query is --------------------------------------------------

def test_the_first_word_chooses_the_mode():
    assert _route("") == (FAVOURITES, "")
    assert _route("крем") == (FAVOURITES, "крем")
    assert _route("замовлення 199") == (ORDERS, "199")
    assert _route("поділитися 1729") == (SHARE, "1729")
    assert _route("share 1729") == (SHARE, "1729")


def test_the_sku_keeps_its_case():
    """A sku is an identifier, not a search: lowering it would stop matching."""
    assert _route("поділитися AB-12")[1] == "AB-12"


# --- what a friend receives -------------------------------------------------

def test_a_shared_card_is_answered_in_somebody_elses_chat(db):
    """The whole point. The two personal lists refuse this and say so; this one
    is the shop's own shelf and belongs anywhere."""
    asyncio.run(save_offers({"1": _offer()}))
    for chat_type in ("group", "supergroup", "private", "sender", None):
        query = _share(chat_type=chat_type)
        assert len(query.results) == 1, chat_type


def test_a_shared_card_says_nothing_about_who_shared_it(db):
    """It is a recommendation because of who sends it, not because of what it
    says. Everything in it comes from the catalogue."""
    asyncio.run(save_offers({"1": _offer()}))
    row = _share().results[0]
    card = row.input_message_content.message_text
    assert "Крем для обличчя" in card
    assert "680" in card and "Korean Story" in card
    assert str(CHAT) not in card, "the sharer is not named in the text"


def test_no_history_is_read_for_a_share(db):
    """Not even a registered customer: the query is answered from the offers
    table, so a share works before anybody has verified a number."""
    asyncio.run(save_offers({"1": _offer()}))
    assert _share().results, "no user row exists at all in this test"


def test_the_card_carries_the_two_things_a_friend_can_want(db):
    asyncio.run(save_offers({"1": _offer()}))
    buy, about = (row[0] for row in _share().results[0].reply_markup.inline_keyboard)
    assert urlparse(buy.url).path == "/cart/99:1"
    assert parse_qs(urlparse(buy.url).query)["utm_campaign"] == ["referral"]
    assert about.url == f"https://t.me/koreanstory_bot?start={REFERRAL_PREFIX}{CHAT}"


def test_the_answer_is_cached_per_person_and_not_across_them(db):
    """The query text is the same for everybody sharing this product; the card
    is not, because the deep link in it carries the sharer's own id. Cached
    across users, the second person to share a cream would hand out the first
    one's referral link."""
    asyncio.run(save_offers({"1": _offer()}))
    query = _share()
    assert query.kwargs["is_personal"] is True
    assert query.kwargs["cache_time"] > 0

    mine = _share().results[0].reply_markup.inline_keyboard[1][0].url
    theirs_query = _Query("поділитися 1", user_id=FRIEND)
    asyncio.run(inline_list(theirs_query, T, _config()))
    theirs = theirs_query.results[0].reply_markup.inline_keyboard[1][0].url
    assert mine != theirs, "each sharer's card carries their own link"


def test_a_product_that_cannot_be_bought_is_not_recommended(db):
    """It sold out between the button being drawn and being pressed. A
    recommendation ending on a sold-out page is a favour nobody asked for."""
    asyncio.run(save_offers({"1": _offer(available=False)}))
    query = _share()
    assert query.results == []
    assert query.kwargs["button"].text == T.MSG_INLINE_NOTHING_FOUND


def test_a_sku_the_shop_never_sold_is_not_recommended(db):
    query = _share("nonsense")
    assert query.results == []


# --- the button that starts it ----------------------------------------------

def test_a_buyable_card_offers_to_recommend_it():
    keyboard = _card_kb("1", _offer(), T, SHOP, waiting=False, out_of_stock=False)
    share = next(b for row in keyboard.inline_keyboard for b in row
                 if b.switch_inline_query_chosen_chat is not None)
    chosen = share.switch_inline_query_chosen_chat
    assert chosen.query == "поділитися 1"
    assert chosen.allow_user_chats and chosen.allow_group_chats
    assert not chosen.allow_channel_chats, "a channel is not a friend"


def test_a_sold_out_card_does_not_offer_to_recommend_it():
    keyboard = _card_kb("1", _offer(available=False), T, SHOP, waiting=False,
                        out_of_stock=True)
    assert not any(b.switch_inline_query_chosen_chat
                   for row in keyboard.inline_keyboard for b in row)


# --- who it brought ---------------------------------------------------------

def test_the_link_that_brought_a_customer_is_written_once(db):
    """First touch. A later visit through somebody else's link does not
    rewrite who brought them, and a re-verified phone does not erase it."""
    asyncio.run(save_user(FRIEND, "+380670000001", source=f"{REFERRAL_PREFIX}{CHAT}"))
    assert asyncio.run(get_source(FRIEND)) == f"{REFERRAL_PREFIX}{CHAT}"

    asyncio.run(save_user(FRIEND, "+380670000001", source=f"{REFERRAL_PREFIX}999"))
    assert asyncio.run(get_source(FRIEND)) == f"{REFERRAL_PREFIX}{CHAT}"

    asyncio.run(save_user(FRIEND, "+380670000002"))
    assert asyncio.run(get_source(FRIEND)) == f"{REFERRAL_PREFIX}{CHAT}"


def test_somebody_who_found_the_bot_alone_has_no_source(db):
    asyncio.run(save_user(FRIEND, "+380670000001"))
    assert asyncio.run(get_source(FRIEND)) == ""


def test_re_registration_keeps_what_the_bot_already_knew(db):
    """REPLACE writes a whole row: everything not carried over by hand is
    reset. The birthday was the newest thing at risk."""
    from core.repos.users import chats_without_birthday, save_birthday

    asyncio.run(save_user(FRIEND, "+380670000001"))
    asyncio.run(save_birthday(FRIEND, "03-14"))
    asyncio.run(save_user(FRIEND, "+380670000002"))
    assert asyncio.run(chats_without_birthday(10)) == [], "still asked about"
