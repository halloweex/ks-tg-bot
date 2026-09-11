"""Refresh what we know about the shop's offers.

Three lines of scenario over a port and a table, which is the point: the sweep
can be run against a fake storefront with no network, and the screens can be
tested against a filled table with no sweep.
"""
from __future__ import annotations

from loguru import logger

from core.ports.catalog import Storefront
from core.ports.repositories import OfferCache


async def refresh_once(storefront: Storefront, catalogue: OfferCache) -> int:
    """Read the catalogue and write it down. Returns how many offers it saw.

    One name from each side: `Storefront` is the outside world, `OfferCache` is
    what we kept. This scenario is the thing in between, and now it names both
    rather than importing one of them.

    A failed read is {} by contract, and both this function and the cache refuse
    it — so a shop that is briefly unreachable leaves the previous answers
    standing rather than taking every buy button off every screen. That contract
    is also what makes the write a `replace`: anything the sweep did not see is
    something the storefront has stopped listing, and its row goes.
    """
    offers = await storefront.get_offers()
    if not offers:
        logger.warning("Catalogue sweep read nothing; keeping the previous offers")
        return 0
    sellable = sum(1 for offer in offers.values() if offer.available)
    known = await catalogue.count()
    if known and len(offers) * 2 < known:
        # **One sweep may not unpublish the shop.** A shop does not lose half
        # its catalogue in an hour, so a read this much shorter than the table
        # is a feed fault until a human says otherwise — and a feed can produce
        # one without erroring: an empty page mid-catalogue is how this feed
        # says "that was the last one". The adapter retries such a page, which
        # catches a transient answer and not a persistent one.
        #
        # The offers are still written. Prices and availability are worth
        # having, and refusing the whole sweep would throw away good data to
        # avoid a bad delete.
        #
        # If the shop really did halve, this keeps refusing and the line below
        # keeps appearing, which is the intended way for it to end: somebody
        # reads it and decides, rather than an hourly job deciding by itself.
        await catalogue.update(offers)
        logger.error(
            "Catalogue sweep read {} offers against {} already cached and did "
            "NOT prune. A shop does not halve in an hour, so this is a short "
            "feed until proven otherwise; if the catalogue really did shrink, "
            "clear the offers table by hand and the next sweep will settle.",
            len(offers), known)
        return len(offers)

    removed = await catalogue.replace(offers)
    logger.info("Catalogue refreshed: {} offers, {} sellable, {} delisted",
                len(offers), sellable, removed)
    return len(offers)
