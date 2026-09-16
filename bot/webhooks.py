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

import binascii
import hashlib
import hmac
import asyncio
import ipaddress
import json
import re
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from aiogram import Bot
from aiohttp import web
from loguru import logger

from bot.alerts import tell_admins

from core.adapters.rivo.parse import is_unexplained, parse_event
from core.ports.outbox import MessageQueue
from core.ports.users import ChatsByEmail, GenderForm, LanguageChoice
from core.usecases.loyalty import announce

SIGNATURE_HEADER = "rivo-signature"

# What Rivo actually sends, as opposed to what its documentation says. Measured
# on 2026-09-16 13:11 UTC from the header names of its own test webhooks: no
# `rivo-signature` at all, and instead a family that follows the Standard
# Webhooks convention (an id, a timestamp, a signature, a topic, an API version),
# which none of Rivo's public pages describe. Both are read; see `_verify`.
WEBHOOK_SIGNATURE_HEADER = "rivo-webhook-signature"
WEBHOOK_ID_HEADER = "rivo-webhook-id"
WEBHOOK_TIMESTAMP_HEADER = "rivo-webhook-timestamp"
WEBHOOK_TOPIC_HEADER = "rivo-webhook-topic"

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

# The two moments the watchdog below keeps for itself, written to the same
# journal and read back the same way. They live in the database rather than in
# the process because both of them are older than any one process: a deploy
# lands several times on a busy day, and a watchdog whose memory starts over
# with each one is a watchdog for a bot nobody is working on.
#
# `WATCHING` is written exactly once, the first time this runs against a
# database. `ALERTED` is written each time the admins are told.
WATCHING = "rivo_watch_started"
ALERTED = "rivo_silence_alerted"

# How long the silence has to last before the admins hear about it. Rivo
# publishes about thirty kinds of event for every customer of the shop, so days
# of nothing is not a quiet week — it is a channel that is not connected.
#
# The number to beat is eleven: that is how long the route was gone in
# September 2026 before anybody noticed, and nothing anywhere said a word. The
# endpoint answered, the container was healthy, the deploy was green, and every
# request was being handed to somebody else's application and answered 405.
SILENCE_AFTER = timedelta(days=3)

# And how long before it says so again. A channel nobody is calling stays that
# way until somebody goes and looks, so the reminder is weekly rather than
# hourly: an alert repeating faster than anybody can act on it is how alerts get
# muted, and a muted alert looks exactly like coverage. Same reasoning as
# `bot/sync.py`, a longer interval because a dead webhook is not an outage that
# resolves itself.
REALERT_AFTER = timedelta(days=7)
WATCHDOG_INTERVAL_SECONDS = 60 * 60
_SAFE_NAME = re.compile(r"[^a-z0-9_.-]+")


# String values kept as they are in a sample: enumerations and moments, which
# describe the event rather than anybody in it. Everything else that is text is
# replaced by its length. `*_at` keys are kept too — a timestamp is shape.
_KEPT_STRINGS = frozenset({
    "event_type", "source", "status", "state", "kind", "type", "currency",
    "loyalty_status",
})


