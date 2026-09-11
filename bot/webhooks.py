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
import asyncio
import json
import re
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from aiogram import Bot
from aiohttp import web
from loguru import logger

from bot.alerts import tell_admins_once

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

# The event `track()` writes on every arrival, and the one the watchdog below
# looks for. Recorded before the signature is checked on purpose: a forged
# request still proves the path from the internet reaches this process, which is
# the only thing being watched for.
ARRIVED = "rivo_webhook_arrived"

# How long the silence has to last before the admins hear about it. Rivo
# publishes about thirty kinds of event for every customer of the shop, so days
# of nothing is not a quiet week — it is a channel that is not connected.
#
# The number to beat is eleven: that is how long the route was gone in
# September 2026 before anybody noticed, and nothing anywhere said a word. The
# endpoint answered, the container was healthy, the deploy was green, and every
# request was being handed to somebody else's application and answered 405.
SILENCE_AFTER = timedelta(days=3)
REALERT_AFTER = timedelta(days=7)
WATCHDOG_INTERVAL_SECONDS = 60 * 60
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
    arrived: Callable[[], None] | None = None,
) -> web.Application:
    """The aiohttp app with one route on it.

    Everything it needs is passed in: this module knows Rivo's signature and
    Telegram's absence, and nothing about databases.

    `sample_dir` is where a body we could not fully act on is written down, and
    None turns that off. Off is the honest default for a library-shaped
    function: a module that starts writing files because somebody imported it
    is a module nobody can test twice.

    `arrived` is called once per request, before anything is checked, so the
    watchdog below can tell "nobody is calling" from "somebody is calling and
    being refused". A callable rather than a repository, for the reason at the
    top of this file: this module knows nothing about databases.
    """

    async def handle(request: web.Request) -> web.Response:
        if request.content_length and request.content_length > MAX_BODY:
            return web.Response(status=413, text="too large")

        # Before the signature, deliberately. A forged request is still proof
        # that the path from the internet reaches this process, and that is the
        # only thing the watchdog is asking about.
        if arrived is not None:
            arrived()

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


async def watch_for_silence(
    bot: Bot, admin_ids: list[int],
    last_arrival: Callable[[], Awaitable[str | None]],
    *, started: datetime | None = None,
) -> None:
    """Tell the admins when nothing has called the webhook for days.

    **The failure this exists for is the one that looks like success.** In
    September 2026 the `/rivo/` route disappeared from the neighbouring
    project's nginx when that config was rewritten, and every request was
    proxied to their application and answered 405. For eleven days the endpoint
    was up, the container was healthy, the deploys were green, and the loyalty
    channel was dead. Nothing in this system was capable of noticing, because
    everything it monitors was working.

    So what is watched is not an error — there is no error to watch. It is
    **silence**, the same thing `bot/sync.py` watches for the order sweep and
    for the same reason: a channel that stopped being called raises nothing.

    `started` is the moment this process came up, and silence is measured from
    whichever is later, it or the last arrival. Without it a fresh deploy would
    alert immediately about the days before it was running, which is not news
    and is how an alert gets muted.

    It never raises. An alert that takes down the loop that noticed turns one
    broken thing into two.
    """
    if not admin_ids:
        logger.warning("No admin ids configured — a dead webhook will be silent")
        return

    since = started or datetime.now(timezone.utc)
    logger.info("Rivo webhook watchdog started (alerts after {})", SILENCE_AFTER)

    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            now = datetime.now(timezone.utc)
            stamp = await last_arrival()
            seen_at = _read_stamp(stamp)
            quiet_since = max(seen_at, since) if seen_at else since
            quiet_for = now - quiet_since
            if quiet_for < SILENCE_AFTER:
                continue
            await tell_admins_once(
                bot, admin_ids, "rivo-silent",
                "🔌 <b>Nothing has called the loyalty webhook</b> for "
                f"{quiet_for.days} day(s)"
                + (f" (last arrival {stamp} UTC)." if stamp
                   else " — and nothing ever has.")
                + "\n\nRivo publishes constantly, so this is not a quiet week. "
                "Two things to check, in this order: that the route still exists "
                "in the neighbouring project's nginx (it was silently removed "
                "once, and cost eleven days), and that the webhooks are still "
                "created in Rivo under Settings → Webhooks.\n\n"
                "A request with a wrong signature would count as an arrival, so "
                "this means nobody is calling at all.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the watcher must outlive a bad poll
            logger.warning("Rivo watchdog poll failed: {}", exc)


def _read_stamp(raw: str | None) -> datetime | None:
    """SQLite writes "2026-09-11 15:18:16"; anything else is treated as absent.

    Tolerant on purpose: a stamp this cannot read must read as "no arrival",
    which alerts, rather than as "now", which would silence the watchdog on
    exactly the day its input changed shape.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "")).replace(
            tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Rivo watchdog could not read a timestamp: {!r}", raw)
        return None


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
