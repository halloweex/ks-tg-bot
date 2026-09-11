"""The only door into this bot from outside Telegram, and its two locks.

Everything else the bot does it starts itself. This arrives uninvited, from a
service whose signing key has already been seen on a screenshot once, so the
tests here are mostly about what must NOT happen: no message on a bad
signature, none on a body that was tampered with after signing, none for a
customer nobody here knows.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from base64 import b64encode

from aiohttp.test_utils import TestClient, TestServer

from bot.webhooks import build_app
from core.i18n import Texts

SECRET = "b1778e9a7deda7d3a800437e8611d9c0"
PATH = "/rivo/2f8c1d"
KNOWN, UNKNOWN = "olya@example.com", "nobody@example.com"
T = Texts("uk")


class _Chats:
    async def chat_for(self, email):
        return 777 if email.lower() == KNOWN else None


class _Languages:
    async def chosen_by(self, chat_id):
        return "uk"


class _Queue:
    def __init__(self):
        self.sent: list[dict] = []

    async def queue(self, chat_id, kind, campaign, payload, **kwargs):
        self.sent.append({"chat_id": chat_id, **payload, **kwargs})
        return len(self.sent)


def _body(event_type: str, email: str = KNOWN, **extra) -> bytes:
    payload = {
        "event_type": event_type,
        "points_diff": extra.pop("points", 22),
        "customer": {
            "email": email,
            "points_tally": extra.pop("balance", 527),
            "loyalty_status": extra.pop("tier", "VIP"),
        },
    }
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _sign(body: bytes, secret: str = SECRET) -> str:
    return b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def call(body: bytes, signature: str | None) -> tuple[int, list[dict]]:
    """POST one body at a real server on a real socket.

    Through aiohttp's own test server rather than by calling the handler with a
    hand-made request: the signature is computed over the bytes as they arrive,
    and a fake request is exactly the place where that stops being true.
    """
    queue = _Queue()
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=queue,
                    account_url="https://koreanstory.com.ua/account")

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            headers = {"rivo-signature": signature} if signature is not None else {}
            response = await client.post(PATH, data=body, headers=headers)
            return response.status

    return asyncio.run(go()), queue.sent


# --- the locks --------------------------------------------------------------

def test_a_signed_event_reaches_the_customer():
    body = _body("balance_transaction/created")
    status, sent = call(body, _sign(body))
    assert status == 200
    assert sent[0]["chat_id"] == 777
    assert "22" in sent[0]["text"] and "527" in sent[0]["text"]


def test_no_signature_is_refused():
    body = _body("balance_transaction/created")
    status, sent = call(body, None)
    assert status == 401 and not sent


def test_a_wrong_key_is_refused():
    body = _body("balance_transaction/created")
    status, sent = call(body, _sign(body, "not-the-secret"))
    assert status == 401 and not sent


def test_a_body_changed_after_signing_is_refused():
    """The whole point of signing the raw bytes: a proxy, or somebody, editing
    one number must invalidate it."""
    body = _body("balance_transaction/created", points=22)
    signature = _sign(body)
    tampered = body.replace(b'"points_diff": 22', b'"points_diff": 9999')
    status, sent = call(tampered, signature)
    assert status == 401 and not sent


# --- who it is about --------------------------------------------------------

def test_a_customer_we_do_not_know_gets_nothing():
    """The loyalty programme has thousands of customers and this bot a handful.
    Most events are about somebody who never opened it, and that is ordinary."""
    body = _body("balance_transaction/created", email=UNKNOWN)
    status, sent = call(body, _sign(body))
    assert status == 200 and not sent


def test_points_spent_are_not_announced():
    """She just spent them, on purpose. Telling her is noise."""
    body = _body("balance_transaction/created", points=-40)
    status, sent = call(body, _sign(body))
    assert status == 200 and not sent


def test_an_event_we_do_not_announce_is_accepted_and_dropped():
    """200, not an error: a webhook that errors gets retried, and there is
    nothing here to retry."""
    body = _body("membership/payment_success")
    status, sent = call(body, _sign(body))
    assert status == 200 and not sent


# --- what she is told -------------------------------------------------------

def test_a_new_tier_is_congratulated():
    body = _body("customer_vip_tier/upgraded", tier="VIP")
    status, sent = call(body, _sign(body))
    assert status == 200
    assert "VIP" in sent[0]["text"]


def test_expiring_points_name_the_balance():
    body = _body("notification/points_expiry_warning", balance=310)
    _status, sent = call(body, _sign(body))
    assert "310" in sent[0]["text"]


def test_every_message_offers_the_account():
    """The bot reports; the points are spent on the shop's own page."""
    body = _body("balance_transaction/created")
    _status, sent = call(body, _sign(body))
    button = sent[0]["keyboard"]["inline_keyboard"][0][0]
    assert button["url"] == "https://koreanstory.com.ua/account"


