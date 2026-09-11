"""What the proactive messages did, read out of what was already written down.

Nothing here records anything. Both halves of this were on disk before the file
existed, which is the whole reason it is a read and not an instrumentation
project:

* `outbox` is not a queue, it is a year-long journal. `prune()` clears the
  `payload` after a week and leaves the header — `chat_id`, `type`,
  `campaign_key`, `sent_at`, `failed_at`, `attempts` — standing for 365 days.
  Every proactive message already carries a campaign key, because `enqueue`
  takes one as a required argument and all six senders give it.
* `orders` is filled by `core/usecases/sync_incremental.py` every two minutes,
  from the CRM, **without the customer opening the bot**. So a purchase lands in
  this database whether or not she ever came back to the chat.

Two tables, one file, both keyed by `chat_id`. The join is a SELECT.

**What it deliberately does not measure.** A click. Of the six kinds of
proactive message, three carry no keyboard at all, two carry a `url` button —
and Telegram tells a bot nothing when one is tapped, which `/stats` has said
out loud about the Website button since long before this — and the sixth opens
the inline panel with a query the customer can see in her own input field.
There is no click to read, and the only way to buy one is to put a button on a
message that has none, pointing back into the bot instead of at the shop. That
is a product change made to obtain a number, and it is refused here.

**Why there is no revenue column.** "How many of the people who got it ordered
afterwards" is an observation. "This campaign earned ₴12,480" is a claim about
cause, and nothing here can support it: the same customer buys on a two-day
cycle for her own reasons. The count is honest and the sum would not be.
"""
from __future__ import annotations

from collections.abc import Sequence

from core.repos.base import connect

# A message a person wrote, and a message to an admin. Neither is a campaign.
#
# `support` matters most: it is a manager's reply inside a conversation that is
# very often *about an order*, so counting purchases after it would credit the
# campaign machinery with a human being's work and make "support" the best
# converting thing the shop does.
#
# `broadcast_report` is subtler and would corrupt every broadcast row.
# `core/usecases/broadcast.py` queues the admin's completion summary under the
# **same** `bcast.<job_id>` key as the broadcast itself (line 131), so grouping
# on the key alone hands every broadcast one extra recipient — an admin, who
# never buys — and quietly lowers the share. Grouping by `(campaign_key, type)`
# is what keeps them apart; excluding the type is what keeps it off the report.
NOT_A_CAMPAIGN: tuple[str, ...] = ("support", "broadcast_report")


# **Both sides go through `datetime()`, and that is not decoration.** `sent_at`
# is written by `datetime('now')` and looks like "2026-09-11 09:00:00"; the
# CRM's `ordered_at` arrives as ISO with a T, "2026-09-11T08:00:00". Compared as
# text, position ten is 'T' against ' ' — and 'T' sorts higher, so an order
# placed an hour *before* the message reads as an order placed after it.
# Measured, not assumed: the raw comparison answers 1 where the truth is 0.
# That single character is the difference between an observation and a campaign
# taking credit for a purchase that preceded it.
#
# It also disposes of the empty string `orders.ordered_at` defaults to:
# `datetime('')` is NULL, and NULL fails every comparison rather than passing
# the wrong one.
_OUTCOMES = """
SELECT o.campaign_key,
       o.type,
       -- People, not rows: the restock sweep can send one chat two messages
       -- under one daily key, and a denominator that counts them twice reports
       -- a share of something that was never a population.
       COUNT(DISTINCT CASE WHEN o.sent_at   IS NOT NULL THEN o.chat_id END)  AS people,
       SUM(CASE WHEN o.sent_at   IS NOT NULL THEN 1 ELSE 0 END)              AS delivered,
       COUNT(DISTINCT CASE WHEN o.failed_at IS NOT NULL THEN o.chat_id END)  AS failed,
       COUNT(DISTINCT CASE WHEN EXISTS (
             SELECT 1 FROM orders r
              WHERE r.chat_id = o.chat_id
                AND datetime(r.ordered_at) >  datetime(o.sent_at)
                AND datetime(r.ordered_at) <= datetime(o.sent_at, ?)
       ) THEN o.chat_id END)                                           AS bought,
       COUNT(DISTINCT CASE WHEN EXISTS (
             SELECT 1 FROM orders r
              WHERE r.chat_id = o.chat_id
                AND datetime(r.ordered_at) <= datetime(o.sent_at)
                AND datetime(r.ordered_at) >  datetime(o.sent_at, ?)
       ) THEN o.chat_id END)                                           AS bought_before,
       MAX(o.sent_at)                                                  AS last_sent
  FROM outbox o
 -- Failed rows stay in. They keep their campaign key and they are the cost of
 -- the channel: somebody who blocked the bot is a person this campaign reached
 -- for. Filtering them here is what made the "undelivered" column always zero.
 -- They cannot pollute the purchase windows, because those key off `sent_at`
 -- and `datetime(NULL)` matches nothing.
 WHERE (o.sent_at IS NOT NULL OR o.failed_at IS NOT NULL)
   AND o.created_at >= datetime('now', ?)
   AND o.type NOT IN ({excluded})
 GROUP BY o.campaign_key, o.type
 ORDER BY last_sent DESC
"""


async def campaign_outcomes(
    *, days: int, window_days: int,
    exclude_types: Sequence[str] = NOT_A_CAMPAIGN,
) -> list[tuple[str, str, int, int, int, int, int, str]]:
    """One row per (campaign key, message type), newest send first.

    `(campaign_key, type, people, delivered, failed, bought, bought_before,
    last_sent)`.

    `bought` counts the people who ordered in the `window_days` **after** the
    message reached them. `bought_before` counts those who ordered in a window
    of the same length ending where the message starts — the same people, the
    same query, the days before instead of the days after. It costs one more
    EXISTS and it is what turns "5 of 38 ordered" from a number nobody can act
    on into a comparison with the ordinary rate.

    It is not a control group and must not be rendered as one for every kind:
    where the campaign is *caused* by a purchase — loyalty points awarded for an
    order, a referral reward for a friend's — the triggering order sits in the
    "before" window by construction, and the row would read as "she would have
    bought anyway" about the very purchase that sent the message. Which kinds
    those are is the report's business, not this query's; see
    `core/usecases/analytics.py`.
    """
    excluded = list(exclude_types)
    placeholders = ",".join("?" * len(excluded)) if excluded else "''"
    sql = _OUTCOMES.format(excluded=placeholders)
    params = [f"+{int(window_days)} days", f"-{int(window_days)} days",
              f"-{int(days)} days", *excluded]
    async with connect() as db:
        cursor = await db.execute(sql, params)
        return [tuple(row) for row in await cursor.fetchall()]


class SqliteCampaignOutcomes:
    """Implements core.ports.analytics.CampaignOutcomes against today's database.

    The same delegating shell `SqliteUsageStats` is, for the same reason: the
    seam moves while SQLite is underneath and the query is unchanged.
    """

    async def outcomes(
        self, *, days: int, window_days: int, exclude_types: Sequence[str],
    ) -> list[tuple[str, str, int, int, int, int, int, str]]:
        return await campaign_outcomes(days=days, window_days=window_days,
                                       exclude_types=exclude_types)
