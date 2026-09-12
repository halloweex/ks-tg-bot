"""The broadcast after it stopped having a delivery mechanism of its own.

What used to be `broadcast_targets` plus a driver plus a resume-on-startup is
now rows in the outbox, so these tests are about the two things that had to
survive the swap — the recipient snapshot and the summary — and the one that
had to change: a crash no longer resends, it stops.

Both scenarios take ports since commit 16, and none of them is faked here. The
subject of every test below is what actually landed in two tables, so a fake
queue would be a fake of the thing under test; the ports answer from the
temporary database the fixture builds.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.repos import base as repos_base
from core.repos.broadcast import SqliteBroadcastJournal, get_unfinished_broadcasts
from core.repos.outbox import (LOCK_FOR, REVIEW, SqliteMessageQueue,
                               campaign_stats, claim, mark_sent)
from core.repos.schema import init_db
from core.repos.users import (SqliteLanguageChoice, SqliteMailingList,
                              opt_out_user, save_user)
from core.usecases.broadcast import (campaign_for, report_finished_jobs,
                                     start_broadcast)

ADMIN = 129462784
CUSTOMERS = (555, 556, 557)
TEXT = "Нова колекція вже в магазині 🌸"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_base, "DB_PATH", str(tmp_path / "bot_data.db"))
    asyncio.run(init_db())
    for chat_id in CUSTOMERS:
        asyncio.run(save_user(chat_id, f"+38067000{chat_id}"))


def _start() -> tuple[int, int]:
    started = asyncio.run(start_broadcast(
        TEXT, ADMIN,
        SqliteBroadcastJournal(), SqliteMailingList(), SqliteMessageQueue(),
    ))
    return started.job_id, started.queued


def _report() -> list[int]:
    return asyncio.run(report_finished_jobs(
        SqliteBroadcastJournal(), SqliteLanguageChoice(), SqliteMessageQueue(),
    ))


def test_every_recipient_gets_a_queued_message(db):
    job_id, queued = _start()
    assert queued == len(CUSTOMERS)

    rows = asyncio.run(claim(50))
    assert sorted(row["chat_id"] for row in rows) == sorted(CUSTOMERS)
    assert {row["campaign_key"] for row in rows} == {f"bcast.{job_id}"}
    assert json.loads(rows[0]["payload"])["text"] == TEXT


def test_a_broadcast_refuses_to_guess_after_a_crash(db):
    """§6.2, and the one behaviour that genuinely changed. The old driver
    resumed from a status column a crash could equally have lost; these rows
    stop and wait for a person instead of messaging people twice."""
    _start()
    assert {row["on_uncertain"] for row in asyncio.run(claim(50))} == {REVIEW}


def test_someone_who_opted_out_is_not_queued(db):
    asyncio.run(opt_out_user(555))
    _job_id, queued = _start()

    assert queued == 2
    assert 555 not in [row["chat_id"] for row in asyncio.run(claim(50))]


def test_opting_out_after_the_send_does_not_pull_the_message_back(db):
    """The recipient list is snapshotted at the moment of sending — as the
    queued rows themselves now, and as broadcast_targets before that."""
    _start()
    asyncio.run(opt_out_user(555))

    assert 555 in [row["chat_id"] for row in asyncio.run(claim(50))]


def test_each_send_is_its_own_campaign(db):
    """So Tuesday's broadcast and Wednesday's are two lines in the funnel rather
    than one bucket called "broadcast"."""
    first_id, _ = _start()
    second_id, _ = _start()

    assert first_id != second_id
    assert str(campaign_for(first_id)) != str(campaign_for(second_id))


def test_queueing_one_campaign_twice_writes_nothing_the_second_time(db):
    """What the dedup prefix is for: the same job queued again — a retried call,
    a resumed handler — must not double anybody's message."""
    from core.domain.campaign import CampaignKey
    from core.repos.outbox import enqueue_many

    campaign = CampaignKey("bcast", "77")
    first = asyncio.run(enqueue_many(list(CUSTOMERS), "broadcast", campaign,
                                     {"text": TEXT}, dedup_prefix=str(campaign)))
    again = asyncio.run(enqueue_many(list(CUSTOMERS), "broadcast", campaign,
                                     {"text": TEXT}, dedup_prefix=str(campaign)))

    assert (first, again) == (len(CUSTOMERS), 0)


