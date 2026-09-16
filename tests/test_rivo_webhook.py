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
from datetime import datetime, timedelta, timezone

from aiohttp.test_utils import TestClient, TestServer

from bot.webhooks import (ALERTED, ARRIVED, MAX_AGENT, MAX_BODY, WATCHING,
                          build_app)
from core.i18n import Texts

# Obviously invented. The previous value here was the first half of the live
# signing key, in a public repository — found on 2026-09-16 by comparing it
# with the key shown in the Rivo cabinet.
SECRET = "0123456789abcdef" * 4
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


def call_with_headers(body: bytes, headers: dict) -> int:
    """POST one body with exactly these headers, and return the status."""
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue())

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            response = await client.post(PATH, data=body, headers=headers)
            return response.status

    return asyncio.run(go())


# --- the locks --------------------------------------------------------------

def test_a_signed_event_reaches_the_customer():
    body = _body("balance_transaction/created")
    status, sent = call(body, _sign(body))
    assert status == 200
    assert sent[0]["chat_id"] == 777
    assert "22" in sent[0]["text"] and "527" in sent[0]["text"]


def _call_with(secret: str, body: bytes, signature: str) -> int:
    """POST one body at an app configured with `secret`, return the status."""
    app = build_app(path=PATH, secret=secret, chats=_Chats(),
                    languages=_Languages(), queue=_Queue())

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            r = await client.post(PATH, data=body,
                                  headers={"rivo-signature": signature})
            return r.status

    return asyncio.run(go())


def test_any_key_in_the_list_verifies():
    """Rivo's cabinet shows one key for the account, but the list is how a key
    is rotated without a gap: the new one beside the old until Rivo switches."""
    body = _body("balance_transaction/created")
    keys = "first-key,second-key, third-key "
    for key in ("first-key", "second-key", "third-key"):
        assert _call_with(keys, body, _sign(body, key)) == 200, key


def test_a_key_that_is_not_in_the_list_is_still_refused():
    body = _body("balance_transaction/created")
    assert _call_with("first-key,second-key", body,
                      _sign(body, "somebody-else")) == 401


def test_a_stray_comma_never_turns_into_a_key_anybody_has():
    """An HMAC with an empty key is something anybody can compute. A trailing
    comma or a blank between two commas must not become a key that verifies —
    that would be a forged loyalty message for the price of a typo."""
    body = _body("balance_transaction/created")
    for keys in ("real-key,", "real-key, ,", ",real-key"):
        assert _call_with(keys, body, _sign(body, "")) == 401, repr(keys)


def test_one_key_still_works_exactly_as_before():
    body = _body("balance_transaction/created")
    assert _call_with(SECRET, body, _sign(body)) == 200


def _standard(body: bytes, *, key: bytes, webhook_id="msg_2mY8", ts="1789563071",
              content="id.ts.body", encoding="base64", label="v1,") -> dict:
    """Headers the way Rivo actually sends them: `rivo-webhook-*`."""
    signed = {"body": body,
              "ts.body": ts.encode() + b"." + body,
              "id.ts.body": webhook_id.encode() + b"." + ts.encode() + b"." + body}[content]
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    value = b64encode(digest).decode() if encoding == "base64" else digest.hex()
    return {"rivo-webhook-signature": label + value,
            "rivo-webhook-id": webhook_id, "rivo-webhook-timestamp": ts}


def test_the_headers_rivo_really_sends_are_verified():
    """On 2026-09-16 13:11 UTC Rivo's own test webhooks carried no
    `rivo-signature` — the only header its documentation names — and instead a
    `rivo-webhook-signature` beside an id and a timestamp. The Standard Webhooks
    construction signs `id.timestamp.body`, labelled `v1,`."""
    body = _body("balance_transaction/created")
    said = _said_info(lambda: call_with_headers(
        body, _standard(body, key=SECRET.encode())))
    assert "verified (rivo-webhook-signature, path #1/1, key #1/1)" in said, said


def test_only_the_scheme_rivo_was_measured_to_use_verifies():
    """While the scheme was unknown the verifier tried several constructions and
    logged which one matched. Eight test webhooks and eight real events matched
    one — the key as text, `id.timestamp.body`, base64 — and the rest are gone.
    Each of them, signed with the right key, is now refused."""
    body = _body("balance_transaction/created")
    ts, wid = "1789563071", "msg_2mY8"
    key = SECRET.encode()

    def sig(k, content, enc="base64"):
        d = hmac.new(k, content, hashlib.sha256).digest()
        return b64encode(d).decode() if enc == "base64" else d.hex()

    retired = {
        "key read as hex": sig(bytes.fromhex(SECRET), wid.encode() + b"." + ts.encode() + b"." + body),
        "ts.body": sig(key, ts.encode() + b"." + body),
        "body alone under the new header": sig(key, body),
        "hex encoding": sig(key, wid.encode() + b"." + ts.encode() + b"." + body, "hex"),
    }
    for name, value in retired.items():
        headers = {"rivo-webhook-signature": "v1," + value,
                   "rivo-webhook-id": wid, "rivo-webhook-timestamp": ts}
        assert call_with_headers(body, headers) == 401, name

    # With the id present, so the refusal is about the scheme and not about a
    # missing header.
    stripe = f"t={ts},v1=" + sig(key, ts.encode() + b"." + body, "hex")
    assert call_with_headers(body, {"rivo-webhook-signature": stripe,
                                    "rivo-webhook-id": wid,
                                    "rivo-webhook-timestamp": ts}) == 401


