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
