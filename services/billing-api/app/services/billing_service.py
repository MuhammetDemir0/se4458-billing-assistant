from collections.abc import Callable
from datetime import UTC, datetime

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.models import Bill
from app.domain.pricing import calculate_bill
from app.repositories.interfaces import IUnitOfWork
from app.services.interfaces import BillDetails
from app.services.validation import ensure_period

MAX_PAGE_SIZE = 100


class BillingService:
    def __init__(self, uow: IUnitOfWork, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._uow = uow
        self._clock = clock

    def calculate_bill(self, subscriber_no: str, year: int, month: int) -> Bill:
        """Calculate and store the bill from recorded usage.

        Idempotent: running it again recalculates from the same usage and keeps
        the payments already made. A month with no usage still costs the $50
        internet base price (guide 3.1).
        """
        ensure_period(year, month)
        if self._uow.subscribers.get(subscriber_no) is None:
            raise NotFoundError(f"Subscriber {subscriber_no} does not exist.", code="SUBSCRIBER_NOT_FOUND")

        totals = self._uow.usage.totals(subscriber_no, year, month)
        breakdown = calculate_bill(totals.phone_minutes, totals.internet_mb)
        now = self._clock()

        existing = self._uow.bills.get_for_update(subscriber_no, year, month)
        if existing is None:
            bill = Bill.from_breakdown(subscriber_no, year, month, breakdown, now)
        else:
            if breakdown.total < existing.paid_amount:
                raise ConflictError(
                    "Recalculated total would be lower than the amount already paid.",
                    code="TOTAL_BELOW_PAID_AMOUNT",
                )
            existing.apply_calculation(breakdown, now)
            bill = existing

        saved = self._uow.bills.save(bill)
        self._uow.commit()
        return saved

    def get_bill(self, subscriber_no: str, year: int, month: int) -> Bill:
        ensure_period(year, month)
        bill = self._uow.bills.get(subscriber_no, year, month)
        if bill is None:
            raise NotFoundError(
                f"No calculated bill for subscriber {subscriber_no} in {year}-{month:02d}.",
                code="BILL_NOT_FOUND",
            )
        return bill

    def get_bill_details(self, subscriber_no: str, year: int, month: int, page: int, page_size: int) -> BillDetails:
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise BadRequestError(f"page must be >= 1 and pageSize between 1 and {MAX_PAGE_SIZE}.")
        bill = self.get_bill(subscriber_no, year, month)
        # The breakdown is derived from the *stored* bill, so details and summary always agree.
        breakdown = calculate_bill(bill.phone_minutes, bill.internet_mb)
        records = self._uow.usage.list_page(subscriber_no, year, month, page, page_size)
        return BillDetails(bill=bill, breakdown=breakdown, usage_records=records)
