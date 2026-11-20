"""Business service contracts (the "ICourseService" layer in the lecture's class diagram).

Controllers depend on these interfaces, never on concrete classes, so the
implementation can be replaced (or faked in tests) through dependency injection.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.enums import BillStatus, UsageType
from app.domain.models import Bill, Page, UsageRecord
from app.domain.pricing import BillBreakdown


@dataclass(frozen=True)
class BillDetails:
    bill: Bill
    breakdown: BillBreakdown
    usage_records: Page[UsageRecord]


@dataclass(frozen=True)
class PaymentResult:
    transaction_id: str
    paid_amount: Decimal
    total_paid: Decimal
    remaining_balance: Decimal
    bill_status: BillStatus


class IUsageService(Protocol):
    def add_usage(self, subscriber_no: str, year: int, month: int, usage_type: UsageType) -> UsageRecord: ...


class IBillingService(Protocol):
    def calculate_bill(self, subscriber_no: str, year: int, month: int) -> Bill: ...

    def get_bill(self, subscriber_no: str, year: int, month: int) -> Bill: ...

    def get_bill_details(self, subscriber_no: str, year: int, month: int, page: int, page_size: int) -> BillDetails: ...


class IPaymentService(Protocol):
    def pay_bill(
        self, subscriber_no: str, year: int, month: int, amount: Decimal, transaction_id: str
    ) -> PaymentResult: ...
