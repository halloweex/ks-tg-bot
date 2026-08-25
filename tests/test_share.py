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


CARD = "https://halloweex.github.io/ks-tg-bot/invite.jpg"


def _config(invite_card_url: str = CARD,
            first_order_reward: str = "Знижка 10% на перше замовлення",
            first_order_code: str = "FIRST10"):
    return SimpleNamespace(website_url=SHOP, brand_name="Korean Story",
                           bot_username="koreanstory_bot", support_chat_id=-1,
                           invite_card_url=invite_card_url,
                           first_order_reward=first_order_reward,
                           first_order_code=first_order_code)


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


# --- inviting the bot itself, and being paid for it -------------------------

def test_the_same_button_without_a_sku_invites_the_bot(db):
    """The referral programme is about the bot travelling, not a cream: a
    friend who opens it and orders is what earns the reward."""
    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config()))
    [row] = query.results
    assert row.title == "Korean Story"
    link = row.reply_markup.inline_keyboard[0][0].url
    assert link == f"https://t.me/koreanstory_bot?start={REFERRAL_PREFIX}{CHAT}"
    assert query.kwargs["is_personal"] is True


def test_the_invitation_arrives_as_the_shops_own_card(db):
    """An invitation in the brand's colours is a different thing from a link —
    and it arrives as the preview above the text rather than as a photo result,
    because a photo result turns the panel into unlabelled thumbnails that the
    first person to use this could not tell were tappable."""
    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config()))
    row = query.results[0]
    assert row.description == T.MSG_INVITE_ROW, "the row says what it does"
    preview = row.input_message_content.link_preview_options
    assert preview.url == CARD and preview.prefer_large_media
    assert "Korean Story" in row.input_message_content.message_text
    assert str(CHAT) not in row.input_message_content.message_text


def test_without_a_published_card_the_invitation_is_still_sent(db):
    """The image is published on its own schedule, beside the Mini App page."""
    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config(invite_card_url="")))
    row = query.results[0]
    assert "Korean Story" in row.input_message_content.message_text
    assert row.input_message_content.link_preview_options.is_disabled
    assert row.reply_markup.inline_keyboard[0][0].url.endswith(str(CHAT))


# --- when a referral is earned ----------------------------------------------

def _friend_who(source: str, *, ordered: bool, cancelled: bool = False,
                chat_id: int = FRIEND) -> None:
    import json

    from core.repos.orders import upsert_orders

    asyncio.run(save_user(chat_id, f"+38067000{chat_id:04d}", source=source))
    if ordered:
        asyncio.run(upsert_orders(chat_id, [{
            "chat_id": chat_id, "source": "keycrm", "source_order_id": "1",
            "external_id": "1", "order_name": "", "status_name": "completed",
            "status_group_id": 6 if cancelled else 1, "grand_total": 100.0,
            "currency": "грн", "ordered_at": "2026-08-01T10:00:00",
            "products_json": json.dumps([{"name": "A", "qty": 1, "sku": "1"}]),
            "buyer_name": "", "payment_status": "", "tracking_code": "",
            "shipping_status": "", "delivery_city": "", "receive_point": "",
            "recipient_name": "",
        }]))


def _sweep():
    from core.usecases.referrals import check_once

    return asyncio.run(check_once(REFERRAL_PREFIX))


def _queued_rewards() -> list[dict]:
    import json

    from core.repos.outbox import claim

    return [{**row, "payload": json.loads(row["payload"])}
            for row in asyncio.run(claim(50)) if row["type"] == "referral"]


def test_a_friend_who_only_opened_the_bot_earns_nothing(db):
    """Opening a bot costs nothing, and a programme that pays for that pays for
    nothing. The owner's rule: the first order is what counts."""
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=False)
    assert _sweep().earned == 0
    assert _queued_rewards() == []