def _shape_only(value: object, key: str = "") -> object:
    """A value with every piece of text in it reduced to its length.

    Recursive, because the bodies that most need recording are the ones whose
    people are not where the parser looks: Rivo's real `referral/completed` has
    no top-level `customer`, and whoever referred and whoever was referred sit
    somewhere else. A redaction that only knew `customer` would have written
    both of them to disk verbatim.
    """
    if isinstance(value, dict):
        return {k: _shape_only(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape_only(v, key) for v in value]
    if isinstance(value, str):
        if key in _KEPT_STRINGS or key.endswith("_at"):
            return value
        return f"<str:{len(value)}>"
    return value


def _redacted(payload: object) -> object:
    """The body with everything but the shape taken out of it.

    `customer` is rebuilt rather than filtered: a denylist protects against the
    fields we thought of, and Rivo is free to add one we did not. The parser
    reads `email`, `points_tally` and `loyalty_status`; the first is replaced and
    the other two are kept, because whether they arrive as strings or numbers is
    exactly the kind of thing a fixture is for.

    Everything else goes through `_shape_only`: numbers, nulls and structure
    stay, text becomes its length unless it is an enumeration or a moment.
    """
    if not isinstance(payload, dict):
        return payload
    out = {k: _shape_only(v, str(k)) for k, v in payload.items() if k != "customer"}
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


def _keep_a_sample(directory: Path, payload: object) -> Path | None:
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
            return None
        if len(list(directory.glob("*.json"))) >= MAX_SAMPLES:
            return None
        target.write_text(json.dumps(_redacted(payload), ensure_ascii=False,
                                     indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        logger.info("Kept a redacted Rivo sample at {} — copy it into "
                    "tests/fixtures/rivo/ and the parser finally has a real "
                    "body to run on", target)
        return target
    except Exception as exc:  # noqa: BLE001 — a sampler may never cost a message
        logger.debug("Could not keep a Rivo sample: {}", exc)
    return None


# Inside the container only. Nothing publishes it; nginx on the host is what
# the internet reaches, and it proxies here by container name.
PORT = 8081
# Their limit is far below this; the cap is here so a body that is not a
# webhook at all is dropped before it is read into memory.
MAX_BODY = 64 * 1024

# How much of the caller's User-Agent is kept on an arrival row. Enough to tell
# `curl/8.7.1` from whatever Rivo calls itself, and not enough to be a log.
MAX_AGENT = 32


def _secrets(raw: str) -> tuple[str, ...]:
    """The signing keys in `RIVO_WEBHOOK_SECRET`.

    Plural because Rivo's documentation says every webhook has its own Secret
    Token. The cabinet itself, opened the same day, showed one key for the whole
    account ("Your webhooks will be signed with …"), so in practice this holds
    one entry. The list stays: it costs nothing, and it is also how a key is
    rotated without a gap — the new one alongside the old until Rivo switches.

    Comma-separated. **Blank entries are dropped, and that is a security rule,
    not tidiness:** an HMAC with an empty key is something anybody can compute,
    so a trailing comma left in the file must never become a key that verifies.
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _keys(secret: str) -> list[tuple[str, bytes]]:
    """Every reasonable reading of one secret as HMAC key bytes, labelled.

    Rivo's cabinet shows the key as 64 hex characters, and nothing says how its
    signer reads them: as the text itself (what the old documented scheme did),
    as the 32 bytes the hex spells, or base64-decoded the way Standard Webhooks
    libraries read a `whsec_` secret. Each reading is still the secret — none of
    them is a key anybody else holds.
    """
    keys = [("text", secret.encode("utf-8"))]
    try:
        keys.append(("hex", bytes.fromhex(secret)))
    except ValueError:
        pass
    try:
        keys.append(("base64", b64decode(secret.removeprefix("whsec_"),
                                         validate=True)))
    except (ValueError, binascii.Error):
        pass
    return [(label, key) for label, key in keys if key]


def _offered(value: str) -> list[str]:
    """The signatures a header value carries, with any scheme labels removed.

    Covers the three spellings in use: a bare signature, Standard Webhooks'
    space-separated `v1,<sig>` list, and Stripe's `t=…,v1=<sig>`. A base64
    signature ends in `=` padding, so a piece is only read as `label=value`
    when the label is one of the known ones.
    """
    found = [value.strip()]
    for token in value.split():
        for piece in token.split(","):
            label, sep, rest = piece.partition("=")
            if sep and label in ("v1", "v1a", "s", "sig", "signature"):
                found.append(rest)
            elif piece not in ("v1", "v1a") and not (sep and label == "t"):
                found.append(piece)
    return [f for f in dict.fromkeys(found) if f]


def _verify(secrets: tuple[str, ...], body: bytes, signature: str,
            webhook_id: str, timestamp: str) -> str | None:
    """Which signing scheme this request satisfies, or None for none of them.

    **Why several.** Rivo documents one scheme — base64 HMAC-SHA256 of the raw
    body under `rivo-signature` — and on 2026-09-16 sent something else: a
    `rivo-webhook-signature` beside an id and a timestamp, the Standard Webhooks
    pattern, which usually signs `id.timestamp.body` and not the body alone. Its
    exact construction is written down nowhere public, and the one honest way to
    find it without handling the key is to let the process that already holds
    the key try the plausible constructions and say which one matched.

    **Why that is not weaker.** Every candidate is HMAC-SHA256 keyed with the
    configured secret over content that contains the whole body. Forging any of
    them needs the key; offering several only multiplies a forger's odds by the
    number of candidates, against 2^256. The match is reported by label so that
    this can later be narrowed to the one scheme Rivo actually uses.

    Every combination is computed every time, with no early return: which one
    matched is none of a caller's business, and the clock would tell it.
    """
    offered = _offered(signature)
    contents = [("body", body)]
    if timestamp:
        contents.append(("ts.body", timestamp.encode() + b"." + body))
        if webhook_id:
            contents.append(("id.ts.body", webhook_id.encode() + b"."
                             + timestamp.encode() + b"." + body))
    scheme = None
    for secret in secrets:
        for key_label, key in _keys(secret):
            for content_label, content in contents:
                digest = hmac.new(key, content, hashlib.sha256).digest()
                for encoding, expected in (("base64", b64encode(digest).decode()),
                                           ("hex", digest.hex())):
                    for candidate in offered:
                        if hmac.compare_digest(expected.encode(),
                                               candidate.encode()) and scheme is None:
                            scheme = f"key={key_label} content={content_label} {encoding}"
    return scheme


def _shape(signature: str) -> str:
    """What a signature looks like, with nothing of what it says.

    For the log line of a refusal: enough to recognise a scheme — its length, a
    `v1,` or `t=` label, whether it is hex — and not one character of the value.
    Labels come from a fixed list and are never copied out of the header: a
    base64 signature can itself begin with `v1`.
    """
    if not signature:
        return "absent"
    labels = set()
    for token in signature.split():
        for piece in token.split(","):
            if piece in ("v1", "v1a"):
                labels.add(piece + ",")
            elif piece.startswith("v1="):
                labels.add("v1=")
            elif piece.startswith("t="):
                labels.add("t=")
    hexish = all(c in "0123456789abcdefABCDEF" for c in signature)
    return (f"{len(signature)} chars, {len(signature.split())} part(s), "
            f"labels={sorted(labels) or 'none'}, hex={hexish}")


def _detail(request: web.Request) -> dict:
    """Small, countable facts about one request — never the body, never who.

    The arrival row used to carry `{}`, and a row that says only "somebody
    called" cannot answer the question it exists for. On 2026-09-16 the single
    arrival on record turned out to be our own verification curl from eight
    minutes after the route was restored, and establishing that took the
    neighbouring project's nginx log, which we do not own and which rotates.

    `agent` is the field that actually settles it, and it was added after the
    first one did not. A verification curl run on the server still goes out to
    the public URL and comes back through nginx, so it arrives from the box's
    own public address and is indistinguishable from a real call by address
    alone — measured, on the probe of 2026-09-16 07:00. The user agent is not:
    `curl/8.x` and whatever Rivo sends are never the same string. Truncated,
    because this is a label, not a log.

    `from_lan` is kept for the other half of the question — whether the request
    reached nginx from inside the machine at all, which is what a request to the
    container rather than to the domain looks like. No address is stored, only
    which of the two it was: a fact about the channel rather than about a person.
    """
    seen_from = request.headers.get("X-Real-IP", "")
    try:
        from_lan = ipaddress.ip_address(seen_from).is_private
    except ValueError:
        from_lan = None
    return {
        "method": request.method,
        "signed": (SIGNATURE_HEADER in request.headers
                   or WEBHOOK_SIGNATURE_HEADER in request.headers),
        "bytes": request.content_length or 0,
        "from_lan": from_lan,
        "agent": (request.headers.get("User-Agent") or "")[:MAX_AGENT],
    }


def build_app(
    *,
    path: str,
    secret: str,
    chats: ChatsByEmail,
    languages: LanguageChoice,
    queue: MessageQueue,
    forms: GenderForm | None = None,
    account_url: str = "",
    sample_dir: Path | None = None,
    arrived: Callable[[dict], None] | None = None,
    kept: Callable[[str], None] | None = None,
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
    being refused". It is handed the small dict `_detail` builds, so that a row
    in the log can later be told apart from our own curl. A callable rather than
    a repository, for the reason at the top of this file: this module knows
    nothing about databases.

    `kept` is called with the path of a sample that was just written, once per
    shape. It exists because a file on a server that nobody is told about is
    not a fixture, and collecting it is the entire point.
    """

    secrets = _secrets(secret)
    # How many, never which: the one line that lets somebody who just edited
    # the file confirm the bot read all of it.
    logger.info("Rivo webhook accepts {} signing key(s)", len(secrets))

    async def handle(request: web.Request) -> web.Response:
        # First line of the function, deliberately, and above every check
        # including the size guard and the method. A forged, oversized or
        # wrong-method request is still proof that the path from the internet
        # reaches this process, and that is the only thing the watchdog is
        # asking about. The size guard used to sit above this, which made a
        # body too large indistinguishable from a call that never came.
        if arrived is not None:
            arrived(_detail(request))

        if request.method != "POST":
            # This route is registered for every method on purpose. A GET here
            # used to be answered by aiohttp's own 405 without ever reaching
            # this function, leaving no trace anywhere — and 405 is exactly what
            # the neighbouring project's FastAPI answers when our route is
            # missing, which is how a 405 was read on 11.09. Those two must not
            # look the same from the outside.
            return web.Response(status=405, text="post only")

        if request.content_length and request.content_length > MAX_BODY:
            return web.Response(status=413, text="too large")

        body = await request.read()
        received = (request.headers.get(WEBHOOK_SIGNATURE_HEADER)
                    or request.headers.get(SIGNATURE_HEADER, ""))
        scheme = _verify(secrets, body, received,
                         request.headers.get(WEBHOOK_ID_HEADER, ""),
                         request.headers.get(WEBHOOK_TIMESTAMP_HEADER, "")
                         ) if received else None
        if scheme is None:
            # Deliberately terse and deliberately 401: an attacker learns
            # nothing about which half was wrong. The log is another matter —
            # it is read by whoever has to fix this, and they need both.
            #
            # Header names and the signature's shape, never a value. Names are
            # how Rivo's undocumented `rivo-webhook-signature` was found; the
            # shape says which scheme a mismatch is written in.
            logger.warning(
                "Rivo webhook refused, from {}: {}; signature shape: {}; "
                "headers sent: {}",
                request.remote,
                "no signature header" if not received else "signature did not match",
                _shape(received),
                ", ".join(sorted({name.lower() for name in request.headers})))
            return web.Response(status=401, text="bad signature")
        topic = request.headers.get(WEBHOOK_TOPIC_HEADER, "")
        logger.info("Rivo webhook verified ({}), topic {}", scheme, topic or "-")

        try:
            payload = await request.json()
        except ValueError:
            return web.Response(status=400, text="bad json")

        # **The event type is in a header, not in the body.** Rivo's documented
        # payloads carry `event_type` at the top level, and the parser reads it
        # there. The first real body Rivo ever sent, on 2026-09-16 13:42 UTC,
        # had twenty-one top-level keys and no `event_type` among them; the
        # type came as `rivo-webhook-topic`. Without this line every event was
        # "unknown" and ignored — the channel verified, answered 200, and never
        # told a single customer anything. That body is now a fixture.
        #
        # Only filled in when absent: a body that names its own type is the
        # older API version speaking, and it is the more specific of the two.
        if isinstance(payload, dict) and not payload.get("event_type") and topic:
            payload["event_type"] = topic

        # Kept before anything is decided, because the shape most worth having
        # is not the one that comes back as None. A points event with no signed
        # amount parses into a perfectly good event that `announce` then drops,
        # and a sampler watching only for None would never see it. The parser
        # says which bodies are unexplained; this asks.
        if sample_dir is not None and is_unexplained(payload):
            written = _keep_a_sample(sample_dir, payload)
            # Said out loud, because a file nobody knows about is not a
            # fixture. The whole point of the sampler is that somebody copies
            # what it caught into tests/fixtures/rivo/, and a log line on a
            # server is not how that happens.
            if written is not None and kept is not None:
                kept(str(written))

        event = parse_event(payload)
        if event is None:
            # Two thirds of what Rivo publishes is not for a customer. 200,
            # because a webhook that answers with an error gets retried, and
            # this one has nothing to retry.
            return web.Response(text="ignored")

        await announce(event, chats, languages, queue, forms=forms,
                       account_url=account_url)
        return web.Response(text="ok")

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    # Every method, not just POST: see the note in `handle`. What is watched is
    # whether anything reaches this process at all.
    app.router.add_route("*", path, handle)
    # Not on the secret path: something has to be pingable without knowing it.
    app.router.add_get("/health", health)
    return app


async def watch_for_silence(
    bot: Bot, admin_ids: list[int],
    when: Callable[[str], Awaitable[str | None]],
    note: Callable[..., None],
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
    The parity stops at the idea, though: that watchdog still keeps both of its
    own moments in the process, which is nearly harmless there because its
    threshold is fifteen minutes and no deploy outlives one.

    **Both of its own moments outlive the process.** Silence is measured from
    whichever is later, the last arrival or the moment this bot first started
    watching — and that second moment used to be the start of the *process*,
    which quietly made the watchdog useless on exactly the weeks somebody was
    working: three days without a single deploy is not a week of active
    development, so the count reset before it could ever reach the threshold.
    The one alert it has ever sent came after four quiet days in September. The
    moment is now written down on the first run and read back on every later
    one; likewise the moment of the last alert, without which a redeploy would
    hand the admins the same message again an hour later.

    The cost of that, stated plainly: if the bot itself is off for a week, the
    gap it reports afterwards is the true gap since anybody called, not the part
    of it that this bot could have heard. That is the right way round. A channel
    nobody called while we were not listening is still a channel nobody called,
    and the alternative is what we had — an alarm that resets itself whenever
    anyone touches the repository.

    `when` answers "when did this named moment last happen"; `note` records that
    it has happened now. Two callables rather than a repository, for the reason
    at the top of this file: this module knows nothing about databases. They are
    the same pair the arrival counter uses, so the journal is one table and one
    spelling of a timestamp.

    It never raises. An alert that takes down the loop that noticed turns one
    broken thing into two.
    """
    if not admin_ids:
        logger.warning("No admin ids configured — a dead webhook will be silent")
        return

    # The one read on the startup path, and it is guarded: everything below
    # promises never to raise, and a watchdog that dies on a locked database
    # while the loop it protects never starts is the worst of both.
    unwritten = False
    try:
        raw = await when(WATCHING)
        since = _read_stamp(raw)
        # Absent and unreadable are different facts, and only the first of them
        # invites a write. `_read_stamp` folds both to None on purpose — for the
        # arrival, where "cannot read it" must alert rather than reassure — so
        # the raw value is what decides here.
        unwritten = raw is None
    except Exception as exc:  # noqa: BLE001 — see above
        logger.warning("Rivo watchdog could not read its baseline: {}", exc)
        since = None
    if since is None:
        # A database this has never run against — a new install, or a restored
        # volume. Start the clock now, so that a fresh bot does not open with an
        # alert about the days before it existed.
        since = datetime.now(timezone.utc)
        # Written down only when the journal is known to be empty. `when` reads
        # MAX(created_at), so a second row does not sit harmlessly beside the
        # first — it moves the baseline forward, which is the very bug this is
        # here to fix. A read that merely failed leaves the journal alone and
        # lets the next process find the real moment.
        if unwritten:
            note(WATCHING)
    # A baseline in the future would mute this for as long as the clock took to
    # catch up, and because the moment is only ever written when absent, no
    # restart would correct it. Clamped here rather than in the loop on purpose:
    # pinning it once gives a fixed moment the gap can grow from, while clamping
    # every poll would hold the gap at zero forever, which is the same mute
    # wearing a different hat.
    since = min(since, datetime.now(timezone.utc))
    logger.info("Rivo webhook watchdog started (watching since {}, alerts "
                "after {})", since, SILENCE_AFTER)

    # The journal is the memory that survives a deploy; this is the one that
    # cannot be lost. `note` is fire-and-forget and swallows its own write
    # errors, so a failed write would otherwise turn the weekly reminder back
    # into an hourly one — the exact spam this whole change exists to end, and
    # a regression against the local variable the journal replaced. Both, and
    # the later of the two wins.
    spoke_at: datetime | None = None

    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            now = datetime.now(timezone.utc)
            stamp = await when(ARRIVED)
            seen_at = _read_stamp(stamp)
            quiet_since = max(seen_at, since) if seen_at else since
            quiet_for = now - quiet_since
            if quiet_for < SILENCE_AFTER:
                continue

            alerted_at = _read_stamp(await when(ALERTED))
            if spoke_at and (alerted_at is None or spoke_at > alerted_at):
                alerted_at = spoke_at
            # The reminder interval must not span two different outages. If
            # anything called after the last alert, the channel came back and
            # died again, and that second death is news — waiting out the rest
            # of the week would swallow exactly the pattern the alert text
            # tells the reader to expect.
            spoke_since_then = seen_at is not None and (
                alerted_at is None or seen_at > alerted_at)
            if (alerted_at is not None and not spoke_since_then
                    and now - alerted_at < REALERT_AFTER):
                continue
            # The number told to the admins is the whole gap since the last
            # arrival, not the part of it that falls after this bot first
            # started watching. `quiet_for` decides whether to speak; but the
            # stamp is printed right next to the number, and a number that
            # disagrees with the stamp beside it teaches the reader to believe
            # neither.
            reported = (now - seen_at) if seen_at else quiet_for
            delivered = await tell_admins(
                bot, admin_ids,
                "🔌 <b>Nothing has called the loyalty webhook</b> for "
                f"{reported.days} day(s)"
                + (f" (last arrival {stamp} UTC)." if stamp
                   else " — and nothing ever has.")
                + "\n\nRivo publishes constantly, so this is not a quiet week. "
                "Two things to check, in this order: that the route still exists "
                "in the neighbouring project's nginx (their deploy has silently "
                "dropped it three times — 06.09, 12.09 and 16.09 — and the first "
                "one cost eleven days), and that the webhooks are still created "
                "in Rivo under Settings → Webhooks.\n\n"
                "A request with a wrong signature would still count as an "
                "arrival, so nothing reached this process at all — it is not a "
                "call being refused. One caveat before you touch anybody's "
                "nginx: if this bot was itself down for part of that window, "
                "some of the silence is ours.")
            if not delivered:
                # Nobody heard it, so it did not happen. Recording it anyway
                # would buy silence for a week on the strength of a message
                # that reached no one — and unlike before, that mistake would
                # now outlive the restart that used to clear it.
                logger.warning("Rivo silence alert reached no admin; will try "
                               "again next round")
                continue
            spoke_at = now
            # What it knew when it spoke. The row's existence is the fact the
            # suppression reads, but a bare `{}` is what made the arrival row
            # useless in September, and this one is read by a human wondering
            # why the reminder did or did not come.
            note(ALERTED, days=reported.days, ever=seen_at is not None)
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
