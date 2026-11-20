"""Bill calculation rules from the Mobile Provider Billing Guide (sections 2-4)."""

from decimal import Decimal

import pytest

from app.domain.pricing import calculate_bill


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "0"),
        (1000, "0"),       # all included
        (1001, "10"),      # one started block (guide 2.2)
        (2000, "10"),
        (2001, "20"),      # guide 2.2 example
        (5500, "50"),
    ],
)
def test_phone_charge_blocks(minutes, expected):
    assert calculate_bill(minutes, 0).phone_charge == Decimal(expected)


@pytest.mark.parametrize(
    ("mb", "expected"),
    [
        (0, "50"),         # base price applies even with zero usage (guide 3.1)
        (20_000, "50"),    # 20 GB
        (20_001, "60"),    # partial block is a full block
        (21_000, "60"),    # 21 GB (guide 3.2)
        (30_000, "60"),    # 30 GB (guide 3.2)
        (35_000, "70"),    # 35 GB (guide 3.2)
        (40_001, "80"),
    ],
)
def test_internet_charge_blocks(mb, expected):
    assert calculate_bill(0, mb).internet_charge == Decimal(expected)


def test_worked_example_from_guide_section_4_3():
    bill = calculate_bill(1350, 35_000)
    assert bill.phone_charge == Decimal("10")
    assert bill.internet_charge == Decimal("70")
    assert bill.total == Decimal("80")
    assert bill.phone_extra_blocks == 1
    assert bill.internet_extra_blocks == 2
    assert bill.internet_gb == Decimal("35")


def test_gb_uses_1000_mb_per_gb():
    assert calculate_bill(0, 1500).internet_gb == Decimal("1.5")


def test_negative_usage_is_rejected():
    with pytest.raises(ValueError):
        calculate_bill(-10, 0)