def test_a_bare_signature_without_the_v1_label_is_read_too():
    """Only the verdict was ever logged, never the header's spelling, so both
    Standard Webhooks' `v1,<sig>` and a bare signature are read."""
    body = _body("balance_transaction/created")
    headers = _standard(body, key=SECRET.encode(), label="")
    assert call_with_headers(body, headers) == 200


def test_the_new_signature_without_its_id_or_timestamp_is_refused():
    """Both are inside what is signed; a request missing either cannot be
    checked and is not waved through."""
    body = _body("balance_transaction/created")
    for drop in ("rivo-webhook-id", "rivo-webhook-timestamp"):
        headers = _standard(body, key=SECRET.encode())
        del headers[drop]
        assert call_with_headers(body, headers) == 401, drop


def _status_at(path_config: str, request_path: str) -> int:
    app = build_app(path=path_config, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue())
    body = _body("balance_transaction/created")

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            r = await client.post(request_path, data=body,
                                  headers={"rivo-signature": _sign(body)})
            return r.status

    return asyncio.run(go())


def test_two_paths_both_work_while_the_cabinet_is_being_edited():
    """The path leaked, and changing it means editing the URL in every Rivo
    webhook by hand. The new path and the old one both answer until that is
    done; with one path, whatever Rivo sent in between would be lost."""
    config = "/rivo/new-secret-segment,/rivo/old-secret-segment"
    assert _status_at(config, "/rivo/new-secret-segment") == 200
    assert _status_at(config, "/rivo/old-secret-segment") == 200
    assert _status_at(config, "/rivo/somebody-guessing") == 404


def test_a_path_outside_rivo_is_never_a_route():
    """`/rivo/` is what the neighbouring nginx forwards; the rule keeps a stray
    `/` or `/health` in the file from becoming a route that takes webhooks."""
    config = "/rivo/real-segment, /, /health, /rivo/, ,"
    assert _status_at(config, "/rivo/real-segment") == 200
    for stray in ("/", "/rivo/"):
        assert _status_at(config, stray) in (404, 405), stray


def test_a_valid_documented_signature_does_not_rescue_a_bad_new_one():
    """The scheme is chosen by the header. Both use the same key; a request that
    carries a garbage `rivo-webhook-signature` must not pass on a valid
    `rivo-signature` sent beside it."""
    body = _body("balance_transaction/created")
    headers = {"rivo-webhook-signature": "v1,AAAA", "rivo-webhook-id": "x",
               "rivo-webhook-timestamp": "1", "rivo-signature": _sign(body)}
    assert call_with_headers(body, headers) == 401


def test_a_key_rotation_shows_which_key_rivo_is_using():
    """The rotation of the key goes through the scheme Rivo really uses, and the
    old key is only removed once the log shows Rivo signing with the new one."""
    body = _body("balance_transaction/created")

    async def go(key: bytes) -> int:
        # One app per event loop: aiohttp binds an application to the loop
        # that first runs it.
        app = build_app(path=PATH, secret="new-key,old-key", chats=_Chats(),
                        languages=_Languages(), queue=_Queue())
        async with TestClient(TestServer(app)) as client:
            r = await client.post(PATH, data=body, headers=_standard(body, key=key))
            return r.status

    for key, number in ((b"old-key", "key #2/2"), (b"new-key", "key #1/2")):
        said = _said_info(lambda: asyncio.run(go(key)))
        assert number in said, said


def test_a_header_that_is_not_utf8_is_refused_not_a_crash():
    """It used to be a 500 with a traceback. A signature that cannot even be
    encoded is a signature that does not match."""
    from bot.webhooks import _verify
    assert _verify(("k",), b"{}", webhook_signature="v1,\udce9", documented_signature="",
                   webhook_id="x", timestamp="1") is None
    assert _verify(("k",), b"{}", webhook_signature="", documented_signature="\udce9",
                   webhook_id="", timestamp="") is None


def test_a_path_pasted_twice_does_not_take_the_bot_down():
    """aiohttp raises when the same route is registered twice, at startup, and
    that is the whole bot — Telegram polling included — in a restart loop. The
    easiest mistake to make during a rotation."""
    config = "/rivo/same-segment, /rivo/same-segment"
    assert _status_at(config, "/rivo/same-segment") == 200


