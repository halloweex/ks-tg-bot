"""The KeyCRM client's two transport rules: read every page, survive a 429.

Both were defects the module move deliberately left alone — §4 and §5 in
docs/found-during-move.md — and both live in the part of the adapter that needs
a socket, so these go through a mock transport rather than a fixture.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.adapters.keycrm.client import _MAX_ORDER_PAGES, KeyCRMClient

PHONE = "+380670000000"


def _order(order_id: int) -> dict:
    """The few fields the parser reads; the rest of the envelope is the point."""
    return {
        "id": order_id,
        "status": {"name": "delivered"},
        "status_group_id": 1,
        "grand_total": 100,
        "created_at": "2026-07-01T10:00:00.000000Z",
        "products": [],
    }


def _page(page: int, last: int, per_page: int = 2) -> dict:
    start = (page - 1) * per_page
    return {
        "current_page": page,
        "last_page": last,
        "total": last * per_page,
        "per_page": per_page,
        "data": [_order(1000 + start + i) for i in range(per_page)],
    }


@pytest.fixture()
def transport(monkeypatch):
    """Installs a handler and records the requests that reached it."""
    box: dict = {"requests": []}

    def install(handler):
        def wrapped(request: httpx.Request) -> httpx.Response:
            box["requests"].append(request)
            return handler(request)

        real = httpx.AsyncClient
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(wrapped)}),
        )

    # Paging sleeps 0.55s between requests and a retry waits on Retry-After;
    # the tests are about which requests happen, not about wall clock. Bind the
    # real sleep first — a lambda that calls asyncio.sleep would call itself.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _seconds: real_sleep(0))
    box["install"] = install
    box["pages"] = lambda: [
        int(dict(r.url.params).get("page", 1)) for r in box["requests"]
    ]
    return box


def _fetch(phone: str = PHONE):
    return asyncio.run(KeyCRMClient(api_key="k").get_orders_by_phone(phone))


# --- §4: every page, not just the first ------------------------------------

def test_all_pages_are_read(transport):
    """The defect: 50 orders asked for, last_page never read. Ten customers in
    the live CRM have more than 50 orders; the heaviest has 140."""
    transport["install"](
        lambda r: httpx.Response(200, json=_page(int(dict(r.url.params)["page"]), last=3))
    )
    orders = _fetch()
    assert [o.source_order_id for o in orders] == [
        "1000", "1001", "1002", "1003", "1004", "1005",
    ]
    assert transport["pages"]() == [1, 2, 3]


def test_a_single_page_costs_a_single_request(transport):
    """Almost everyone: the median customer is far below one page."""
    transport["install"](lambda r: httpx.Response(200, json=_page(1, last=1)))
    assert len(_fetch()) == 2
    assert transport["pages"]() == [1]


def test_paging_stops_at_the_safety_valve(transport):
    """A wholesale account must not turn one tap into a hundred requests."""
    transport["install"](
        lambda r: httpx.Response(200, json=_page(int(dict(r.url.params)["page"]), last=500))
    )
    orders = _fetch()
    assert transport["pages"]() == list(range(1, _MAX_ORDER_PAGES + 1))
    assert len(orders) == _MAX_ORDER_PAGES * 2


def test_a_failure_halfway_keeps_the_pages_already_read(transport):
    """Orders are upserted, never replaced, so a short read costs freshness and
    nothing else — unlike get_stock, where a partial read would read as a
    restock and is deliberately thrown away."""
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params)["page"])
        if page == 3:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=_page(page, last=5))

    transport["install"](handler)
    orders = _fetch()
    assert [o.source_order_id for o in orders] == ["1000", "1001", "1002", "1003"]


def test_an_empty_result_is_not_an_error(transport):
    transport["install"](
        lambda r: httpx.Response(200, json={"data": [], "last_page": 1, "total": 0})
    )
    assert _fetch() == []


# --- §5: a rate limit is worth waiting out ---------------------------------

def test_a_429_is_retried_and_then_succeeds(transport):
    """Before this, 429 wrote a line to the log and fell into raise_for_status,
    so the only effect of being rate-limited was a customer seeing no orders."""
    state = {"first": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["first"]:
            state["first"] = False
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json=_page(1, last=1))

    transport["install"](handler)
    assert len(_fetch()) == 2
    assert len(transport["requests"]) == 2


def test_a_429_that_never_lets_up_gives_up_after_three_attempts(transport):
    transport["install"](
        lambda r: httpx.Response(429, headers={"Retry-After": "0"}, json={})
    )
    assert _fetch() == []
    assert len(transport["requests"]) == 3


def test_the_retry_survives_a_missing_retry_after(transport):
    """No header is the common case; the wait doubles from a second instead."""
    state = {"seen": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"] += 1
        if state["seen"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json=_page(1, last=1))

    transport["install"](handler)
    assert len(_fetch()) == 2


def test_the_retry_is_only_for_429(transport):
    """A 500 is not a rate limit: retrying it would make a broken CRM three
    times as expensive while the customer waits."""
    transport["install"](lambda r: httpx.Response(500, json={"message": "boom"}))
    assert _fetch() == []
    assert len(transport["requests"]) == 1


def test_the_phone_filter_is_normalised_on_every_page(transport):
    """KeyCRM matches filter[buyer_phone] exactly, so a '+' on page two would
    return somebody else's nothing."""
    transport["install"](
        lambda r: httpx.Response(200, json=_page(int(dict(r.url.params)["page"]), last=2))
    )
    _fetch("+38 (067) 000-00-00")
    for request in transport["requests"]:
        assert dict(request.url.params)["filter[buyer_phone]"] == "380670000000"


