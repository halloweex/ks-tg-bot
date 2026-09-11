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
    removed = await catalogue.replace(offers)
    sellable = sum(1 for offer in offers.values() if offer.available)
    logger.info("Catalogue refreshed: {} offers, {} sellable, {} delisted",
                len(offers), sellable, removed)
    if removed > len(offers):
        # Said out loud rather than guarded against. A sweep that deletes more
        # than it writes is either the first one after months of a cache that
        # never pruned — which is the point of this change and happens once — or
        # a feed that answered with a fraction of the shop and no error. The
        # second is recoverable by the next hour's sweep, because this table is
        # rebuilt from the shop and holds nothing of its own; what is not
        # recoverable is nobody noticing.
        logger.warning(
            "Catalogue sweep removed more rows ({}) than it wrote ({}). Expected "
            "once, on the first sweep after pruning was introduced; twice means "
            "the feed is answering short without erroring.", removed, len(offers))
    return len(offers)