def test_pattern_syntax_in_a_path_is_never_a_wildcard():
    """`/rivo/{x}` would be an aiohttp pattern matching every path under the
    prefix; a quote, `?`, `#`, `%` or a trailing slash would be a route nothing
    ever reaches. None of them becomes a route."""
    config = "/rivo/{x},/rivo/real-segment,/rivo/typo/,/rivo/a?b,/rivo/\"quoted\""
    assert _status_at(config, "/rivo/real-segment") == 200
    assert _status_at(config, "/rivo/anything-at-all") == 404, "a pattern became a wildcard"


def test_health_still_answers_beside_the_secret_paths():
    app = build_app(path="/rivo/one,/rivo/two", secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue())

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            return (await client.get("/health")).status

    assert asyncio.run(go()) == 200


def test_the_secret_paths_never_reach_the_log():
    """Counts, never values — at startup, on a refusal and on a verified call."""
    body = _body("balance_transaction/created")
    config = "/rivo/very-secret-new,/rivo/very-secret-old,/rivo/{bad}"
    app_calls = []

    def run():
        app = build_app(path=config, secret=SECRET, chats=_Chats(),
                        languages=_Languages(), queue=_Queue())

        async def go():
            async with TestClient(TestServer(app)) as client:
                await client.post("/rivo/very-secret-old", data=body,
                                  headers=_standard(body, key=SECRET.encode()))
                await client.post("/rivo/very-secret-new", data=body,
                                  headers={"rivo-webhook-signature": "v1,AAAA",
                                           "rivo-webhook-id": "x",
                                           "rivo-webhook-timestamp": "1"})
        asyncio.run(go())

    said = _said_info(run)
    assert "serves 2 path(s)" in said and "path #2/2" in said, said
    for secret in ("very-secret-new", "very-secret-old", "{bad}"):
        assert secret not in said, f"{secret!r} reached the log"


def test_the_new_headers_with_a_foreign_key_are_refused():
    body = _body("balance_transaction/created")
    for content in ("body", "ts.body", "id.ts.body"):
        headers = _standard(body, key=b"somebody-else", content=content)
        assert call_with_headers(body, headers) == 401, content


def test_a_signature_over_a_different_id_does_not_verify():
    """The id and timestamp are inside what is signed: moving either must
    break the signature, or a captured request could be replayed as another."""
    body = _body("balance_transaction/created")
    headers = _standard(body, key=SECRET.encode())
    headers["rivo-webhook-id"] = "msg_somebody_else"
    assert call_with_headers(body, headers) == 401


def test_an_empty_signature_never_verifies():
    body = _body("balance_transaction/created")
    for value in ("v1,", "v1, ", "t=1,v1=", " "):
        assert call_with_headers(body, {"rivo-webhook-signature": value,
                                        "rivo-webhook-timestamp": "1",
                                        "rivo-webhook-id": "x"}) == 401, repr(value)


def test_a_refusal_shows_the_signature_shape_and_never_the_signature():
    """A base64 signature can itself begin with `v1`, which is exactly how a
    label-copying shape function would have printed one whole."""
    body = _body("balance_transaction/created")
    value = "v1Zq" + "A" * 40
    said = _said(lambda: call_with_headers(
        body, {"rivo-webhook-signature": "v1," + value,
               "rivo-webhook-id": "x", "rivo-webhook-timestamp": "1"}))
    assert "signature did not match" in said
    assert "labels=['v1,']" in said, said
    assert value not in said, "a signature value reached the log"


def _real_points_body(email: str = KNOWN) -> bytes:
    """The first body Rivo ever sent, redacted by the sampler on 2026-09-16 and
    kept in tests/fixtures/rivo/. Minified, the way it arrives, with the email
    pointed at a test chat."""
    import json
    from pathlib import Path
    payload = json.loads((Path(__file__).parent / "fixtures" / "rivo"
                          / "points_event_created.json").read_text(encoding="utf-8"))
    payload["customer"]["email"] = email
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _post_real(body: bytes, headers: dict, sample_dir=None):
    """POST with Rivo's real headers; return (status, messages queued)."""
    queue = _Queue()
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=queue, sample_dir=sample_dir)

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            r = await client.post(PATH, data=body, headers=headers)
            return r.status

    return asyncio.run(go()), queue.sent


def test_the_real_body_names_its_type_in_a_header_and_is_announced():
    """No `event_type` in Rivo's real body — the type is `rivo-webhook-topic`.
    Before this was read, the channel verified every call, answered 200, and
    told no customer anything."""
    body = _real_points_body()
    headers = {**_standard(body, key=SECRET.encode()),
               "rivo-webhook-topic": "points_event/created"}
    status, sent = _post_real(body, headers)
    assert status == 200
    assert sent and sent[0]["chat_id"] == 777, "a real points event reached nobody"
    assert "100" in sent[0]["text"]


