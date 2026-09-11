"""The one door into this bot from outside Telegram.

Rivo posts a JSON body when something happens to somebody's loyalty account.
Everything else the bot does it starts itself: long polling asks Telegram for
updates, the sweeps ask the CRM. This is the only thing that arrives uninvited,
and it is treated accordingly — signature first, then a shape, then a person,
and nothing at all if any of the three is missing.

Two locks rather than one. The path carries a secret segment, so the endpoint
cannot be found by scanning; and the body carries an HMAC signature, so a
request to a known path still cannot be forged. Either alone would be thin: a
URL leaks through logs and proxies, and a signing key can end up on somebody's
screenshot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from base64 import b64encode
from pathlib import Path

from aiohttp import web
from loguru import logger

from core.adapters.rivo.parse import is_unexplained, parse_event
from core.ports.outbox import MessageQueue
from core.ports.users import ChatsByEmail, LanguageChoice
from core.usecases.loyalty import announce

SIGNATURE_HEADER = "rivo-signature"

# Where a body we could not fully use is written down, so that the next real one
# becomes a fixture instead of a memory.
#
# **Why this exists at all.** Rivo is the only adapter in this repo with no
# recorded payload — keycrm, novaposhta and shopify all have saved responses
# under tests/fixtures, and their tests run on what the service actually sends.
# Rivo's tests run on bodies assembled from its documentation, which is how
# `points_diff: null` went unnoticed: nobody had ever seen a real one. Fetching
# them by hand means somebody logging into Rivo at the right moment; this way
# the first event that arrives and puzzles us records itself.
#
# **What is kept and what is not.** Only the shape. `customer` is rebuilt from
# the two fields the parser reads, so an email, a name and a phone number never
# reach the disk — a fixture is meant to be committed, and a body with a
# customer's address in it could not be. One file per event type, never
# overwritten, capped: this is a sampler, not a log.
MAX_SAMPLES = 30
_SAFE_NAME = re.compile(r"[^a-z0-9_.-]+")


def _redacted(payload: object) -> object:
    """The body with everything but the shape taken out of it.

    `customer` is rebuilt rather than filtered: a denylist protects against the
    fields we thought of, and Rivo is free to add one we did not. The parser
    reads `email`, `points_tally` and `loyalty_status`; the first is replaced and
    the other two are kept, because whether they arrive as strings or numbers is
    exactly the kind of thing a fixture is for.
    """
    if not isinstance(payload, dict):
        return payload
    out = {k: v for k, v in payload.items() if k != "customer"}
    customer = payload.get("customer")
    if isinstance(customer, dict):
        out["customer"] = {
            "email": "redacted@example.com",
            "points_tally": customer.get("points_tally"),
            "loyalty_status": customer.get("loyalty_status"),
            "_redacted_keys": sorted(k for k in customer if k not in
                                     ("email", "points_tally", "loyalty_status")),
        }
    return out


def _keep_a_sample(directory: Path, payload: object) -> None:
    """Write one redacted body per event type, and never let it cost a delivery.

    The directory is an argument, like everything else this module needs. It
    was read straight out of the environment for one commit, and a smoke test
    caught that within the minute: §12.7 gives the environment exactly one
    reader, `core/config.py`, and this would have been the second. Deriving it
    from where the database lives was the other temptation, and this module's
    own docstring rules that out — it knows Rivo's signature and Telegram's
    absence, and nothing about databases.

    Everything is swallowed: this runs on the path of a real customer's
    notification, and a full disk or a read-only mount must not turn a webhook
    that Rivo would stop retrying into a 500.
    """
    try:
        kind = ""
        if isinstance(payload, dict):
            kind = str(payload.get("event_type") or "unknown")
        name = _SAFE_NAME.sub("_", kind.lower()) or "unknown"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{name}.json"
        if target.exists():
            return
        if len(list(directory.glob("*.json"))) >= MAX_SAMPLES:
            return
        target.write_text(json.dumps(_redacted(payload), ensure_ascii=False,
                                     indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        logger.info("Kept a redacted Rivo sample at {} — copy it into "
                    "tests/fixtures/rivo/ and the parser finally has a real "
                    "body to run on", target)
    except Exception as exc:  # noqa: BLE001 — a sampler may never cost a message
        logger.debug("Could not keep a Rivo sample: {}", exc)


# Inside the container only. Nothing publishes it; nginx on the host is what
# the internet reaches, and it proxies here by container name.
PORT = 8081
# Their limit is far below this; the cap is here so a body that is not a
# webhook at all is dropped before it is read into memory.
MAX_BODY = 64 * 1024


def _signature_matches(secret: str, body: bytes, received: str) -> bool:
    """HMAC-SHA256 of the raw body, base64, compared in constant time.

    The raw bytes, never a re-encoded parse of them: Rivo signs what they sent,
    and `json.dumps` of the parsed body differs from it by a space.
    """
    expected = b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, received)


def build_app(
    *,
    path: str,
    secret: str,
    chats: ChatsByEmail,
    languages: LanguageChoice,
    queue: MessageQueue,
    account_url: str = "",
    sample_dir: Path | None = None,
) -> web.Application:
    """The aiohttp app with one route on it.

    Everything it needs is passed in: this module knows Rivo's signature and
    Telegram's absence, and nothing about databases.

    `sample_dir` is where a body we could not fully act on is written down, and
    None turns that off. Off is the honest default for a library-shaped
    function: a module that starts writing files because somebody imported it
    is a module nobody can test twice.
    """

    async def handle(request: web.Request) -> web.Response:
        if request.content_length and request.content_length > MAX_BODY:
            return web.Response(status=413, text="too large")

        body = await request.read()
        received = request.headers.get(SIGNATURE_HEADER, "")
        if not received or not _signature_matches(secret, body, received):
            # Deliberately terse and deliberately 401: an attacker learns
            # nothing about which half was wrong.
            logger.warning("Rivo webhook with a bad signature, from {}",
                           request.remote)
            return web.Response(status=401, text="bad signature")

        try:
            payload = await request.json()
        except ValueError:
            return web.Response(status=400, text="bad json")

        # Kept before anything is decided, because the shape most worth having
        # is not the one that comes back as None. A points event with no signed
        # amount parses into a perfectly good event that `announce` then drops,
        # and a sampler watching only for None would never see it. The parser
        # says which bodies are unexplained; this asks.
        if sample_dir is not None and is_unexplained(payload):
            _keep_a_sample(sample_dir, payload)

        event = parse_event(payload)
        if event is None:
            # Two thirds of what Rivo publishes is not for a customer. 200,
            # because a webhook that answers with an error gets retried, and
            # this one has nothing to retry.
            return web.Response(text="ignored")

        await announce(event, chats, languages, queue, account_url=account_url)
        return web.Response(text="ok")

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post(path, handle)
    # Not on the secret path: something has to be pingable without knowing it.
    app.router.add_get("/health", health)
    return app


async def serve(app: web.Application, port: int) -> None:
    """Run until cancelled. Bound to every interface inside the container,
    which is not the same as being on the internet: nothing publishes this
    port, and nginx in front of it is what the world can reach."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Webhook endpoint listening on :{}", port)
    try:
        # Sleep forever; the task is cancelled at shutdown like every other.
        import asyncio

        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
