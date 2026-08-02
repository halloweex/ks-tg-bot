"""Durable broadcast: one job row, one progress row per recipient.

The pair exists so a broadcast interrupted by a restart or a redeploy resumes
where it stopped instead of starting over — which, for a send that is already
half delivered, is the difference between finishing and messaging people twice.
"""
from __future__ import annotations

import aiosqlite

from core.repos.base import connect


# ---------------------------------------------------------------------------
# Durable broadcast
# ---------------------------------------------------------------------------


async def create_broadcast_job(text: str, created_by: int) -> int:
    """Create a broadcast job and snapshot the current recipient list into
    broadcast_targets (all 'pending'). Returns the new job id.

    The recipient set is frozen at creation time so a later opt-out/opt-in
    can't change which people this job is responsible for.
    """
    async with connect() as db:
        cursor = await db.execute(
            "INSERT INTO broadcast_jobs (text, created_by) VALUES (?, ?)",
            (text, created_by),
        )
        job_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO broadcast_targets (job_id, chat_id) "
            "SELECT ?, chat_id FROM users "
            "WHERE chat_id NOT IN (SELECT chat_id FROM opt_out)",
            (job_id,),
        )
        await db.commit()
        return job_id


async def get_pending_targets(job_id: int) -> list[int]:
    """Return chat_ids of this job's targets that still need sending."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT chat_id FROM broadcast_targets "
            "WHERE job_id = ? AND status = 'pending'",
            (job_id,),
        )
        return [row[0] for row in await cursor.fetchall()]


async def mark_target(
    job_id: int, chat_id: int, status: str, error: str | None = None
) -> None:
    """Record the delivery outcome for one recipient (sent/failed/blocked)."""
    async with connect() as db:
        await db.execute(
            "UPDATE broadcast_targets SET status = ?, error = ?, "
            "updated_at = datetime('now') WHERE job_id = ? AND chat_id = ?",
            (status, error, job_id, chat_id),
        )
        await db.commit()


async def get_unfinished_broadcasts() -> list[dict]:
    """Return jobs still marked 'running' (to resume after a restart)."""
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


async def broadcast_job_stats(job_id: int) -> dict:
    """Return counts per status for a job: sent/failed/blocked/pending."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM broadcast_targets "
            "WHERE job_id = ? GROUP BY status",
            (job_id,),
        )
        stats = {"sent": 0, "failed": 0, "blocked": 0, "pending": 0}
        for status, count in await cursor.fetchall():
            stats[status] = count
        return stats