def test_a_friend_who_ordered_earns_the_reward(db):
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True)
    assert _sweep().earned == 1
    [message] = _queued_rewards()
    assert message["chat_id"] == CHAT, "the reward goes to whoever brought her"
    assert "промокод" in message["payload"]["text"]


def test_a_cancelled_order_is_not_an_order(db):
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True, cancelled=True)
    assert _sweep().earned == 0


def test_a_referral_is_paid_for_once(db):
    """The sweep runs every quarter of an hour; the row is what stops the
    second run owing it again."""
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True)
    assert _sweep().earned == 1
    assert _sweep().earned == 0
    assert len(_queued_rewards()) == 1


def test_nobody_earns_a_reward_for_bringing_themselves(db):
    _friend_who(f"{REFERRAL_PREFIX}{FRIEND}", ordered=True)
    assert _sweep().earned == 0


def test_a_customer_who_came_alone_earns_nobody_anything(db):
    _friend_who("", ordered=True)
    assert _sweep().earned == 0


def test_the_screen_counts_both_states(db):
    """Somebody whose friend has opened the bot but not ordered would read a
    single zero as "it does not work"."""
    from core.repos.referrals import referral_counts

    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True, chat_id=7071)
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=False, chat_id=7072)
    _sweep()
    assert asyncio.run(referral_counts(CHAT, REFERRAL_PREFIX)) == (2, 1)


def test_the_invitation_says_the_offer_in_words_too(db):
    """A preview can fail to load, and an invitation that then says nothing
    about the discount is an invitation that lost its point."""
    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config()))
    text = query.results[0].input_message_content.message_text
    assert "10%" in text


def test_no_reward_no_line(db):
    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config(first_order_reward="")))
    assert "10%" not in query.results[0].input_message_content.message_text


# --- what the reward actually is --------------------------------------------

def _sweep_with(code: str = "", reward: str = "Знижка 10%", link: str = ""):
    from core.usecases.referrals import check_once

    return asyncio.run(check_once(REFERRAL_PREFIX, code=code, reward=reward,
                                  discount_link=link))


def test_with_a_code_the_reward_arrives_finished(db):
    """The customer is told and paid in the same message: the code is in it,
    and the button applies it."""
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True)
    _sweep_with(code="REF10", link=f"{SHOP}/discount/REF10")
    payload = _queued_rewards()[0]["payload"]
    assert "REF10" in payload["text"]
    button = payload["keyboard"]["inline_keyboard"][0][0]
    assert button["url"] == f"{SHOP}/discount/REF10"


def test_without_a_code_the_message_says_what_really_happens(db):
    """A bot promising a code it cannot send is a promise that quietly stops
    being kept. Until the shop creates one, a person writes it."""
    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True)
    _sweep_with()
    payload = _queued_rewards()[0]["payload"]
    assert "Менеджер надішле" in payload["text"]
    assert "keyboard" not in payload


def test_the_code_that_paid_is_recorded(db):
    """What was handed over is the thing anybody auditing this asks about."""
    import aiosqlite

    from core.repos.base import connect

    _friend_who(f"{REFERRAL_PREFIX}{CHAT}", ordered=True)
    _sweep_with(code="REF10", link=f"{SHOP}/discount/REF10")

    async def read():
        async with connect() as db_:
            db_.row_factory = aiosqlite.Row
            cursor = await db_.execute(
                "SELECT code FROM referrals WHERE friend_chat_id = ?", (FRIEND,))
            return (await cursor.fetchone())["code"]

    assert asyncio.run(read()) == "REF10"


def test_the_invitation_button_says_what_is_waiting(db):
    """The button leads into the bot either way — that is how the referral is
    attributed — so the label follows whether the discount is live."""
    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config()))
    assert query.results[0].reply_markup.inline_keyboard[0][0].text == T.BTN_FIRST_ORDER

    query = _Query("поділитися")
    asyncio.run(inline_list(query, T, _config(first_order_code="")))
    assert query.results[0].reply_markup.inline_keyboard[0][0].text == T.BTN_INVITE_OPEN
