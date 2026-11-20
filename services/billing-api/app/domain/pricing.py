"""Bill calculation rules (Mobile Provider Billing Guide, sections 2-4).

This module is pure: no database, no HTTP, no clock. That makes the most
important business rule trivial to unit test.

  Phone    = $10 x ceil(max(0, minutes - 1000) / 1000)
  Internet = $50 + $10 x ceil(max(0, MB - 20000) / 10000)      (1 GB = 1000 MB)
  Total    = Phone + Internet   (no taxes, fees or discounts)

All arithmetic is done on integers (minutes, MB) and Decimal (money); floats
are never used for money.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.enums import UsageType, UsageUnit

# Usage recorded by one "Add Usage" call (guide 2.3 and 3.3)
USAGE_PER_EVENT: dict[UsageType, tuple[int, UsageUnit]] = {
    UsageType.PHONE: (10, UsageUnit.MINUTE),
    UsageType.INTERNET: (1, UsageUnit.MB),
}

PHONE_FREE_MINUTES = 1_000
PHONE_BLOCK_MINUTES = 1_000
PHONE_BLOCK_PRICE = Decimal("10.00")

INTERNET_BASE_PRICE = Decimal("50.00")
INTERNET_INCLUDED_MB = 20_000  # 20 GB
INTERNET_BLOCK_MB = 10_000  # 10 GB
INTERNET_BLOCK_PRICE = Decimal("10.00")

MB_PER_GB = 1_000


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


@dataclass(frozen=True)
class BillBreakdown:
    phone_minutes: int
    internet_mb: int
    phone_extra_blocks: int
    internet_extra_blocks: int
    phone_charge: Decimal
    internet_charge: Decimal

    @property
    def total(self) -> Decimal:
        return self.phone_charge + self.internet_charge

    @property
    def internet_gb(self) -> Decimal:
        return Decimal(self.internet_mb) / MB_PER_GB


def calculate_bill(phone_minutes: int, internet_mb: int) -> BillBreakdown:
    if phone_minutes < 0 or internet_mb < 0:
        raise ValueError("Usage cannot be negative.")

    phone_blocks = _ceil_div(max(0, phone_minutes - PHONE_FREE_MINUTES), PHONE_BLOCK_MINUTES)
    internet_blocks = _ceil_div(max(0, internet_mb - INTERNET_INCLUDED_MB), INTERNET_BLOCK_MB)

    return BillBreakdown(
        phone_minutes=phone_minutes,
        internet_mb=internet_mb,
        phone_extra_blocks=phone_blocks,
        internet_extra_blocks=internet_blocks,
        phone_charge=PHONE_BLOCK_PRICE * phone_blocks,
        internet_charge=INTERNET_BASE_PRICE + INTERNET_BLOCK_PRICE * internet_blocks,
    )
