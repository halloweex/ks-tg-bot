"""Starting a broadcast, and noticing when it has finished.

The second sender to move onto the outbox (§6.6). What it replaces is not a
direct send but a second durable mechanism: `broadcast_targets` held one row per
recipient with its own status, `run_broadcast_job` drove it, and
`resume_broadcasts` restarted it after a redeploy. All three were the outbox,
written once for one feature — so the move deletes them rather than adapting
them.

**What the job row keeps.** `broadcast_jobs` stays, because the queue answers
"what happened to this message" and not "who sent what text, when, and did it
finish". It is also where the campaign key comes from: `bcast.<job id>`, so a
send from Tuesday and a send from Wednesday are two campaigns in the funnel
rather than one bucket called "broadcast".

**The policy is `review`, and it is the reason §6.2 exists.** A broadcast
interrupted mid-send used to resume from its target table; through the outbox, a
row whose lock expired after a crash is one nobody can say was delivered, and
sending it again would message people twice. Those rows stop and wait for a
person. The old mechanism had the same problem and solved it by trusting a
status write that a crash could equally have lost.

**The recipient list is still snapshotted at the moment of sending**, now as the
queued rows themselves: an opt-out after that point does not pull a message back
out of the queue, exactly as it did not change a job's target list before.
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from core.domain.campaign import CampaignKey
from core.i18n import admin_texts
from core.repos.broadcast import (create_broadcast_job, finish_broadcast_job,
                                  get_unfinished_broadcasts)
from core.repos.outbox import REVIEW, campaign_stats, enqueue, enqueue_many
from core.repos.users import get_broadcast_recipients, get_user_language

KIND = "broadcast"

# The completion summary is itself a proactive message, so it goes through the
# queue like everything else rather than being the one send that bypasses it.
REPORT_KIND = "broadcast_report"


def campaign_for(job_id: int) -> CampaignKey:
    """One campaign per job, so two sends on one day stay two campaigns."""
    return CampaignKey("bcast", str(job_id))


@dataclass(frozen=True)
class Started:
    job_id: int
    queued: int


async def start_broadcast(text: str, admin_id: int) -> Started:
    """Record the job and queue a message for everyone who has not opted out."""
    job_id = await create_broadcast_job(text, admin_id)
    campaign = campaign_for(job_id)
    recipients = await get_broadcast_recipients()

    queued = await enqueue_many(
        recipients, KIND, campaign, {"text": text},
        # §6.2: a doubled broadcast is worse than a missed one.
        on_uncertain=REVIEW,
        # Makes starting the same job twice — a retried tap, a resumed
        # handler — write nothing the second time.
        dedup_prefix=str(campaign),
    )
    logger.info("Broadcast job #{} by admin {}: queued {} of {} recipient(s)",
                job_id, admin_id, queued, len(recipients))
    return Started(job_id=job_id, queued=queued)


async def report_finished_jobs() -> list[int]:
    """Close out any broadcast whose queue has drained, and tell whoever sent it.

    Called from the sender's loop, which is the thing already awake. A job is
    finished when none of its messages are waiting — sent, blocked and parked
    all count as decided, because none of them will change on their own.
    """
    finished: list[int] = []
    for job in await get_unfinished_broadcasts():
        stats = await campaign_stats(str(campaign_for(job["id"])))
        if stats["waiting"]:
            continue
        # Nothing in the queue at all means the messages were never written:
        # the process died between recording the job and queueing it, or the
        # job predates the outbox. Closed with zeros rather than left running,
        # because a job that stays "running" forever is checked on every pass
        # forever — and a summary saying nothing went out is true.

        await finish_broadcast_job(job["id"])
        finished.append(job["id"])
        logger.info("Broadcast job #{} finished: {}", job["id"], stats)

        admin_id = job["created_by"]
        if not admin_id:
            continue
        at = admin_texts(await get_user_language(admin_id))
        await enqueue(
            admin_id, REPORT_KIND, campaign_for(job["id"]),
            {"text": at.MSG_BROADCAST_COMPLETE.format(
                sent=stats["sent"], failed=stats["failed"], blocked=stats["blocked"])},
            # One report per job, whatever happens to the loop that noticed.
            dedup_key=f"{campaign_for(job['id'])}:report",
        )
    return finished
