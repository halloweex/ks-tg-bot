"""Rivo's webhook JSON, turned into the four events worth telling somebody.

Pure: a dict in, a domain event or None out. Nothing here touches the network,
which is what lets the payloads live in tests as the strings Rivo actually
sends rather than as something a mock agreed to return.

None means "not for a customer" and covers three different cases on purpose —
an event type we do not announce, a payload with no email, and a shape we do
not recognise. All three end the same way: nothing is sent, and nothing is
logged as an error, because two thirds of what Rivo publishes is somebody
else's business.
"""
from __future__ import annotations

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

    kind = _KINDS.get(str(payload.get("event_type") or ""))
    if kind is None:
        return None

    customer = payload.get("customer")
    if not isinstance(customer, dict):
        return None
    email = str(customer.get("email") or "").strip()
    if not email:
        return None

    # `points_diff` is the change including the sign; `points_amount` is the
    # same number without one on some events. Prefer the signed field, so a
    # spend does not arrive looking like an award.
    points = _as_int(payload.get("points_diff", payload.get("points_amount", 0)))

    return LoyaltyEvent(
        kind=kind,
        email=email,
        points=points,
        balance=_as_int(customer.get("points_tally", 0)),
        tier=str(customer.get("loyalty_status") or "").strip(),
    )
