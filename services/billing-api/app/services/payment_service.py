from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from app.core.errors import BadRequestError, BusinessRuleError, ConflictError, NotFoundError
from app.domain.enums import BillStatus
from app.domain.models import Payment
from app.repositories.interfaces import IUnitOfWork
from app.services.interfaces import PaymentResult
from app.services.validation import ensure_period

CENT = Decimal("0.01")


class PaymentService:
    """Records simulated payments. No card data is processed."""

    def __init__(self, uow: IUnitOfWork, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._uow = uow
        self._clock = clock

    def pay_bill(
        self, subscriber_no: str, year: int, month: int, amount: Decimal, transaction_id: str
    ) -> PaymentResult:
        ensure_period(year, month)
        if amount <= 0:
            raise BadRequestError("amount must be greater than zero.")
        if amount != amount.quantize(CENT):
            raise BadRequestError("amount can have at most two decimal places.")
        if not transaction_id or not transaction_id.strip():
            raise BadRequestError("transactionId is required.")

        # Lock the bill row first, so two concurrent payments cannot both pass the checks below.
        bill = self._uow.bills.get_for_update(subscriber_no, year, month)
        if bill is None:
            raise NotFoundError(
                f"No calculated bill for subscriber {subscriber_no} in {year}-{month:02d}.", code="BILL_NOT_FOUND"
            )
        # Idempotency: a transaction id can be used exactly once. Checked while holding the lock; the
        # UNIQUE constraint on payments.transaction_id is the final guard (e.g. the same id on another bill).
        if self._uow.payments.exists(transaction_id):
            raise ConflictError(f"Transaction {transaction_id} was already processed.", code="DUPLICATE_TRANSACTION")
        if bill.status == BillStatus.PAID:
            raise BusinessRuleError("This bill is already fully paid.", code="BILL_ALREADY_PAID")
        if amount > bill.remaining_balance:
            raise BusinessRuleError(
                f"Amount {amount} exceeds the remaining balance {bill.remaining_balance}.",
                code="AMOUNT_EXCEEDS_BALANCE",
            )

        bill.apply_payment(amount)
        self._uow.bills.save(bill)
        self._uow.payments.add(
            Payment(bill_id=bill.id, transaction_id=transaction_id, amount=amount, created_at=self._clock())
        )
        self._uow.commit()

        return PaymentResult(
            transaction_id=transaction_id,
            paid_amount=amount,
            total_paid=bill.paid_amount,
            remaining_balance=bill.remaining_balance,
            bill_status=bill.status,
        )