def test_without_the_topic_header_the_real_body_is_not_announced():
    """Pins where the type comes from: the same body, no topic, no message."""
    body = _real_points_body()
    status, sent = _post_real(body, _standard(body, key=SECRET.encode()))
    assert status == 200 and sent == []


def test_a_body_that_names_its_own_type_is_not_overridden_by_the_header():
    """The older API version puts `event_type` in the body; when both are
    there, the body is the more specific of the two."""
    body = _body("customer_vip_tier/upgraded")
    headers = {**_standard(body, key=SECRET.encode()),
               "rivo-webhook-topic": "points_redemption/created"}
    status, sent = _post_real(body, headers)
    assert status == 200 and sent, "the header overrode the body's own type"


def test_a_real_points_event_is_not_mistaken_for_an_unreadable_one(tmp_path):
    """With its type known, the real body is fully readable: no sample, no
    🧪 message to the admins for every order."""
    body = _real_points_body()
    headers = {**_standard(body, key=SECRET.encode()),
               "rivo-webhook-topic": "points_event/created"}
    _post_real(body, headers, sample_dir=tmp_path)
    assert list(tmp_path.glob("*.json")) == []


def _real_referral_body(referrer: str, referred: str) -> bytes:
    """Rivo's real `referral/completed`, recorded and redacted on 2026-09-16:
    no `customer`, a `referrer_customer` and a `referred_customer` instead."""
    import json
    from pathlib import Path
    payload = json.loads((Path(__file__).parent / "fixtures" / "rivo"
                          / "referral_completed.json").read_text(encoding="utf-8"))
    payload["referrer_customer"]["email"] = referrer
    payload["referred_customer"]["email"] = referred
    payload["referred_email"] = referred
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _referral_headers(body: bytes) -> dict:
    return {**_standard(body, key=SECRET.encode()),
            "rivo-webhook-topic": "referral/completed"}


def test_a_real_referral_reaches_the_friend_who_shared_the_link():
    body = _real_referral_body(referrer=KNOWN, referred=UNKNOWN)
    status, sent = _post_real(body, _referral_headers(body))
    assert status == 200
    assert sent and sent[0]["chat_id"] == 777, "the referrer was not told"
    assert "подруга" in sent[0]["text"]


def test_a_referral_never_reaches_the_friend_who_ordered():
    """The message says «твоя подруга зробила замовлення». Sent to the friend
    it would thank her for a reward she did not earn — so when only she is
    somebody this bot can reach, nobody is told anything."""
    body = _real_referral_body(referrer=UNKNOWN, referred=KNOWN)
    status, sent = _post_real(body, _referral_headers(body))
    assert status == 200 and sent == [], "the referred friend got the referrer's message"


def test_a_real_referral_is_fully_readable_now(tmp_path):
    body = _real_referral_body(referrer=KNOWN, referred=UNKNOWN)
    _post_real(body, _referral_headers(body), sample_dir=tmp_path)
    assert list(tmp_path.glob("*.json")) == [], "a readable referral was sampled"


def test_the_documented_referral_shape_still_works():
    body = _body("referral/completed")
    status, sent = call(body, _sign(body))
    assert status == 200 and sent and sent[0]["chat_id"] == 777


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


def _said_info(fn) -> str:
    """Like `_said`, from INFO up: a verified webhook is an INFO line."""
    from loguru import logger
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        fn()
    finally:
        logger.remove(sink)
    return "\n".join(lines)


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


def test_a_refusal_names_the_headers_that_did_arrive_and_never_their_values():
    """Rivo's first four calls were refused with no `rivo-signature` header,
    and the log could not say what they carried instead. The names are what
    tells "unsigned" from "signed under another name"; the values stay out,
    because one of them may be a signature and a log is not a vault."""
    body = _body("balance_transaction/created")
    said = _said(lambda: call_with_headers(
        body, {"X-Rivo-Hmac-Sha256": "c2VjcmV0LXNpZ25hdHVyZQ==",
               "Content-Type": "application/json"}))
    assert "no signature header" in said
    assert "x-rivo-hmac-sha256" in said, said
    assert "c2VjcmV0LXNpZ25hdHVyZQ==" not in said, "a header value reached the log"


def test_a_signature_that_does_not_match_is_told_apart_from_a_missing_one():
    body = _body("balance_transaction/created")
    said = _said(lambda: call(body, _sign(body, "some-other-key")))
    assert "signature did not match" in said
    assert "rivo-signature" in said


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


