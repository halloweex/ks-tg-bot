"""Refresh what we know about the shop's offers.

Three lines of scenario over a port and a table, which is the point: the sweep
can be run against a fake storefront with no network, and the screens can be
tested against a filled table with no sweep.
"""
from __future__ import annotations

from loguru import logger

from core.ports.catalog import Storefront
from core.repos.catalogue import save_offers


async def refresh_once(storefront: Storefront) -> int:
    """Read the catalogue and write it down. Returns how many offers it saw.

    A failed read is {} by contract, and save_offers ignores it — so a shop that
    is briefly unreachable leaves the previous answers standing rather than
    taking every buy button off every screen.
    """
    offers = await storefront.get_offers()
    if not offers:
        logger.warning("Catalogue sweep read nothing; keeping the previous offers")
        return 0
    await save_offers(offers)
    sellable = sum(1 for offer in offers.values() if offer.available)
    logger.info("Catalogue refreshed: {} offers, {} sellable", len(offers), sellable)
    return len(offers)
