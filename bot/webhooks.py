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
from base64 import b64encode

from aiohttp import web
from loguru import logger

from core.adapters.rivo.parse import parse_event
from core.ports.outbox import MessageQueue
from core.ports.users import ChatsByEmail, LanguageChoice
from core.usecases.loyalty import announce

SIGNATURE_HEADER = "rivo-signature"
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
) -> web.Application:
    """The aiohttp app with one route on it.

    Everything it needs is passed in: this module knows Rivo's signature and
    Telegram's absence, and nothing about databases.
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