def test_a_known_type_with_nobody_to_address_is_kept_and_not_announced(tmp_path):
    """Rivo's real `referral/completed` came back "ignored" twice on 2026-09-16:
    a type the parser knows, in a body with no `customer` email. The sampler
    asked only about unknown types and unsigned points, so it kept nothing, and
    the one body worth seeing left no trace."""
    body = _raw({"event_type": "referral/completed",
                 "advocate": {"email": KNOWN}, "referred": {"email": UNKNOWN}})
    status, files = _with_samples(tmp_path, body)
    assert status == 200
    assert files == ["referral_completed.json"]


def test_people_outside_customer_never_reach_the_disk(tmp_path):
    """The bodies most worth recording are the ones whose people are not where
    the parser looks — and a redaction that only knew `customer` would write
    them out verbatim: the friend who referred, the friend who was referred."""
    import json as _json

    body = _raw({
        "event_type": "referral/completed",
        "source": "referral",
        "created_at": "2026-09-16T14:10:20Z",
        "reward_points": 200,
        "referred_email": "anna@example.com",
        "advocate": {"email": "olya@example.com", "first_name": "Оля",
                     "phone": "+380670000000", "id": 42},
        "referrals": [{"name": "Ганна Коваль", "code": "OLYA-7Q"}],
    })
    _with_samples(tmp_path, body)
    written = (tmp_path / "referral_completed.json").read_text()

    for secret in ("anna@example.com", "olya@example.com", "Оля",
                   "380670000000", "Ганна", "OLYA-7Q"):
        assert secret not in written, f"{secret!r} was written to disk"

    kept = _json.loads(written)
    assert kept["event_type"] == "referral/completed" and kept["source"] == "referral"
    assert kept["created_at"] == "2026-09-16T14:10:20Z", "a moment is shape"
    assert kept["reward_points"] == 200 and kept["advocate"]["id"] == 42
    assert kept["advocate"]["email"] == "<str:16>", "the key stays, the text goes"
    assert kept["referrals"][0]["code"] == "<str:7>"


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


def _journal(*, arrived: str | None = None, watching: str | None = None,
             alerted: str | None = None) -> dict:
    """The events table as this watchdog sees it: one stamp per named moment.

    A dict rather than a fake repository, because that is all the watchdog asks
    of the database — three questions of the form "when did this last happen",
    and two answers it writes back. Passing the same dict to two runs is what a
    restart looks like from in here, and that is the whole point of the change
    it pins.
    """
    j = {}
    for key, value in ((ARRIVED, arrived), (WATCHING, watching), (ALERTED, alerted)):
        if value is not None:
            j[key] = value
    return j


def _watch(journal: dict, *, admin_ids=(1,), rounds: int = 1, advance=None,
           note_fails: bool = False, unreadable: tuple = (), deliver: bool = True):
    """Run `rounds` polls of the watchdog against `journal`, which it mutates.

    `advance` moves the watchdog's clock by that much before every poll after
    the first, which is what a test about repeating — or not repeating — needs:
    real time does not pass inside a test, and sleeping for a week to find out
    whether the reminder comes back is not a test anybody runs.
    """
    from datetime import datetime as real_datetime, timezone

    from bot import alerts, webhooks as mod

    told: list[str] = []

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            if not deliver:
                # What an admin who blocked the bot looks like from in here.
                raise RuntimeError("chat not found")
            told.append(text)

    box = {"now": real_datetime.now(timezone.utc)}

    async def when(event: str):
        if event in unreadable:
            raise RuntimeError("database is locked")
        return journal.get(event)

    def note(event: str, **meta) -> None:
        if note_fails:
            # Production's `note` is fire-and-forget and swallows its own write
            # errors, so a lost write looks exactly like this from here: the
            # call returns, and nothing is recorded.
            return
        # The spelling SQLite writes, because that is what `_read_stamp` has to
        # read back when the next process starts.
        journal[event] = box["now"].strftime("%Y-%m-%d %H:%M:%S")

    class _Clock(real_datetime):
        """Subclassed rather than replaced wholesale: `_read_stamp` reaches for
        `datetime.fromisoformat` through the same module global, and a stand-in
        with only `now` on it would make every stamp unreadable — which is a
        state this watchdog has its own behaviour for, and would quietly be the
        state under test instead of the one the test names."""

        @classmethod
        def now(cls, tz=None):
            return box["now"]

    async def go() -> None:
        # The loop is `sleep` then poll, so a zero sleep and a cancel one pass
        # past the last requested round exercises exactly `rounds` polls.
        real_sleep = asyncio.sleep
        calls = {"n": 0}

        async def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] > rounds:
                raise asyncio.CancelledError
            if advance is not None and calls["n"] > 1:
                box["now"] = box["now"] + advance
            await real_sleep(0)

        mod.asyncio.sleep = fake_sleep
        mod.datetime = _Clock
        # `bot.alerts` keeps its own clock. The watchdog no longer goes through
        # the suppression that reads it — it has its own, in the journal — but
        # the patch stays: with real time frozen inside a test run, that shared
        # ten-minute window would swallow every repeat by itself, and a
        # watchdog with no gate at all would look identical to one that has it.
        # That is how the first version of the repeat test passed against the
        # unfixed code.
        alerts.datetime = _Clock
        try:
            await mod.watch_for_silence(_Bot(), list(admin_ids), when, note)
        except asyncio.CancelledError:
            pass
        finally:
            mod.asyncio.sleep = real_sleep
            mod.datetime = real_datetime
            alerts.datetime = real_datetime

    alerts._last_told.clear()
    asyncio.run(go())
    return told


