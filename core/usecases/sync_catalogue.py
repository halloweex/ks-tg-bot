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
    standing rather than taking every buy button off every screen.
    """
    offers = await storefront.get_offers()
    if not offers:
        logger.warning("Catalogue sweep read nothing; keeping the previous offers")
        return 0
    await catalogue.record(offers)
    sellable = sum(1 for offer in offers.values() if offer.available)
    logger.info("Catalogue refreshed: {} offers, {} sellable", len(offers), sellable)
    return len(offers)
