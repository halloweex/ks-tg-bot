"""What the storefront sells, as the bot needs to know it.

One product's buyable form: the sku it shares with the CRM, the variant id the
cart link is built from, whether it can be bought at all right now, and the one
picture a screen can show of it.

Deliberately not a copy of the storefront's product record. The description, the
tags, the whole image gallery and the six timestamps are not what any screen
here asks for, and a domain object that mirrors somebody else's API is one that
changes every time they add a field.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Offer:
    """One sellable variant of a product.

    `sku` is the join to everything else we hold: order lines carry it, and so
    does the CRM's stock. `variant_id` is the storefront's own id and is what a
    cart link is addressed to — the sku means nothing to it.

    `available` is the storefront's answer to "can this be bought", which is not
    the same question as the CRM's unit count: a product can have stock and be
    unpublished, and both mean the customer cannot order it.

    `image_url` is one picture and not the gallery, because one is what fits
    where it is shown: the thumbnail beside a product in the inline list. Empty
    for a product the feed has no picture for, and the screens treat that as
    "no thumbnail" rather than as a reason to hide the product.
    """

    sku: str
    variant_id: int
    handle: str
    title: str
    price: str
    available: bool
    # Defaulted, and last: a product with no picture is a normal product, and
    # the code that never shows one should not have to mention it.
    image_url: str = ""
