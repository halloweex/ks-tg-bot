"""The header of a broadcast: what was sent, by whom, and whether it is done.

There used to be a second half — one `broadcast_targets` row per recipient,
with its own status, driver and resume-after-restart — and it was the outbox
written once for one feature. Stage 6 replaced it with the real one
(core/usecases/broadcast.py), so what is left here is the part the queue cannot
answer: which text a person sent, when, and under whose name.

The table `broadcast_targets` is left in the schema with the rows of the jobs
that ran before the move. Dropping it would delete the only record of them; it
is simply no longer written.
"""
from __future__ import annotations

import aiosqlite

from core.repos.base import connect


async def create_broadcast_job(text: str, created_by: int) -> int:
    """Record a new job and return its id.

    The recipient snapshot used to happen here, into broadcast_targets. It now
    happens as the queued rows themselves — same property, one mechanism: who is
    in the list at this moment is who the job is responsible for, and an opt-out
    afterwards does not pull a queued message back out.
    """
    async with connect() as db:
        cursor = await db.execute(
            "INSERT INTO broadcast_jobs (text, created_by) VALUES (?, ?)",
            (text, created_by),
        )
        await db.commit()
        return cursor.lastrowid


async def get_unfinished_broadcasts() -> list[dict]:
    """Jobs still marked 'running'.

    Read by the sender's loop to notice the ones whose queue has drained. It is
    no longer a resume list — nothing needs resuming when the messages are rows
    in a queue that outlives the process.
    """
    async with connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, text, created_by FROM broadcast_jobs "
            "WHERE status = 'running' ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def finish_broadcast_job(job_id: int) -> None:
    """Mark a job done once all its targets are processed."""
    async with connect() as db:
        await db.execute(
            "UPDATE broadcast_jobs SET status = 'done', "
            "finished_at = datetime('now') WHERE id = ?",
            (job_id,),
        )
        await db.commit()


# broadcast_job_stats lived here and is gone: the numbers now come from the
# queue, which is where the outcomes are — core.repos.outbox.campaign_stats.