def _ago(days: float) -> str:
    from datetime import datetime, timedelta, timezone
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def test_a_channel_that_went_quiet_for_days_is_reported():
    told = _watch(_journal(arrived=_ago(5), watching=_ago(10)))
    assert told, "five days of silence went unreported"
    assert "loyalty webhook" in told[0]


def test_a_channel_that_is_being_called_says_nothing():
    told = _watch(_journal(arrived=_ago(0.5), watching=_ago(10)))
    assert told == []


def test_the_boundary_is_where_the_constant_says_it_is():
    """Just inside and just outside SILENCE_AFTER, rather than a comfortable
    middle: a test at five days passes whether the threshold is three days or
    four, which is to say it pins nothing."""
    from bot.webhooks import SILENCE_AFTER

    days = SILENCE_AFTER.days
    assert _watch(_journal(arrived=_ago(days - 0.1), watching=_ago(30))) == [], (
        "alerted before the silence was long enough")
    assert _watch(_journal(arrived=_ago(days + 0.1), watching=_ago(30))), (
        "stayed quiet past the threshold")


def test_a_fresh_install_does_not_alert_about_the_days_before_it():
    """A database this has never run against — a new install, or a restored
    volume. Silence is measured from whichever is later, the last arrival or
    the moment this bot first started watching, and on the first run that
    moment is now: a bot must not open by reporting a week it did not exist for.

    Note what this is NOT, any more: a fresh *deploy*. That used to land here
    too, and it is why the watchdog could never reach three days on a week when
    anybody was working."""
    journal = _journal(arrived=_ago(30))
    assert _watch(journal) == []
    assert WATCHING in journal, "the moment it started watching was not kept"


def test_a_channel_that_has_never_been_called_is_reported_as_such():
    told = _watch(_journal(watching=_ago(10)))
    assert told and "nothing ever has" in told[0], (
        "never called and gone quiet are different facts")


def test_an_unreadable_timestamp_alerts_rather_than_silencing():
    """A stamp this cannot parse must read as "no arrival", which alerts —
    never as "now", which would mute the watchdog on exactly the day its input
    changed shape."""
    told = _watch(_journal(arrived="not a timestamp", watching=_ago(10)))
    assert told


def test_with_no_admins_it_declines_to_run_rather_than_alerting_nobody():
    assert _watch(_journal(arrived=_ago(30), watching=_ago(30)),
                  admin_ids=()) == []


def test_the_clock_outlives_a_restart():
    """The change this file exists to pin, and the bug it replaces.

    The baseline used to be the moment the *process* came up, so every deploy
    set the silence back to zero. On any week with daily deploys the count
    could never reach three days, and the watchdog was decorative — the single
    alert it has ever sent came after the longest quiet stretch of the year.
    """
    journal = _journal(arrived=_ago(30))
    assert _watch(journal) == [], "a first run must not alert about the past"

    # Four days and several deploys later. Each of those processes is brand
    # new; the moment the first one wrote down is not.
    journal[WATCHING] = seeded = _ago(4)
    told = _watch(journal)

    assert told, ("a restart reset the clock, so a bot anybody is working on "
                  "can never notice a dead channel")
    # And it must not have moved the moment while reading it. Within one run an
    # overwrite is invisible — `since` is already in hand — so without this the
    # suite stays green against a watchdog that re-stamps the baseline on every
    # start, which is the pre-change bug wearing the new code.
    assert journal[WATCHING] == seeded, "the baseline was rewritten on startup"


def test_the_alert_does_not_come_back_with_every_deploy():
    """The other half, and it has to land in the same change: a baseline that
    survives a restart while the memory of having spoken does not is the hourly
    spam again, one deploy at a time."""
    journal = _journal(arrived=_ago(30), watching=_ago(30))
    assert _watch(journal), "expected the first alert"
    assert ALERTED in journal, "the moment of the alert was not kept"

    assert _watch(journal) == [], "a redeploy sent the same alert again"


