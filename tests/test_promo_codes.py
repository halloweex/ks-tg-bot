"""Whether a promo code from the shop's admin can be handed out right now.

The rule reads Shopify's status and the dates both: a broadcast is composed a
while before it is sent, and a code that ends tonight is still «ACTIVE» in an
answer taken this morning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.domain import promo as promos
from core.domain.promo import Promo, verdict

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


@pytest.mark.parametrize("found, expected", [
    (None, promos.MISSING),
    (Promo("NOV20", status="ACTIVE", ends_at=NOW + DAY), promos.OK),
    (Promo("NOV20", status="ACTIVE"), promos.OK),                      # no end date
    (Promo("NOV20", status="ACTIVE", ends_at=NOW - DAY), promos.EXPIRED),
    (Promo("NOV20", status="EXPIRED"), promos.EXPIRED),
    (Promo("NOV20", status="SCHEDULED", starts_at=NOW + DAY), promos.SCHEDULED),
    (Promo("NOV20", status="ACTIVE", starts_at=NOW + DAY), promos.SCHEDULED),
    (Promo("NOV20", status="ACTIVE", usage_limit=100, used=100), promos.USED_UP),
    (Promo("NOV20", status="ACTIVE", usage_limit=100, used=99), promos.OK),
    (Promo("NOV20", status="SOMETHING_NEW"), promos.INACTIVE),
])
def test_a_code_is_handed_out_only_when_a_customer_would_get_the_discount(found, expected):
    assert verdict(found, NOW) == expected
