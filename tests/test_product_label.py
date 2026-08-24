"""Turning a CRM product name into something a customer can read."""
from __future__ import annotations

import pytest

from core.texts import product_label


@pytest.mark.parametrize("raw,expected", [
    # The common shape: English first, Ukrainian after the dash. Trimming from
    # the left used to leave "Dear Doer The Hidden Body Scrub - Dear Doer the…"
    ("Abib Collagen Gel Mask Heartleaf Jelly - Гелева маска з колагеном",
     "Abib Гелева маска з колагеном"),
    # The other way round — Ukrainian first — must survive untouched.
    ("Abib Сонцезахисний крем, 50 мл - Abib Sedum Hyaluron Sunscreen",
     "Abib Сонцезахисний крем, 50 мл"),
    # The brand is already in the Ukrainian half; do not say it twice.
    ("Dear Doer The Hidden Body Scrub - Dear Doer the Hidden Скраб для тіла",
     "Dear Doer the Hidden Скраб для тіла"),
    # A sample: the marker is not the brand.
    ("(Miniature) Solep Premier Hi-gro Shampoo - шампуня проти випадіння",
     "Solep шампуня проти випадіння"),
    # No Cyrillic anywhere — already what it is.
    ("CURE SPF Cooling Sunstick", "CURE SPF Cooling Sunstick"),
    # No separator, mixed languages — left alone rather than cut in the middle.
    ("BLIV:U Collagen Cream крем для обличчя",
     "BLIV:U Collagen Cream крем для обличчя"),
])
def test_the_half_a_customer_reads_is_the_half_that_survives(raw, expected):
    assert product_label(raw, limit=80) == expected


def test_a_long_name_is_still_trimmed():
    label = product_label("Abib Gel Mask - " + "дуже довга назва " * 5, limit=20)
    assert len(label) <= 21 and label.endswith("…")


def test_an_empty_name_stays_empty():
    assert product_label("") == ""