def test_a_restarted_bot_still_speaks_up_once_the_week_is_out():
    """And it must not go permanently quiet either: the suppression is an
    interval, not a latch, and it is read back from the journal rather than
    from a variable that a deploy resets."""
    from datetime import datetime, timedelta, timezone

    from bot.webhooks import REALERT_AFTER

    stale = (datetime.now(timezone.utc) - REALERT_AFTER - timedelta(hours=1))
    journal = _journal(arrived=_ago(30), watching=_ago(30),
                       alerted=stale.strftime("%Y-%m-%d %H:%M:%S"))
    assert _watch(journal), "stayed quiet a week past the last reminder"


def test_a_lost_write_does_not_bring_the_hourly_alert_back():
    """The journal is fire-and-forget and swallows its own write errors, so a
    durable gate alone is a downgrade from the local variable it replaced: one
    lost write and the weekly reminder is hourly again, for good. Both memories,
    and the later of the two wins."""
    told = _watch(_journal(arrived=_ago(30), watching=_ago(30)), rounds=5,
                  advance=timedelta(hours=1), note_fails=True)
    assert len(told) == 1, f"said it {len(told)} times with the journal broken"


def test_a_second_outage_is_not_swallowed_by_the_first_reminder():
    """The reminder interval must not span two different outages. Somebody
    called after the last alert, so the channel came back and died again — and
    that is news, not a repeat. Waiting out the rest of the week would swallow
    exactly the pattern this alert tells the reader to expect."""
    told = _watch(_journal(arrived=_ago(4), watching=_ago(30),
                           alerted=_ago(5)))
    assert told, "a fresh outage was suppressed as a repeat of the old one"


def test_a_baseline_it_cannot_read_neither_kills_it_nor_moves_it():
    """The one read on the startup path sits outside the loop that promises
    never to raise. And it must not answer a failed read by writing a second
    baseline: `when` is MAX(created_at), so a second row moves the clock
    forward — the very bug this watchdog was fixed for."""
    journal = _journal(arrived=_ago(30), watching=_ago(30))
    told = _watch(journal, unreadable=(WATCHING,))

    assert told == [], "a fresh process cannot know the channel was quiet"
    assert journal[WATCHING] == _ago(30), (
        "a failed read overwrote the baseline it could not see")


def test_a_baseline_it_cannot_parse_is_not_replaced_by_a_second_one():
    """`_read_stamp` folds "absent" and "unreadable" into the same None, which
    is right for the arrival — a stamp it cannot read must alert rather than
    reassure — and wrong here: the journal is read with MAX(created_at), so
    answering an unreadable baseline with a second row moves the clock forward,
    which is the bug this watchdog was fixed for."""
    journal = _journal(arrived=_ago(30), watching="not a timestamp")
    _watch(journal)
    assert journal[WATCHING] == "not a timestamp", (
        "wrote a second baseline over one it merely could not read")


def test_an_alert_nobody_received_is_not_recorded_as_sent():
    """Recording it would buy a week of silence on the strength of a message
    that reached no one — and unlike before, that mistake now outlives the
    restart that used to clear it."""
    journal = _journal(arrived=_ago(30), watching=_ago(30))
    told = _watch(journal, deliver=False, rounds=2, advance=timedelta(hours=1))

    assert told == []
    assert ALERTED not in journal, "counted as told when nobody was told"


def test_a_baseline_from_the_future_does_not_mute_it_for_ever():
    """A clock that stepped forward once writes a moment that no restart would
    ever correct, because the baseline is only written when it is absent."""
    from bot.webhooks import SILENCE_AFTER

    ahead = (datetime.now(timezone.utc) + timedelta(days=5)).strftime(
        "%Y-%m-%d %H:%M:%S")
    told = _watch(_journal(watching=ahead), rounds=2,
                  advance=SILENCE_AFTER + timedelta(hours=1))
    assert told, "a stamp from the future silenced it permanently"


def test_a_dead_channel_is_not_reported_every_hour():
    """The watchdog polls hourly and the channel it watches stays dead until
    somebody goes and looks, so without a gate of its own it would send the
    same alert every hour — which is how an alert gets muted, and a muted alert
    is indistinguishable from coverage.

    This is what REALERT_AFTER is for. It sat in this module unread for its
    first weeks, and nothing failed: every other test here runs a single poll,
    and a single poll cannot tell a gate from no gate.
    """
    from datetime import timedelta

    told = _watch(_journal(arrived=_ago(30), watching=_ago(30)), rounds=5,
                  advance=timedelta(hours=1))
    assert len(told) == 1, f"said it {len(told)} times in five hours"


def test_it_says_it_again_after_the_reminder_interval():
    """Once and then never again is the other way to lose an alert."""
    from datetime import timedelta

    from bot.webhooks import REALERT_AFTER

    told = _watch(_journal(arrived=_ago(30), watching=_ago(30)), rounds=2,
                  advance=REALERT_AFTER + timedelta(minutes=1))
    assert len(told) == 2


