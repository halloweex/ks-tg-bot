"""GET /internal/activity: the counts the analytics service next door charts.

Not a door from outside, and the tests are mostly about keeping it that way:
nothing without the token, nothing that came through nginx even with it, and
no route at all when the token is not configured.
"""
from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from bot.webhooks import (INTERNAL_ACTIVITY, MAX_ACTIVITY_DAYS,
                          MIN_ACTIVITY_DAYS, build_app)

TOKEN = "f" * 64
GOOD = {"Authorization": f"Bearer {TOKEN}"}


class _Unused:
    """Rivo's collaborators, which none of these requests reach."""

    async def chat_for(self, email):
        raise AssertionError("not a Rivo request")

    chosen_by = queue = chat_for


def _app(*, token: str | None = TOKEN, fails: bool = False, asked: list | None = None):
    async def activity(days: int) -> dict:
        if asked is not None:
            asked.append(days)
        if fails:
            raise RuntimeError("database is locked")
        return {"version": 1, "days": days, "wau": 3}

    unused = _Unused()
    return build_app(chats=unused, languages=unused, queue=unused,
                     activity=activity, internal_token=token)


def _get(app, path: str = INTERNAL_ACTIVITY, headers: dict | None = None):
    async def go():
        async with TestClient(TestServer(app)) as client:
            response = await client.get(path, headers=headers or {})
            body = await response.text()
            return response.status, body
    return asyncio.run(go())


def test_with_the_token_it_answers_counts_as_json():
    asked: list[int] = []
    status, body = _get(_app(asked=asked), f"{INTERNAL_ACTIVITY}?days=30", GOOD)
    assert status == 200
    assert '"wau": 3' in body
    assert asked == [30]


def test_without_days_it_answers_the_default_window():
    asked: list[int] = []
    assert _get(_app(asked=asked), headers=GOOD)[0] == 200
    assert MIN_ACTIVITY_DAYS <= asked[0] <= MAX_ACTIVITY_DAYS


def test_no_token_or_a_wrong_one_is_refused():
    assert _get(_app())[0] == 401
    assert _get(_app(), headers={"Authorization": "Bearer nope"})[0] == 401
    assert _get(_app(), headers={"Authorization": TOKEN})[0] == 401


def test_a_header_that_is_not_ascii_is_a_401_and_not_a_crash():
    """compare_digest on str raises TypeError for non-ASCII input."""
    assert _get(_app(), headers={"Authorization": "Bearer токен"})[0] == 401


def test_anything_that_came_through_nginx_does_not_find_the_route():
    """Nginx sets both headers on everything it forwards. With them, even the
    right token gets the same 404 as a path that does not exist."""
    for header in ("X-Real-IP", "X-Forwarded-For"):
        status, _ = _get(_app(), headers={**GOOD, header: "203.0.113.7"})
        assert status == 404, header


def test_no_token_configured_means_no_route():
    assert _get(_app(token=None), headers=GOOD)[0] == 404
    assert _get(_app(token=""), headers=GOOD)[0] == 404


def test_a_window_that_is_not_a_number_or_out_of_range_is_a_400():
    for days in ("x", "1.5", str(MIN_ACTIVITY_DAYS - 1), str(MAX_ACTIVITY_DAYS + 1)):
        status, _ = _get(_app(), f"{INTERNAL_ACTIVITY}?days={days}", GOOD)
        assert status == 400, days


def test_a_failing_report_is_a_500_that_says_nothing_about_why():
    status, body = _get(_app(fails=True), headers=GOOD)
    assert status == 500
    assert "locked" not in body


def test_without_rivo_configured_the_server_still_has_health_and_no_rivo_route():
    assert _get(_app(), "/health")[0] == 200
    assert _get(_app(), "/rivo/anything")[0] == 404
