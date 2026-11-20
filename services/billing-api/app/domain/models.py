"""Domain entities.

These plain dataclasses are what services work with. They are independent of
both the database (ORM models in app/db) and the HTTP contract (DTOs in
app/api/v1/schemas), so each layer can change without breaking the others.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.enums import BillStatus, UsageType, UsageUnit
from app.domain.pricing import BillBreakdown

ZERO = Decimal("0.00")


@dataclass
class Subscriber:
    subscriber_no: str
    full_name: str


@dataclass
class UsageRecord:
    subscriber_no: str
    year: int
    month: int
    usage_type: UsageType
    quantity: int
    unit: UsageUnit
    recorded_at: datetime
    id: int | None = None


@dataclass(frozen=True)
class UsageTotals:
    phone_minutes: int = 0
    internet_mb: int = 0


@dataclass
class Bill:
    subscriber_no: str
    year: int
    month: int
    phone_minutes: int
    internet_mb: int
    phone_charge: Decimal
    internet_charge: Decimal
    total: Decimal
    paid_amount: Decimal
    status: BillStatus
    calculated_at: datetime
    id: int | None = None

    @property
    def remaining_balance(self) -> Decimal:
        return max(self.total - self.paid_amount, ZERO)

    @staticmethod
    def status_for(total: Decimal, paid: Decimal) -> BillStatus:
        if paid <= ZERO and total > ZERO:
            return BillStatus.UNPAID
        if paid >= total:
            return BillStatus.PAID
        return BillStatus.PARTIALLY_PAID

    def apply_calculation(self, breakdown: BillBreakdown, calculated_at: datetime) -> None:
        """Recalculate charges while preserving payments already recorded."""
        self.phone_minutes = breakdown.phone_minutes
        self.internet_mb = breakdown.internet_mb
        self.phone_charge = breakdown.phone_charge
        self.internet_charge = breakdown.internet_charge
        self.total = breakdown.total
        self.calculated_at = calculated_at
        self.status = Bill.status_for(self.total, self.paid_amount)

    def apply_payment(self, amount: Decimal) -> None:
        self.paid_amount += amount
        self.status = Bill.status_for(self.total, self.paid_amount)

    @classmethod
    def from_breakdown(cls, subscriber_no: str, year: int, month: int, breakdown: BillBreakdown,
                       calculated_at: datetime) -> "Bill":
        return cls(
            subscriber_no=subscriber_no,
            year=year,
            month=month,
            phone_minutes=breakdown.phone_minutes,
            internet_mb=breakdown.internet_mb,
            phone_charge=breakdown.phone_charge,
            internet_charge=breakdown.internet_charge,
            total=breakdown.total,
            paid_amount=ZERO,
            status=Bill.status_for(breakdown.total, ZERO),
            calculated_at=calculated_at,
        )


@dataclass
class Payment:
    bill_id: int
    transaction_id: str
    amount: Decimal
    created_at: datetime
    id: int | None = None


@dataclass(frozen=True)
class Page[T]:
    items: list[T] = field(default_factory=list)
    page: int = 1
    page_size: int = 20
    total_items: int = 0

    @property
    def total_pages(self) -> int:
        return -(-self.total_items // self.page_size) if self.total_items else 0