def test_the_day_count_agrees_with_the_stamp_printed_beside_it():
    """Whether to speak is measured from the later of the last arrival and the
    moment this bot first started watching. What gets *said* is the whole gap,
    because the last-arrival stamp is printed right next to the number, and a
    number that contradicts the stamp beside it teaches the reader to believe
    neither.

    The live alert of 2026-09-16 said three days next to a stamp four days old:
    three was the age of the deploy.
    """
    told = _watch(_journal(arrived=_ago(10), watching=_ago(4)))
    assert told, "ten days of silence went unreported"
    assert "for 10 day(s)" in told[0], told[0]


def test_a_request_with_a_wrong_signature_still_counts_as_an_arrival():
    """Load-bearing, and a mutation caught it missing: moving the mark below
    the signature check passed every other test here.

    The watchdog asks one question — does anything from the internet reach this
    process. A forged request answers it just as well as a genuine one, and
    counting only signed requests would make "nobody is calling" and "somebody
    is calling and being refused" look identical. Those have different causes:
    the first is a route or a missing webhook, the second is a rotated signing
    key."""
    arrivals: list[dict] = []
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue(),
                    arrived=arrivals.append)
    body = _points_body(points_diff=22)

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            r = await client.post(PATH, data=body,
                                  headers={"rivo-signature": "wrong"})
            return r.status

    assert asyncio.run(go()) == 401
    assert len(arrivals) == 1, (
        "a refused request is still proof the path reaches this process")


def _arrivals_for(method: str, *, headers=None, data=None):
    """Send one request to the secret path and return (status, arrivals)."""
    seen: list[dict] = []
    app = build_app(path=PATH, secret=SECRET, chats=_Chats(),
                    languages=_Languages(), queue=_Queue(),
                    arrived=seen.append)

    async def go() -> int:
        async with TestClient(TestServer(app)) as client:
            r = await client.request(method, PATH, data=data,
                                     headers=headers or {})
            return r.status

    return asyncio.run(go()), seen


def test_a_get_on_the_secret_path_counts_and_is_not_a_missing_route():
    """405 is the answer the neighbouring project's application gives when our
    route is gone from their nginx — that is how the route was found missing on
    11.09. Our own framework answered a GET on the right path with the same 405
    and recorded nothing, so the one number the diagnosis turns on meant two
    opposite things. Now the request is counted before the method is judged."""
    status, seen = _arrivals_for("GET")
    assert status == 405
    assert len(seen) == 1, "a GET reached this process and left no trace"
    assert seen[0]["method"] == "GET"


def test_a_body_too_large_counts_as_an_arrival():
    """The size guard used to sit above the mark, so a body over the cap was a
    call that never happened as far as the watchdog was concerned — the one
    shape of real traffic most likely to be refused."""
    status, seen = _arrivals_for(
        "POST", data=b"x" * (MAX_BODY + 1),
        headers={"rivo-signature": "wrong"})
    assert status == 413
    assert len(seen) == 1
    assert seen[0]["bytes"] > MAX_BODY


def test_the_arrival_says_enough_to_tell_a_real_call_from_our_own():
    """An arrival row of `{}` cost a morning: the single arrival on record was
    our own verification curl, and proving it took the neighbouring project's
    nginx log — which we do not own and which rotates."""
    _, seen = _arrivals_for("POST", data=b"{}",
                            headers={"rivo-signature": "wrong",
                                     "User-Agent": "curl/8.7.1",
                                     "X-Real-IP": "172.18.0.1"})
    assert seen[0] == {"method": "POST", "signed": True, "bytes": 2,
                       "from_lan": True, "agent": "curl/8.7.1"}

    # A genuinely global address: Python counts the documentation ranges
    # (203.0.113.0/24 and friends) as private, so the obvious example address
    # would have made this assertion pass for the wrong reason.
    _, outside = _arrivals_for("POST", data=b"{}",
                               headers={"X-Real-IP": "8.8.8.8"})
    assert outside[0]["from_lan"] is False, (
        "a call from the internet must not look like a test from the box")
    assert outside[0]["signed"] is False


def test_the_caller_names_itself_and_the_name_is_kept_short():
    """The address does not settle it: a check run on the server still goes out
    to the public URL and comes back through nginx, so it arrives from the box's
    own public address like any real call. Measured on the probe of 16.09, which
    is why this field exists. The agent string is never the same."""
    _, seen = _arrivals_for("POST", data=b"{}",
                            headers={"User-Agent": "x" * (MAX_AGENT + 40)})
    assert seen[0]["agent"] == "x" * MAX_AGENT, "a label, not a log"


def test_an_unknown_caller_address_is_neither_lan_nor_internet():
    """Absent or unparseable, it must not silently read as one of the two: the
    whole value of the field is that it separates them."""
    _, seen = _arrivals_for("POST", data=b"{}")
    assert seen[0]["from_lan"] is None


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