def test_one_message_per_kind_per_day():
    """Rivo can award points three times for one order. Three buzzes in a
    minute is how a helpful channel becomes a muted one."""
    body = _body("balance_transaction/created")
    _status, sent = call(body, _sign(body))
    assert sent[0]["dedup_key"].endswith(":points:777")


# --- the number, and the ways it can fail to arrive --------------------------
#
# `_body` above always writes `points_diff` itself, so every test in this file
# ran on the one shape that could not go wrong. There is no tests/fixtures/rivo
# directory — the only adapter in the repo with no saved real payload — so the
# `points_amount` branch the parser's own comment says exists had never once
# been exercised. The two bodies below are constructed from Rivo's documented
# shape, not recorded from the wire, and that is still the open half of this:
# real bodies are wanted, see docs/found-during-move.md §18.


def _raw(payload: dict) -> bytes:
    import json as _json
    return _json.dumps(payload).encode("utf-8")


def _points_body(**fields) -> bytes:
    payload = {
        "event_type": "points_event/created",
        "customer": {"email": KNOWN, "points_tally": 527, "loyalty_status": "VIP"},
    }
    payload.update(fields)
    return _raw(payload)


def _said(fn) -> str:
    """What loguru wrote while fn ran. caplog cannot see it: loguru does not go
    through the standard logging module."""
    from loguru import logger
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING")
    try:
        fn()
    finally:
        logger.remove(sink)
    return "\n".join(lines)


def test_an_unsigned_points_event_is_not_announced_but_is_said_out_loud():
    """The first repair of this was worse than the bug, and this test is the
    reason it did not ship.

    `points_diff: null` used to fall through to `points_amount` — but that field
    is the same number WITHOUT a sign, and `balance_transaction/created` carries
    redemptions as well as awards. So forty points spent came back as forty
    points won, «Тобі нараховано 40 балів» about points she had just spent, and
    `announce`'s one-a-day dedup then ate her real award an hour later.

    No signed field, no claim. The half of this that had teeth was never the
    missed message; it was that nothing anywhere said a word."""
    from core.adapters.rivo import parse as mod

    mod._UNSIGNED_SEEN.discard("points_event/created")
    body = _points_body(points_diff=None, points_amount=22)
    out: list = []
    said = _said(lambda: out.append(call(body, _sign(body))))
    status, sent = out[0]

    assert status == 200
    assert not sent, "a number with no sign was announced as an award"
    assert "points_amount=22" in said, (
        "a shape we cannot act on must be visible, or nobody can go and fetch "
        "the real payload")


def test_a_redemption_with_no_signed_field_is_never_called_an_award():
    """The direction that made the first repair dangerous, and the one its own
    tests missed: they built the spend with `points_diff` PRESENT."""
    body = _points_body(points_diff=None, points_amount=40)
    status, sent = call(body, _sign(body))

    assert status == 200
    assert not sent, (
        "40 points spent were announced as 40 points awarded — and the dedup "
        "key then blocks her real award for the rest of the day")


