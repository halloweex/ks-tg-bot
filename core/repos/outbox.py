"""The queue every proactive message passes through, and the rules of capture.

§6.1 is honest about what this buys and what it does not. It buys retries with a
backoff, respect for a 429, resumption after a restart and one place where
everything the bot sends on its own initiative can be seen. It does not buy
exactly-once delivery: `sendMessage` can succeed and the process die before
`sent_at` is written, and Telegram has no idempotency key. At-least-once with a
narrow window is the guarantee, and every decision below is about making that
window narrow rather than pretending it is closed.

**Capture, and why the transaction is short.** §6.3: claim rows in one quick
statement, commit, then send outside it. Holding a transaction open across an
HTTP request means holding it across a `retry_after` of thirty seconds, which is
how a queue turns into a pile of stuck locks.

**`attempts` grows at capture, not on failure.** A message that kills the
process never fails in any way the code can see, so counting failures would let
it be picked up forever. Counting captures means it reaches the attempt limit
and lands on the shelf instead.

**An expired lock is the signature of a crash.** A sender that knows a send
failed says so, and the row goes back with `locked_until` cleared and
`not_before` pushed out. Nobody clears the lock when the process dies, so a row
still holding an expired one is exactly the case where nobody knows whether the
message went out — and that is the only case where §6.2's per-type policy is
consulted: `retry` sends again, `review` stops and waits for a person.

**One sender, and the schema cannot enforce it.** Postgres would capture with
`FOR UPDATE SKIP LOCKED`; SQLite has neither, so the `UPDATE ... WHERE id IN
(SELECT ...)` below is only safe while exactly one process claims. That is
already the constraint the deferral of stage 3 rests on (§4.2, and the price
table in docs/postgres-migration.md), and it is pinned by a test rather than
left as a comment.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite

from core.domain.campaign import CampaignKey
from core.repos.base import connect

# How long a captured row stays claimed. §6.3 says two minutes: long enough for
# a send that waits out a short 429, short enough that a crashed process does
# not park its batch for the afternoon.
LOCK_FOR = timedelta(minutes=2)

# How many captures one message gets before it goes on the shelf. Five, because
# the failures worth retrying are transient by definition — a network blip, a
# rate limit — and anything surviving five attempts is a message that will never
# be sent, quietly costing a send slot every two minutes.
MAX_ATTEMPTS = 5

# What to do with a row whose lock expired without anybody releasing it (§6.2).
RETRY = "retry"    # a delivery status: a miss is worse than a duplicate
REVIEW = "review"  # a broadcast: a duplicate is worse than a miss

# How a parked row says the recipient is gone rather than the send being broken.
# A constant because two modules read it: the sender writes it, and the
# broadcast report counts it as "blocked" — which is a different number from
# "failed" for the person reading the summary.
GONE_PREFIX = "recipient gone:"

_STAMP = "%Y-%m-%d %H:%M:%S"


def _stamp(moment: datetime) -> str:
    return moment.strftime(_STAMP)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(
    chat_id: int,
    kind: str,
    campaign: CampaignKey,
    payload: dict,
    *,
    dedup_key: str | None = None,
    not_before: datetime | None = None,
    on_uncertain: str = RETRY,
) -> int | None:
    """Queue one message. Returns its id, or None if it was already queued.

    The campaign key is a required argument and typed, per §6.4 — a proactive
    message without one is a send that can be counted but not attributed, and
    the attribution cannot be added later because the button is already built.

    `dedup_key` is what makes a job safe to run twice: the second attempt writes
    nothing rather than queueing a second copy. It does not make *sending* safe
    to repeat; nothing does.
    """
    if on_uncertain not in (RETRY, REVIEW):
        raise ValueError(f"unknown policy {on_uncertain!r}, expected retry or review")

    async with connect() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO outbox "
            "(chat_id, type, campaign_key, payload, dedup_key, on_uncertain, not_before) "
            "VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
            (chat_id, kind, str(campaign), json.dumps(payload, ensure_ascii=False),
             dedup_key, on_uncertain,
             _stamp(not_before) if not_before else None),
        )
        await db.commit()
        return cursor.lastrowid if cursor.rowcount else None


async def enqueue_many(
    chat_ids: list[int],
    kind: str,
    campaign: CampaignKey,
    payload: dict,
    *,
    on_uncertain: str = RETRY,
    dedup_prefix: str | None = None,
) -> int:
    """Queue the same message for many people at once. Returns how many were new.

    One statement instead of one connection per recipient, because this is the
    broadcast path: twenty thousand rows through `enqueue` would be twenty
    thousand connections, and the admin is watching a spinner while it happens.

    The recipient list is the snapshot — whoever is in it at this moment is who
    this campaign is responsible for, and an opt-out afterwards does not remove
    a message already queued. That is the behaviour the job/target pair had
    before the outbox, kept deliberately.
    """
    if on_uncertain not in (RETRY, REVIEW):
        raise ValueError(f"unknown policy {on_uncertain!r}, expected retry or review")
    if not chat_ids:
        return 0

    body = json.dumps(payload, ensure_ascii=False)
    rows = [
        (chat_id, kind, str(campaign), body,
         f"{dedup_prefix}:{chat_id}" if dedup_prefix else None, on_uncertain)
        for chat_id in chat_ids
    ]
    async with connect() as db:
        cursor = await db.executemany(
            "INSERT OR IGNORE INTO outbox "
            "(chat_id, type, campaign_key, payload, dedup_key, on_uncertain) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()
        return cursor.rowcount or 0


async def campaign_stats(campaign_key: str) -> dict[str, int]:
    """How one campaign is doing: the numbers a person asked for it wants.

    `blocked` is split out of `failed` because they mean opposite things to
    whoever reads the summary — one is people who left, the other is sends that
    broke — and the pre-outbox broadcast report already made that distinction.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT "
            "  COUNT(*) FILTER (WHERE sent_at IS NOT NULL) AS sent, "
            "  COUNT(*) FILTER (WHERE failed_at IS NOT NULL "
            "                     AND last_error LIKE ?) AS blocked, "
            "  COUNT(*) FILTER (WHERE failed_at IS NOT NULL "
            "                     AND (last_error NOT LIKE ? OR last_error IS NULL)) "
            "    AS failed, "
            "  COUNT(*) FILTER (WHERE sent_at IS NULL AND failed_at IS NULL) AS waiting "
            "FROM outbox WHERE campaign_key = ?",
            (f"{GONE_PREFIX}%", f"{GONE_PREFIX}%", campaign_key),
        )
        row = await cursor.fetchone()
        return {"sent": row[0], "blocked": row[1], "failed": row[2], "waiting": row[3]}


