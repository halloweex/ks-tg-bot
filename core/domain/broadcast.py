"""The header of a broadcast: which one it is, and who is owed the summary.

The queue answers what happened to each message. What it cannot answer is who
sent what text, when, and whether the send as a whole is over — that is a row of
its own, and this is the part of it a scenario reads.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Job:
    """One broadcast that has been started and not yet closed out.

    Two fields rather than the whole row. `text` is stored and deliberately
    absent here: the only reader is the sweep that closes finished jobs, and the
    summary it sends counts outcomes instead of quoting the message. A field on
    a type is a field somebody eventually formats into something.

    `created_by` is None for a job whose sender is not recorded — the column is
    nullable, and jobs that predate the outbox have it empty. The scenario reads
    that as "nobody to report to" and closes the job silently, so it is spelled
    out here as a value the type expects rather than left to be discovered by
    whoever gets the first None.
    """

    id: int
    created_by: int | None