def test_a_signed_zero_is_not_read_as_a_missing_field():
    """The obvious repair — `points_diff or points_amount` — is wrong here. An
    event that changed nothing carries a real zero, and falling through to the
    unsigned field would announce it as an award."""
    body = _points_body(points_diff=0, points_amount=22)
    status, sent = call(body, _sign(body))

    assert status == 200
    assert not sent, "a zero change was announced as though 22 were awarded"


def test_a_spend_is_still_not_announced():
    """The reason the signed field is preferred at all: she spent them herself,
    on purpose, and does not need telling."""
    body = _points_body(points_diff=-40, points_amount=40)
    status, sent = call(body, _sign(body))
    assert status == 200 and not sent


def test_an_event_type_we_do_not_know_is_logged_once():
    """A name we do not know is the ordinary case — `_KINDS` covers eight of
    about thirty. It is also exactly what a rename on Rivo's side looks like,
    and a rename kills the whole loyalty channel while the endpoint goes on
    answering 200. One line per type is what makes that visible without
    drowning the log in the ordinary case."""
    from loguru import logger

    from core.adapters.rivo import parse as mod

    mod._UNANNOUNCED_SEEN.discard("order/refunded")
    said: list[str] = []
    sink = logger.add(lambda m: said.append(str(m)), level="INFO")
    try:
        for _ in range(3):
            body = _raw({"event_type": "order/refunded",
                         "customer": {"email": KNOWN}})
            status, sent = call(body, _sign(body))
            assert status == 200 and not sent
    finally:
        logger.remove(sink)

    lines = [line for line in said if "order/refunded" in line]
    assert len(lines) == 1, (
        f"expected exactly one line for a new event type, got {len(lines)}")


# --- the first real body records itself --------------------------------------
#
# Rivo is the only service this repo talks to with no saved payload: keycrm,
# novaposhta and shopify all have recordings under tests/fixtures and their
# parsers run on what the service actually sends. That gap is exactly how
# `points_diff: null` went unnoticed — nobody had ever seen a real body. Fetching
# them by hand means somebody logged into Rivo at the right moment, so instead
# the bodies we cannot fully act on write themselves down.


def _with_samples(tmp_path, body: bytes, signature: str | None = None):
    """Drive the real server with sampling switched on, and return the files."""
    queue = _Queue()
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=queue,
                    account_url="https://koreanstory.com.ua/account",
                    sample_dir=tmp_path)

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            headers = {"rivo-signature": signature or _sign(body)}
            response = await client.post(PATH, data=body, headers=headers)
            return response.status

    status = asyncio.run(go())
    return status, sorted(p.name for p in tmp_path.glob("*.json"))


def test_an_unknown_event_type_writes_itself_down(tmp_path):
    body = _raw({"event_type": "order/refunded", "amount": 120,
                 "customer": {"email": KNOWN}})
    status, files = _with_samples(tmp_path, body)

    assert status == 200
    assert files == ["order_refunded.json"], (
        "a body we could not act on is the one worth keeping")


def test_the_shape_that_cost_a_customer_her_award_is_kept(tmp_path):
    """The important one, and the one a naive sampler misses: a points event
    with no signed amount parses into a perfectly good event that `announce`
    then drops. Watching only for a None from `parse_event` would never see
    it — which is why the parser is asked instead."""
    body = _points_body(points_diff=None, points_amount=22)
    status, files = _with_samples(tmp_path, body)

    assert status == 200
    assert files == ["points_event_created.json"]


def test_a_body_we_understood_is_not_kept(tmp_path):
    """A sampler, not a log. Once the shape is known there is nothing to learn
    from another copy of it, and a directory that grows on every award is a
    directory nobody will read."""
    body = _points_body(points_diff=22)
    status, files = _with_samples(tmp_path, body)

    assert status == 200 and files == []


