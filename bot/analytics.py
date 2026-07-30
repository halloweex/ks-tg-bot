"""Fire-and-forget product analytics.

Handlers call `track(...)` and move on. Two rules make that safe:

* the write happens in a background task, so a slow disk never delays a reply;
* every failure is swallowed and logged, so a broken analytics write can never
  turn into a broken feature. Losing an event is acceptable; losing a customer's
  order view because the events table misbehaved is not.

chat_id is recorded. It is the same identifier already stored in `users`, and
without it there is no way to distinguish a returning user from a new one — the
question most of the roadmap decisions actually hinge on.
"""
from __future__ import annotations

import json

from loguru import logger

from bot.db import log_event
from bot.tasks import spawn


async def _write(chat_id: int | None, event: str, meta: dict) -> None:
    try:
        await log_event(chat_id, event, json.dumps(meta, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — analytics must never break a flow
        logger.warning("analytics: failed to record {!r}: {}", event, exc)


def track(chat_id: int | None, event: str, **meta) -> None:
    """Record that `event` happened, with optional structured detail.

    Keyword arguments land in the `meta` JSON column: keep them small and
    countable (numbers, short enums), never message text or personal data.
    """
    spawn(_write(chat_id, event, meta), name=f"track_{event}")
