"""Tracking for fire-and-forget background tasks.

asyncio keeps only a weak reference to a task returned by create_task, so a
task nobody holds can be garbage-collected mid-await (e.g. halfway through a
KeyCRM request), and any exception it raises is swallowed silently. We keep a
strong reference until the task finishes, log its exceptions, and drain on
shutdown.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine

from loguru import logger

_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Schedule a background coroutine, keeping it alive until done and logging
    any exception it raises (instead of losing it)."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


def _on_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).error("Background task '{}' failed", task.get_name())


async def drain(timeout: float = 8.0) -> None:
    """On shutdown, give outstanding background tasks a moment to finish.

    Anything still running past the timeout is left to the durable layer to
    recover (e.g. an interrupted broadcast resumes on next startup)."""
    if not _background_tasks:
        return
    logger.info("Draining {} background task(s)...", len(_background_tasks))
    await asyncio.wait(set(_background_tasks), timeout=timeout)