def test_the_second_one_of_a_kind_does_not_overwrite_the_first(tmp_path):
    body = _raw({"event_type": "order/refunded", "customer": {"email": KNOWN}})
    _with_samples(tmp_path, body)
    first = (tmp_path / "order_refunded.json").read_text()

    other = _raw({"event_type": "order/refunded", "extra": "later",
                  "customer": {"email": KNOWN}})
    _with_samples(tmp_path, other)

    assert (tmp_path / "order_refunded.json").read_text() == first


def test_nothing_a_customer_could_be_identified_by_reaches_the_disk(tmp_path):
    """A fixture is meant to be committed, and a body with somebody's address
    in it could not be. `customer` is rebuilt from the fields the parser reads
    rather than filtered, because a denylist only protects against the fields we
    thought of and Rivo is free to add one we did not."""
    import json as _json

    body = _raw({
        "event_type": "order/refunded",
        "customer": {"email": "olya@example.com", "first_name": "Оля",
                     "phone": "+380670000000", "address": "вул. Хрещатик, 1",
                     "points_tally": 527, "loyalty_status": "VIP"},
    })
    _with_samples(tmp_path, body)
    written = (tmp_path / "order_refunded.json").read_text()

    for secret in ("olya@example.com", "Оля", "380670000000", "Хрещатик"):
        assert secret not in written, f"{secret!r} was written to disk"

    kept = _json.loads(written)["customer"]
    assert kept["points_tally"] == 527 and kept["loyalty_status"] == "VIP", (
        "the fields the parser reads are the point of the fixture")
    assert kept["_redacted_keys"] == ["address", "first_name", "phone"], (
        "what was dropped is named, so a shape change is still visible")


def test_a_body_with_a_bad_signature_is_never_sampled(tmp_path):
    """Anything reaching the sampler has already passed the HMAC, so nothing
    written there came from anywhere but Rivo. Without this, the endpoint is a
    way for a stranger to put files on the disk."""
    body = _raw({"event_type": "order/refunded", "customer": {"email": KNOWN}})
    status, files = _with_samples(tmp_path, body, signature="not-the-signature")

    assert status == 401 and files == []


def test_sampling_off_is_the_default(tmp_path):
    """A module that starts writing files because somebody imported it is a
    module nobody can test twice."""
    body = _raw({"event_type": "order/refunded", "customer": {"email": KNOWN}})
    status, sent = call(body, _sign(body))

    assert status == 200 and not sent
    assert list(tmp_path.glob("*.json")) == []


# --- the watcher for the day nobody calls ------------------------------------
#
# The failure it exists for is the one that looks like success: in September the
# /rivo/ route vanished from the neighbouring project's nginx and every request
# was answered 405 by their application. Eleven days, with the endpoint up, the
# container healthy and every deploy green. Nothing here could notice, because
# everything it monitored was working.


def _watch(last: str | None, *, up_for_days: float, admin_ids=(1,)):
    """Run one poll of the watchdog and return what the admins were told."""
    from datetime import datetime, timedelta, timezone

    from bot import alerts, webhooks as mod

    told: list[str] = []

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            told.append(text)

    async def last_arrival():
        return last

    async def go() -> None:
        # One tick, then out: the loop is `sleep` then poll, so a zero sleep and
        # a cancel on the second pass exercises exactly one poll.
        real_sleep = asyncio.sleep
        calls = {"n": 0}

        async def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] > 1:
                raise asyncio.CancelledError
            await real_sleep(0)

        mod.asyncio.sleep = fake_sleep
        try:
            await mod.watch_for_silence(
                _Bot(), list(admin_ids), last_arrival,
                started=datetime.now(timezone.utc) - timedelta(days=up_for_days))
        except asyncio.CancelledError:
            pass
        finally:
            mod.asyncio.sleep = real_sleep

    alerts._last_told.clear()
    asyncio.run(go())
    return told


def _ago(days: float) -> str:
    from datetime import datetime, timedelta, timezone
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def test_a_channel_that_went_quiet_for_days_is_reported():
    told = _watch(_ago(5), up_for_days=10)
    assert told, "five days of silence went unreported"
    assert "loyalty webhook" in told[0]