# --- the summary ------------------------------------------------------------

def test_a_job_is_not_finished_while_anything_is_waiting(db):
    job_id, _ = _start()
    assert _report() == []
    assert [job["id"] for job in asyncio.run(get_unfinished_broadcasts())] == [job_id]


def test_when_the_last_message_is_decided_the_admin_gets_the_summary(db):
    job_id, _ = _start()
    for row in asyncio.run(claim(50)):
        asyncio.run(mark_sent(row["id"]))

    assert _report() == [job_id]
    assert asyncio.run(get_unfinished_broadcasts()) == []

    # The summary is itself a queued message rather than the one send that
    # bypasses the queue.
    [report] = [row for row in asyncio.run(claim(50)) if row["chat_id"] == ADMIN]
    assert report["type"] == "broadcast_report"
    assert "3" in json.loads(report["payload"])["text"]


def test_the_summary_is_sent_once(db):
    job_id, _ = _start()
    for row in asyncio.run(claim(50)):
        asyncio.run(mark_sent(row["id"]))

    _report()
    _report()

    reports = [row for row in asyncio.run(claim(50)) if row["chat_id"] == ADMIN]
    assert len(reports) == 1


def test_the_numbers_come_from_the_queue(db):
    """sent, blocked and failed are three different things to the person
    reading the summary, and the queue is where all three now live."""
    job_id, _ = _start()
    rows = asyncio.run(claim(50))
    asyncio.run(mark_sent(rows[0]["id"]))

    from core.repos.outbox import GONE_PREFIX, park

    asyncio.run(park(rows[1]["id"], f"{GONE_PREFIX} bot was blocked"))
    asyncio.run(park(rows[2]["id"], "5 attempts, last: Bad Gateway"))

    stats = asyncio.run(campaign_stats(str(campaign_for(job_id))))
    assert stats == {"sent": 1, "blocked": 1, "failed": 1, "waiting": 0}


def test_a_job_whose_messages_were_never_queued_is_closed_with_zeros(db):
    """The window between recording a job and queueing it is milliseconds wide,
    but a job left "running" would be re-checked on every pass forever — and
    "nothing went out" is a true summary, not a missing one."""
    from core.repos.broadcast import create_broadcast_job

    job_id = asyncio.run(create_broadcast_job(TEXT, ADMIN))

    assert _report() == [job_id]
    [report] = [row for row in asyncio.run(claim(50)) if row["chat_id"] == ADMIN]
    assert "0" in json.loads(report["payload"])["text"]


def test_a_command_never_becomes_the_broadcast(db):
    """The one place an admin command can reach a customer.

    `process_broadcast_message` takes `message.text` verbatim and `F.text`
    matches a command like any other line. Registration order then decides the
    outcome and is not on our side: /stop and /broadcast sit above the handler
    and fire, while /stats and /chatid sit below it and become the broadcast —
    so "/stats" goes out to every subscriber, from the shop, signed by the shop.

    Checked on the slash rather than on a list of known commands, because a
    typo — "/statss" — has no handler and would still be sent."""
    import asyncio
    from types import SimpleNamespace

    from bot.handlers import broadcast as mod

    ADMIN = 4242
    config = SimpleNamespace(env=SimpleNamespace(admin_ids=[ADMIN]))
    stored: dict = {}

    class _State:
        async def update_data(self, **kwargs):
            stored.update(kwargs)
            return dict(stored)

        async def set_state(self, state) -> None:
            stored["state"] = state

        async def clear(self) -> None:
            stored.clear()

    for text in ("/stats", "/chatid", "/statss", "/menu"):
        said: list[str] = []

        async def answer(line, **kwargs):
            said.append(line)
            return SimpleNamespace(message_id=1)

        message = SimpleNamespace(
            chat=SimpleNamespace(id=ADMIN), from_user=SimpleNamespace(id=ADMIN),
            text=text, answer=answer)
        asyncio.run(mod.process_broadcast_message(message, config, _State(), None))

        assert "broadcast_text" not in stored, f"{text} became a broadcast"
        assert said and "/" in said[0], (
            f"{text} was dropped without saying why")
