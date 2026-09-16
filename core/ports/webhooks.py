"""What the webhook endpoint needs from storage to handle a delivery once.

**What counts as the same delivery: the signed message, byte for byte.** Rivo
signs `id.timestamp.body`; the key a delivery is remembered by is a hash of
exactly that. Not the id alone, for two reasons found in review before this
shipped. Nobody has measured that `rivo-webhook-id` is unique per event — were
it a subscription id, or fixed for the cabinet's Test button, an id-keyed ledger
would drop every later event for a month and the channel would die without a
word. And an id containing a dot lets the id/timestamp boundary move without
breaking the signature, which would make a replay look new. The signed bytes
have neither problem, and a replay that swaps the unsigned topic header is still
the same signed bytes.

What the key does assume: that two different events never produce the same
`id.timestamp.body`. The topic is not part of it, so two events differing only in
topic, with identical id, timestamp and body, would be taken for one. Rivo's
bodies carry the event's own id and times, which makes that remote, not
impossible.

**Three states, not two.** `claimed` — this caller handles it. `busy` — another
caller is handling it right now: answer something the sender will retry, not
"done", because that other attempt may still fail. `done` — handled: answer 200
and drop it. A ledger with only "seen" / "not seen" told a retry arriving
mid-handling that the delivery was done, and when the first attempt then failed
there was nobody left to send it.

**A claim is a lease.** An attempt that dies without releasing — killed by a
deploy, out of memory, a cancellation inside the claim itself — leaves its claim
behind; after the lease it can be taken over, so the next retry is handled.
Releasing on failure only makes that faster.

No age check on the timestamp lives here: whether Rivo re-signs a retry with a
new timestamp is unknown, and refusing its own retries would lose exactly what
this protects. A retry re-signed with a new timestamp is a new signed message and
is handled again; the outbox's per-day dedup key is what stops a second message.
"""
from __future__ import annotations

from typing import Literal, Protocol

Claim = Literal["claimed", "busy", "done"]


class DeliveryLedger(Protocol):
    """Which signed webhook messages were handled, or are being handled."""

    async def claim(self, source: str, digest: str, delivery_id: str) -> Claim:
        """Take the message if nobody holds it, or holds it past the lease.
        Atomic: of concurrent claims for one digest, exactly one is `claimed`.
        `delivery_id` is stored for the humans reading the table, not matched."""
        ...

    async def complete(self, source: str, digest: str) -> None:
        """Mark a claimed message handled, so a repeat is answered `done`."""
        ...

    async def release(self, source: str, digest: str) -> None:
        """Give back a claim whose handling failed; a no-op once completed."""
        ...
