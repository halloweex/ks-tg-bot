"""The discount a new customer gets, and the two ways it stays honest.

Shopify applies a code from a link — `/discount/CODE?redirect=…` — which is the
whole reason this works without an Admin token: a person creates the code once
in the shop's admin, and the bot hands out the link. So the two things worth
testing are that the link is right, and that nothing is offered when there is
no code to offer.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from bot.handlers.orders import (_favourites_view, _no_orders_kb, first_order_kb,
                                 first_order_offer)
from bot.keyboards import discount_url
from core.i18n import Texts
from core.repos import base as repos_base
from core.repos.orders import upsert_orders
from core.repos.schema import init_db
from core.repos.users import save_user

CHAT = 8080
SHOP = "https://koreanstory.com.ua"
T = Texts("uk")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _config(code: str = "FIRST10", reward: str = "Знижка 10% на перше замовлення"):
    return SimpleNamespace(website_url=SHOP, first_order_code=code,
                           first_order_reward=reward)


def _labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


# --- the link ---------------------------------------------------------------

def test_the_link_applies_the_code_and_lands_in_the_shop():
    url = discount_url(SHOP, "FIRST10")
    assert urlparse(url).path == "/discount/FIRST10"
    redirect = unquote(parse_qs(urlparse(url).query)["redirect"][0])
    assert redirect.startswith("/?")
    assert "utm_campaign=first_order" in redirect


def test_a_code_with_awkward_characters_survives_the_url():
    assert "/discount/10%25%20OFF" in discount_url(SHOP, "10% OFF")


# --- when it is offered -----------------------------------------------------

def test_a_customer_with_no_orders_is_offered_it(db):
    asyncio.run(save_user(CHAT, "+380670000000"))
    text, markup, _ = asyncio.run(
        _favourites_view(CHAT, T, [], SHOP, _config()))
    assert "перше замовлення" in text
    assert T.BTN_FIRST_ORDER in _labels(markup)


def test_a_customer_who_has_bought_before_is_not(db):
    """It is a first-order discount. Somebody with a history has had theirs."""
    asyncio.run(save_user(CHAT, "+380670000000"))
    order = {
        "chat_id": CHAT, "source": "keycrm", "source_order_id": "1",
        "external_id": "1", "order_name": "", "status_name": "completed",
        "status_group_id": 1, "grand_total": 100.0, "currency": "грн",
        "ordered_at": "2026-08-01T10:00:00",
        "products_json": json.dumps([{"name": "A", "qty": 1, "sku": "1"}]),
        "buyer_name": "", "payment_status": "", "tracking_code": "",
        "shipping_status": "", "delivery_city": "", "receive_point": "",
        "recipient_name": "",
    }
    asyncio.run(upsert_orders(CHAT, [order]))
    cached = [order]
    text, markup, _ = asyncio.run(
        _favourites_view(CHAT, T, cached, SHOP, _config()))
    assert "перше замовлення" not in text
    assert T.BTN_FIRST_ORDER not in _labels(markup)


# --- when it is not ---------------------------------------------------------

def test_no_code_means_the_manager_hands_it_over():
    """The invitation promises this discount whether or not a code exists, so
    the arrival honours it either way — with a link when there is a code, with
    a person when there is not. What must never appear is a button leading to a
    code nobody created."""
    empty = _config(code="")
    offer = first_order_offer(T, empty)
    assert "10%" in offer and "менеджеру" in offer
    assert T.BTN_FIRST_ORDER not in _labels(_no_orders_kb(T, empty))
    assert T.BTN_SUPPORT in _labels(first_order_kb(T, empty))


def test_no_wording_means_no_offer_either():
    """The size of the discount is the shop's promise, not the bot's."""
    assert first_order_offer(T, _config(reward="")) == ""


def test_the_offer_names_what_the_shop_promised():
    assert "10%" in first_order_offer(T, _config())
    assert first_order_kb(T, _config()).inline_keyboard[0][0].url.startswith(
        f"{SHOP}/discount/FIRST10")
