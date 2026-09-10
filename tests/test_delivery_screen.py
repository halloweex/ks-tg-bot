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


def _status(**kw) -> TrackingStatus:
    """A carrier answer, with every date empty unless the test sets one."""
    fields = {
        "ttn": TTN, "status": "Прибув у відділення", "status_code": 7,
        "city_recipient": "Львів", "warehouse_recipient": "Відділення №5",
        "scheduled_delivery": "", "actual_delivery": "", "recipient_date": "",
        "date_created": "",
    }
    fields.update(kw)
    return TrackingStatus(**fields)


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
    # One button, and only one: the way back to the menu. The screen used to
    # carry none, from when the menu was always under the input field.
    assert [b.text for row in markup.inline_keyboard for b in row] == [T.BTN_MENU]
    return text


# --- what the customer sees when there is nothing to show ------------------

def test_a_user_without_a_phone_is_told_so(wired):
    """And told the right thing: the string it used to borrow said "we found
    no orders for this number", about a number we do not have."""
    wired["phone"] = None
    assert _render() == T.MSG_NO_PHONE_YET


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
    status = _status()
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


# --- which date is "you have it" -------------------------------------------
#
# Nova Poshta reports the van arriving and the customer collecting as two
# different moments, and on the parcel that prompted this they were a day and
# three hours apart. The screen used to print the first one under "Отримано".


def test_received_shows_when_she_collected_it_not_when_it_arrived(wired):
    """RecipientDateTime is the handover; ActualDeliveryDate is the van."""
    wired["tracked"] = [_order()]
    text = _render(FakeNovaPoshta(statuses={TTN: _status(
        status="Відправлення отримано", status_code=9,
        actual_delivery="2026-07-29 08:46:10",
        recipient_date="30.07.2026 11:42:06",
    )}))
    assert "Отримано: 30.07.2026" in text
    assert "29.07.2026" not in text, "the arrival date must not be called отримано"


def test_a_parcel_at_the_branch_is_arrived_not_received(wired):
    """Nothing collected yet: say it arrived, do not claim she has it."""
    wired["tracked"] = [_order()]
    text = _render(FakeNovaPoshta(statuses={TTN: _status(
        actual_delivery="2026-07-29 08:46:10")}))
    assert "Прибуло: 29.07.2026" in text
    assert "Отримано" not in text


def test_a_parcel_still_travelling_shows_the_promised_date(wired):
    """Neither moment has happened, so the estimate is all there is."""
    wired["tracked"] = [_order()]
    text = _render(FakeNovaPoshta(statuses={TTN: _status(
        status="В дорозі", status_code=5,
        scheduled_delivery="29-07-2026 18:00:00")}))
    assert "29.07.2026" in text
    assert "Отримано" not in text
    assert "Прибуло" not in text


def test_a_recorded_delivered_parcel_reaches_the_screen_saying_the_right_day():
    """The whole path on one real recording: response → parse_tracking → lines.

    Every other test here builds TrackingStatus by hand and so proves nothing
    about the mapping. A review caught that by mutation — renaming the key the
    parser reads left the entire suite green — and this is the case that fails
    when it happens, because it starts where the carrier's answer starts.
    """
    import json
    import pathlib

    from core.adapters.novaposhta.parse import parse_tracking, tracking_document

    body = json.loads((pathlib.Path(__file__).parent / "fixtures" / "novaposhta"
                       / "tracking_delivered.json").read_text())
    status = parse_tracking(TTN, tracking_document(body))

    lines = screen.parcel_lines(_order(), status, T)
    text = "\n".join(lines)

    assert "Отримано: 30.07.2026" in text, "the day she collected it"
    assert "29.07" not in text, "the day the van arrived is not отримано"


def test_a_rich_block_gets_the_text_unescaped():
    """A block's text is structured, not parsed. Escaped, a branch on
    вул. В'ячеслава arrives as "В&#x27;ячеслава" and is shown with the entity
    spelled out, because nothing on that path unescapes it."""
    status = _status(status="Відправлення отримано",
                     warehouse_recipient="Відділення №20: вул. В'ячеслава Зайцева")
    html_lines = screen.parcel_lines({"tracking_code": TTN}, status, T)
    block_lines = screen.parcel_lines({"tracking_code": TTN}, status, T,
                                      as_html=False)
    assert any("&#x27;" in line for line in html_lines), "the HTML screen escapes"
    assert not any("&#x27;" in line for line in block_lines)
    assert any("В'ячеслава" in line for line in block_lines)
