"""Asking about a discount, and what "already asked" is allowed to mean.

The rule used to be one ask a week per customer: a question about a cream also
refused every other product for seven days, and the pop-up said the request was
in hand for all seven whether or not a manager had ever opened it. Both halves
were wrong in the same way — the bot was answering about something it had not
recorded.

Now a request is scoped to the product and closed by an answer, and the two
things the customer is told are things the table actually knows.
"""
from __future__ import annotations

import asyncio

import pytest

from core.repos import base as repos_base
from core.repos import support as repo
from core.repos.schema import _CREATE_ORDERS, init_db

CUSTOMER = 4242
CREAM, SHAMPOO = "SKU-CREAM", "SKU-SHAMPOO"
WHOLE_LIST = ""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())


def _ask(sku: str, message_id: int = 0) -> None:
    asyncio.run(repo.add_discount_request(
        CUSTOMER, "[]", sku=sku, thread_message_id=message_id))


def _pending(sku: str) -> bool:
    return asyncio.run(repo.pending_discount_request(CUSTOMER, sku))


def test_one_product_does_not_silence_the_others(db):
    """The whole point: a cream and a shampoo are two questions."""
    _ask(CREAM)
    assert _pending(CREAM)
    assert not _pending(SHAMPOO), "asking about one product refused all the rest"


def test_the_same_product_twice_is_refused(db):
    _ask(CREAM)
    assert _pending(CREAM)


def test_the_whole_list_is_its_own_question(db):
    """From the screen the ask covers the top five; from a card it is one item.
    Neither should stand in for the other."""
    _ask(WHOLE_LIST)
    assert _pending(WHOLE_LIST)
    assert not _pending(CREAM)


def test_an_answer_reopens_the_question_at_once(db):
    """A reply in the support chat closes the ask by the id it replied to, so
    the customer can come back the same minute if they need to."""
    _ask(CREAM, message_id=777)
    assert _pending(CREAM)

    asyncio.run(repo.mark_discount_answered(777))
    assert not _pending(CREAM), "answered, and still refusing to ask again"


def test_a_reply_to_something_else_closes_nothing(db):
    _ask(CREAM, message_id=777)
    asyncio.run(repo.mark_discount_answered(778))
    assert _pending(CREAM)


def test_an_unanswered_ask_expires_with_the_window(db):
    """Seven days of silence is its own answer: the customer may ask again."""
    _ask(CREAM, message_id=1)
    asyncio.run(_age(days=8))
    assert not _pending(CREAM)


async def _age(days: int) -> None:
    async with repos_base.connect() as conn:
        await conn.execute(
            "UPDATE discount_requests "
            "   SET created_at = datetime('now', ?)", (f"-{days} days",))
        await conn.commit()


def test_a_long_list_cannot_become_a_long_queue(db):
    """Per-product asks removed the old blanket limit, so something has to stop
    a thumb walking down fifty rows."""
    for n in range(repo.PENDING_LIMIT):
        _ask(f"SKU-{n}", message_id=100 + n)
    assert asyncio.run(repo.pending_discount_count(CUSTOMER)) == repo.PENDING_LIMIT

    # One answer frees exactly one slot, not the queue.
    asyncio.run(repo.mark_discount_answered(100))
    assert asyncio.run(repo.pending_discount_count(CUSTOMER)) == repo.PENDING_LIMIT - 1
    assert not _pending("SKU-0")
    assert _pending("SKU-1")


def test_a_live_database_gets_the_new_columns(tmp_path, monkeypatch):
    """The columns arrive by migration, not by _LATE_COLUMNS alone.

    A database already stamped at the current version never re-runs the
    late-column pass, so production would have kept the old three-column table
    and every ask would have failed on "no such column: sku".
    """
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "old.db"))

    async def build_the_old_shape() -> None:
        async with repos_base.connect() as conn:
            await conn.execute(
                "CREATE TABLE discount_requests ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " chat_id INTEGER NOT NULL,"
                " products_json TEXT NOT NULL DEFAULT '[]',"
                " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            # The real shape: init_db decides "this database is not new" by
            # the presence of `orders`, and indexes it on the way out.
            await conn.execute(_CREATE_ORDERS)
            await conn.execute("PRAGMA user_version = 15")
            await conn.commit()

    async def columns() -> set[str]:
        async with repos_base.connect() as conn:
            cursor = await conn.execute("PRAGMA table_info(discount_requests)")
            return {row[1] for row in await cursor.fetchall()}

    asyncio.run(build_the_old_shape())
    assert "sku" not in asyncio.run(columns())

    asyncio.run(init_db())
    assert {"sku", "thread_message_id", "answered_at"} <= asyncio.run(columns())