async def claim(limit: int = 50, *, now: datetime | None = None) -> list[dict]:
    """Take up to `limit` due messages and lock them. §6.3's capture.

    Two statements rather than one `RETURNING`: SQLite has had RETURNING since
    3.35 but aiosqlite's cursor does not expose the rows of an UPDATE, and the
    pair is atomic enough under the one-sender rule this whole module rests on.

    The order is `not_before`, so a message whose turn came first goes first,
    and a retry pushed into the future waits its turn rather than jumping the
    queue by having been created earlier.
    """
    moment = now or _now()
    stamp = _stamp(moment)
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM outbox "
            " WHERE sent_at IS NULL AND failed_at IS NULL "
            "   AND not_before <= ? "
            # NULL means nobody holds it. An expired lock means the holder died
            # without saying whether the message went out, and only `retry`
            # accepts that risk — §6.2.
            "   AND (locked_until IS NULL "
            "        OR (locked_until < ? AND on_uncertain = ?)) "
            " ORDER BY not_before, id LIMIT ?",
            (stamp, stamp, RETRY, limit),
        )
        ids = [row["id"] for row in await cursor.fetchall()]
        if not ids:
            return []

        placeholders = ", ".join("?" for _ in ids)
        await db.execute(
            f"UPDATE outbox SET locked_until = ?, attempts = attempts + 1 "
            f"WHERE id IN ({placeholders})",
            (_stamp(moment + LOCK_FOR), *ids),
        )
        await db.commit()

        cursor = await db.execute(
            f"SELECT * FROM outbox WHERE id IN ({placeholders}) ORDER BY not_before, id",
            tuple(ids),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def mark_sent(message_id: int) -> None:
    """The send returned success. The narrow window §6.1 names closes here."""
    async with connect() as db:
        await db.execute(
            "UPDATE outbox SET sent_at = datetime('now'), locked_until = NULL, "
            "last_error = NULL WHERE id = ?",
            (message_id,),
        )
        await db.commit()


async def mark_failed(
    message_id: int,
    error: str,
    *,
    retry_in: timedelta,
    now: datetime | None = None,
) -> None:
    """The send failed and we know it did — so this is not the uncertain case.

    Clearing the lock is what says so: a row released this way is claimable
    again whatever its policy, because there is nothing to be unsure about.

    `now` is injectable for the same reason claim() takes one: this is the write
    that decides when the message becomes due again, and a schedule computed
    from a different clock than the one that reads it is a test that passes on
    the wrong grounds.
    """
    async with connect() as db:
        await db.execute(
            "UPDATE outbox SET locked_until = NULL, not_before = ?, last_error = ? "
            "WHERE id = ?",
            (_stamp((now or _now()) + retry_in), error[:500], message_id),
        )
        await db.commit()


async def park(message_id: int, reason: str) -> None:
    """Put a message on the shelf: out of the queue, still on the record.

    Deleting would be tidier and would erase the evidence. A parked row is what
    somebody reads when a customer says they never got it.
    """
    async with connect() as db:
        await db.execute(
            "UPDATE outbox SET failed_at = datetime('now'), locked_until = NULL, "
            "last_error = ? WHERE id = ?",
            (reason[:500], message_id),
        )
        await db.commit()


async def uncertain(*, now: datetime | None = None) -> list[dict]:
    """Rows a crash left in doubt whose policy refuses to guess (§6.2).

    Nothing sends these. They exist to be parked and shown to a person, because
    the alternative — a broadcast that resends itself after every restart — is
    the failure the policy is there to prevent.
    """
    stamp = _stamp(now or _now())
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM outbox "
            " WHERE sent_at IS NULL AND failed_at IS NULL "
            "   AND locked_until IS NOT NULL AND locked_until < ? "
            "   AND on_uncertain = ? ORDER BY id",
            (stamp, REVIEW),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def queue_depth(*, now: datetime | None = None) -> dict[str, int]:
    """What the daily digest and the alert need: due, parked, in flight."""
    stamp = _stamp(now or _now())
    async with connect() as db:
        cursor = await db.execute(
            "SELECT "
            "  COUNT(*) FILTER (WHERE sent_at IS NULL AND failed_at IS NULL "
            "                     AND not_before <= ?) AS due, "
            "  COUNT(*) FILTER (WHERE failed_at IS NOT NULL) AS parked, "
            "  COUNT(*) FILTER (WHERE sent_at IS NOT NULL) AS sent "
            "FROM outbox",
            (stamp,),
        )
        row = await cursor.fetchone()
        return {"due": row[0], "parked": row[1], "sent": row[2]}


async def prune(*, payload_after: timedelta = timedelta(days=7),
                header_after: timedelta = timedelta(days=365)) -> dict[str, int]:
    """§6.4, and the two windows are different on purpose.

    A week is right for the payload — it is the message text, it has been
    delivered, and keeping it is the only part of this table with any real size.
    A week is wrong for the header: the median gap to a repeat purchase is 52
    days and two thirds of them fall inside 90, so a weekly sweep would delete
    the record of a send before the purchase it is meant to be measured against
    ever happens. The header lives a year, and what it costs is a few dozen
    bytes a row.
    """
    now = _now()
    async with connect() as db:
        cleared = await db.execute(
            "UPDATE outbox SET payload = '{}' "
            " WHERE payload != '{}' AND sent_at IS NOT NULL AND sent_at < ?",
            (_stamp(now - payload_after),),
        )
        dropped = await db.execute(
            "DELETE FROM outbox WHERE created_at < ?",
            (_stamp(now - header_after),),
        )
        await db.commit()
        return {"payloads_cleared": cleared.rowcount or 0,
                "rows_dropped": dropped.rowcount or 0}