def test_a_channel_that_is_being_called_says_nothing():
    told = _watch(_ago(0.5), up_for_days=10)
    assert told == []


def test_the_boundary_is_where_the_constant_says_it_is():
    """Just inside and just outside SILENCE_AFTER, rather than a comfortable
    middle: a test at five days passes whether the threshold is three days or
    four, which is to say it pins nothing."""
    from bot.webhooks import SILENCE_AFTER

    days = SILENCE_AFTER.days
    assert _watch(_ago(days - 0.1), up_for_days=30) == [], (
        "alerted before the silence was long enough")
    assert _watch(_ago(days + 0.1), up_for_days=30), (
        "stayed quiet past the threshold")


def test_a_fresh_deploy_does_not_alert_about_the_days_before_it(): 
    """Silence is measured from whichever is later, the last arrival or the
    moment this process came up. Without that a restart alerts immediately
    about a week it was not running for, which is not news and is how an alert
    gets muted."""
    told = _watch(_ago(30), up_for_days=0.2)
    assert told == []


def test_a_channel_that_has_never_been_called_is_reported_as_such():
    told = _watch(None, up_for_days=10)
    assert told and "nothing ever has" in told[0], (
        "never called and gone quiet are different facts")


def test_an_unreadable_timestamp_alerts_rather_than_silencing():
    """A stamp this cannot parse must read as "no arrival", which alerts —
    never as "now", which would mute the watchdog on exactly the day its input
    changed shape."""
    told = _watch("not a timestamp", up_for_days=10)
    assert told


def test_with_no_admins_it_declines_to_run_rather_than_alerting_nobody():
    assert _watch(_ago(30), up_for_days=30, admin_ids=()) == []


def test_a_request_with_a_wrong_signature_still_counts_as_an_arrival():
    """Load-bearing, and a mutation caught it missing: moving the mark below
    the signature check passed every other test here.

    The watchdog asks one question — does anything from the internet reach this
    process. A forged request answers it just as well as a genuine one, and
    counting only signed requests would make "nobody is calling" and "somebody
    is calling and being refused" look identical. Those have different causes:
    the first is a route or a missing webhook, the second is a rotated signing
    key."""
    arrivals: list[int] = []
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue(),
                    arrived=lambda: arrivals.append(1))
    body = _points_body(points_diff=22)

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            r = await client.post(PATH, data=body,
                                  headers={"rivo-signature": "wrong"})
            return r.status

    assert asyncio.run(go()) == 401
    assert arrivals == [1], (
        "a refused request is still proof the path reaches this process")


def test_a_kept_sample_is_announced_once_and_only_when_new(tmp_path):
    """A file on a server nobody is told about is not a fixture.

    The sampler wrote a log line and stopped there, which meant the body it
    caught would sit on disk until somebody happened to look. Collecting it is
    the entire point, so the path goes out to whoever can act on it — once per
    shape, because the second copy of a known shape teaches nothing."""
    announced: list[str] = []
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue(),
                    sample_dir=tmp_path, kept=announced.append)
    body = _raw({"event_type": "order/refunded", "customer": {"email": KNOWN}})

    async def go() -> None:
        async with TestClient(TestServer(app)) as client:
            for _ in range(3):
                await client.post(PATH, data=body,
                                  headers={"rivo-signature": _sign(body)})

    asyncio.run(go())

    assert len(announced) == 1, f"announced {len(announced)} times"
    assert announced[0].endswith("order_refunded.json")


def test_a_shape_we_understood_is_not_announced(tmp_path):
    announced: list[str] = []
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue(),
                    sample_dir=tmp_path, kept=announced.append)
    body = _points_body(points_diff=22)

    async def go() -> None:
        async with TestClient(TestServer(app)) as client:
            await client.post(PATH, data=body,
                              headers={"rivo-signature": _sign(body)})

    asyncio.run(go())
    assert announced == []
