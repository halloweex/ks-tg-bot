"""Rivo's webhook JSON, turned into the four events worth telling somebody.

A dict in, a domain event or None out. Nothing here touches the network, which
is what lets the payloads live in tests as the strings Rivo actually sends
rather than as something a mock agreed to return.

It used to say "pure" and no longer can: `_UNANNOUNCED_SEEN` below remembers
which unknown event types have already been logged, so the same payload twice
writes one line and not two. Said out loud rather than left for a reader to
discover, because a test that asserts on that line has to reset the set.

None means "not for a customer" and covers three different cases on purpose —
an event type we do not announce, a payload with no email, and a shape we do
not recognise. All three end the same way: nothing is sent, and nothing is
logged as an error, because two thirds of what Rivo publishes is somebody
else's business.

**Two exceptions to that silence, and they are the important part.** An event
type we have never seen is logged, once per type: `_KINDS` covers eight of the
roughly thirty types Rivo publishes, so a name we do not know is the ordinary
case — but it is also exactly what a rename on their side looks like, and a
rename would kill the whole loyalty channel while the endpoint went on answering
200 and every log stayed clean. A points event with no signed amount is logged
for the same reason: it is a shape we cannot act on, and until it is said out
loud nobody can go and fetch the real payload. Once per type rather than per
request, because both arrive all day.
"""
from __future__ import annotations

from loguru import logger

from core.domain.loyalty import Kind, LoyaltyEvent

# Their vocabulary on the left, ours on the right. The pairs that say the same
# thing to a customer collapse: an upgraded tier and an updated tier are one
# congratulation, and a warning about points expiring is one warning whether it
# is the first or the last.
_KINDS: dict[str, Kind] = {
    "balance_transaction/created": Kind.POINTS,
    "points_event/created": Kind.POINTS,
    "customer_vip_tier/upgraded": Kind.TIER,
    "notification/points_expiry_warning": Kind.EXPIRING,
    "notification/points_expiry_last_chance": Kind.EXPIRING,
    "notification/credits_expiry_warning": Kind.EXPIRING,
    "notification/credits_expiry_last_chance": Kind.EXPIRING,
    "referral/completed": Kind.REFERRAL,
}


# Types already logged, so the line appears the first time and not again. A set
# that only grows, bounded by Rivo's vocabulary — about thirty entries at worst.
_UNANNOUNCED_SEEN: set[str] = set()

# The same, for the other thing worth saying once: a points event that arrived
# without a signed amount, so we could not tell an award from a redemption.
_UNSIGNED_SEEN: set[str] = set()


def _as_int(value: object) -> int:
    """Their numbers arrive as ints, floats and strings depending on the field."""
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def parse_event(payload: dict) -> LoyaltyEvent | None:
    """One webhook body as an event, or None if it is not ours to announce."""
    if not isinstance(payload, dict):
        return None

    event_type = str(payload.get("event_type") or "")
    kind = _KINDS.get(event_type)
    if kind is None:
        if event_type and event_type not in _UNANNOUNCED_SEEN:
            _UNANNOUNCED_SEEN.add(event_type)
            logger.info("Rivo event type not announced: {}", event_type)
        return None

    customer = payload.get("customer")
    if not isinstance(customer, dict):
        return None
    email = str(customer.get("email") or "").strip()
    if not email:
        return None

    # `points_diff` is the change including the sign. `points_amount` is the
    # same number **without** one, and that is why it is not a fallback: a
    # `balance_transaction/created` carries redemptions as well as awards, so
    # reading the unsigned field when the signed one is missing turns forty
    # points spent into forty points won. The customer reads «Тобі нараховано
    # 40 балів» about points she has just spent, and `announce`'s one-per-day
    # dedup then eats her real award an hour later.
    #
    # So: no signed field, no claim. It is logged instead, because the half of
    # this that had teeth was never the missed message — it was that nothing
    # anywhere said a word. A missed award is invisible and recoverable; a
    # sentence telling somebody she gained what she spent is neither.
    #
    # `dict.get(key, default)` is avoided on purpose: it fires the default only
    # when the key is **missing**, and a JSON API sends an unset field as
    # `null`. That is the whole bug this replaced — `points_diff: null` used to
    # fall through to the unsigned field and then be dropped at `points <= 0`,
    # silently. A legitimate zero, meanwhile, is a real answer and stays one.
    signed = payload.get("points_diff")
    points = _as_int(signed)
    if signed is None and kind is Kind.POINTS:
        if event_type not in _UNSIGNED_SEEN:
            _UNSIGNED_SEEN.add(event_type)
            logger.warning(
                "Rivo {} arrived with no signed points_diff (points_amount={!r}). "
                "Not announced: the unsigned field cannot tell an award from a "
                "redemption. A recorded payload is wanted — see "
                "docs/found-during-move.md §18.",
                event_type, payload.get("points_amount"))

    return LoyaltyEvent(
        kind=kind,
        email=email,
        points=points,
        balance=_as_int(customer.get("points_tally", 0)),
        tier=str(customer.get("loyalty_status") or "").strip(),
    )