# --- §5.2: the changed-orders window ---------------------------------------
#
# The other half of the client, and the opposite failure policy. Everything
# above may come back short; nothing below may, because the caller records a
# success by moving a cursor past the window it just read.

FROM, TO = "2026-08-04 16:00:00", "2026-08-04 16:20:00"


def _sweep(start: str = FROM, end: str = TO):
    return asyncio.run(
        KeyCRMClient(api_key="k").get_orders_changed_between(start, end)
    )


def test_the_window_is_sent_as_the_two_bounds_the_api_accepts(transport):
    """Written this way and no other. The three other spellings tried against
    the live API on 2026-08-03 are rejected with a 400 rather than ignored,
    which is the only reason a typo here would be noticed at all."""
    transport["install"](lambda r: httpx.Response(200, json=_page(1, last=1)))
    _sweep()
    params = dict(transport["requests"][0].url.params)
    assert params["filter[updated_between][from]"] == FROM
    assert params["filter[updated_between][to]"] == TO
    assert params["sort"] == "updated_at"


def test_every_page_of_the_window_is_read(transport):
    transport["install"](
        lambda r: httpx.Response(200, json=_page(int(dict(r.url.params)["page"]), last=4))
    )
    assert len(_sweep()) == 8
    assert transport["pages"]() == [1, 2, 3, 4]


def test_a_failure_halfway_through_the_window_raises(transport):
    """The one behaviour that separates this from get_orders_by_phone. Returning
    the four orders it managed would let the caller mark the window done and
    move the cursor past the ones on page three, which nothing would ever ask
    for again."""
    def handler(request: httpx.Request) -> httpx.Response:
        if int(dict(request.url.params)["page"]) == 3:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=_page(int(dict(request.url.params)["page"]), last=5))

    transport["install"](handler)
    with pytest.raises(httpx.HTTPStatusError):
        _sweep()


def test_a_rate_limit_that_never_lets_up_raises_too(transport):
    """get_orders_by_phone returns [] here, and for a screen that is right."""
    transport["install"](
        lambda r: httpx.Response(429, headers={"Retry-After": "0"}, json={})
    )
    with pytest.raises(httpx.HTTPStatusError):
        _sweep()


def test_a_window_larger_than_the_limit_raises_instead_of_truncating(transport):
    """A wholesale customer's tail is fine to cut; a time window is not. The
    widest window the sync asks for is thirty days, which measured 1,456 orders
    — thirty pages against a limit of four hundred."""
    transport["install"](
        lambda r: httpx.Response(200, json=_page(int(dict(r.url.params)["page"]), last=9999))
    )
    with pytest.raises(RuntimeError, match="over the"):
        _sweep()


def test_an_empty_window_is_not_an_error(transport):
    """Most two-minute windows are empty — 269 orders changed in two days."""
    transport["install"](
        lambda r: httpx.Response(200, json={"data": [], "last_page": 1, "total": 0})
    )
    assert _sweep() == []


def test_the_sweep_asks_for_the_buyer(transport):
    """Without include=buyer there is no phone on the order, and the sweep has
    no way to tell whose it is — it would silently write nothing at all."""
    transport["install"](lambda r: httpx.Response(200, json=_page(1, last=1)))
    _sweep()
    assert "buyer" in dict(transport["requests"][0].url.params)["include"]
