"""The delivery screen, which had no tests and changed yesterday.

Fixing §2 turned "Nova Poshta answered about a number it does not know" from an
answer into a non-answer, and the screen falls back to the CRM when there is no
answer. That fallback is the whole point of the fix, and until now it was only
covered one layer down, in the adapter. These cases pin it where the customer
actually sees it.

No Telegram: the screen takes an anchor message only to show "typing…", and the
four database calls are replaced. What is left is the branching, which is what
broke in §13 on the neighbouring screen.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bot.handlers import delivery as screen
from core.adapters.novaposhta.parse import TrackingStatus
from core.i18n import customer_texts

CHAT = 555
PHONE = "+380670000000"
TTN = "20400000000000"

T = customer_texts("uk")


def _order(**kw) -> dict:
    """A cached order as the repository hands it over."""
    row = {
        "order_name": "#19966",
        "source": "keycrm",
        "products_json": json.dumps([{"name": "Крем", "qty": 1}], ensure_ascii=False),
        "tracking_code": TTN,
        # What the CRM itself knows — the part an unknown TTN used to displace.
        "shipping_status": "delivered",
        "delivery_city": "Київ",
        "receive_point": "Відділення №12",
    }
    row.update(kw)
    return row


class FakeNovaPoshta:
    """Answers with whatever it was given, and remembers what it was asked."""

    def __init__(self, statuses: dict | None = None) -> None:
        self.asked: list[tuple] = []
        self._statuses = statuses or {}

    async def track_many(self, ttns: list[str], phone: str = "") -> dict:
        self.asked.append((tuple(ttns), phone))
        return dict(self._statuses)


@pytest.fixture()
def wired(monkeypatch):
    """Everything around the screen replaced; the branching is what is left."""
    state = {"phone": PHONE, "tracked": [], "all": []}

    async def fake_phone(chat_id):
        return state["phone"]

    async def fake_tracked(chat_id):
        return list(state["tracked"])

    async def fake_all(chat_id):
        return list(state["all"])

    async def fake_typing(anchor):
        pass

    monkeypatch.setattr(screen, "get_user_phone", fake_phone)
    monkeypatch.setattr(screen, "get_orders_with_tracking", fake_tracked)
    monkeypatch.setattr(screen, "get_cached_orders", fake_all)
    monkeypatch.setattr(screen, "typing", fake_typing)
    monkeypatch.setattr(screen, "track", lambda *a, **kw: None)
    return state


def _render(novaposhta=None) -> str:
    text, markup = asyncio.run(screen.delivery_screen(CHAT, T, novaposhta, anchor=object()))
    assert markup is None, "the delivery screen carries no keyboard by design"
    return text


# --- what the customer sees when there is nothing to show ------------------

def test_a_user_without_a_phone_is_told_so(wired):
    wired["phone"] = None
    assert _render() == T.ERR_PHONE_NOT_FOUND


def test_orders_but_none_shipped_is_different_from_no_orders(wired):
    """Two different sentences on purpose: "nothing has shipped yet" and
    "nothing has been ordered yet" are not the same news."""
    wired["all"] = [_order(tracking_code="")]
    assert _render() == T.MSG_DELIVERY_NO_TRACKING

    wired["all"] = []
    assert _render() == T.MSG_NO_DELIVERIES


# --- the §2 fix, seen from the screen --------------------------------------

def test_a_number_the_carrier_does_not_know_falls_back_to_the_crm(wired):
    """The fix: an unknown TTN produces no tracking status, and the screen shows
    what the CRM knows instead of displacing it with "Номер не знайдено"."""
    wired["tracked"] = [_order()]
    text = _render(FakeNovaPoshta(statuses={}))       # nothing came back
    assert "Доставлено" in text
    assert "Київ, Відділення №12" in text


def test_a_real_status_wins_over_the_crm(wired):
    """The other side of the same branch: when the carrier does answer, its
    answer is fresher than the CRM's and replaces it."""
    wired["tracked"] = [_order()]
    status = TrackingStatus(
        ttn=TTN, status="Прибув у відділення", status_code=7,
        city_recipient="Львів", warehouse_recipient="Відділення №5",
        scheduled_delivery="", actual_delivery="", date_created="",
    )
    text = _render(FakeNovaPoshta(statuses={TTN: status}))
    assert "Прибув у відділення" in text
    assert "Відділення №5" in text
    assert "Відділення №12" not in text, "the CRM branch must not run as well"


def test_without_a_carrier_client_the_screen_still_works(wired):
    """Nova Poshta keys are optional: the entry point passes None when there are
    none, and the screen degrades to the CRM rather than failing."""
    wired["tracked"] = [_order()]
    text = _render(None)
    assert "Доставлено" in text
    assert TTN in text


# --- wiring ----------------------------------------------------------------

def test_the_carrier_is_asked_about_every_parcel_once(wired):
    wired["tracked"] = [_order(tracking_code="20400000000001"),
                        _order(tracking_code="20400000000002")]
    novaposhta = FakeNovaPoshta()
    _render(novaposhta)
    assert len(novaposhta.asked) == 1, "one batched call, not one per parcel"
    ttns, phone = novaposhta.asked[0]
    assert set(ttns) == {"20400000000001", "20400000000002"}
    assert phone == PHONE


def test_a_long_list_is_truncated_rather_than_rejected_by_telegram(wired):
    """Telegram refuses a message over 4096 characters, and refusing it means
    the customer sees nothing at all."""
    wired["tracked"] = [_order(order_name=f"#{i}") for i in range(60)]
    text = _render(FakeNovaPoshta())
    assert len(text) < 4096
    assert T.MSG_DELIVERIES_TRUNCATED in text
