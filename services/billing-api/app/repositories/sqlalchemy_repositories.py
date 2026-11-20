"""PostgreSQL implementation of the repository interfaces (SQLAlchemy 2.0 style).

Repositories translate between ORM rows and domain entities, so no ORM object
ever leaves this module.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.orm_models import BillRow, PaymentRow, SubscriberRow, UsageRecordRow
from app.domain.enums import BillStatus, UsageType, UsageUnit
from app.domain.models import Bill, Page, Payment, Subscriber, UsageRecord, UsageTotals


def _flush(session: Session, code: str, detail: str) -> None:
    """Write pending changes; a violated UNIQUE constraint becomes a 409 instead of a 500.

    This covers races that application checks cannot see, e.g. two requests creating the
    same bill period or using the same transaction id at the same moment.
    """
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(detail, code=code) from exc


def _to_bill(row: BillRow) -> Bill:
    return Bill(
        id=row.id,
        subscriber_no=row.subscriber_no,
        year=row.year,
        month=row.month,
        phone_minutes=row.phone_minutes,
        internet_mb=row.internet_mb,
        phone_charge=Decimal(row.phone_charge),
        internet_charge=Decimal(row.internet_charge),
        total=Decimal(row.total),
        paid_amount=Decimal(row.paid_amount),
        status=BillStatus(row.status),
        calculated_at=row.calculated_at,
    )


def _to_usage(row: UsageRecordRow) -> UsageRecord:
    return UsageRecord(
        id=row.id,
        subscriber_no=row.subscriber_no,
        year=row.year,
        month=row.month,
        usage_type=UsageType(row.usage_type),
        quantity=row.quantity,
        unit=UsageUnit(row.unit),
        recorded_at=row.recorded_at,
    )


class SqlAlchemySubscriberRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, subscriber_no: str) -> Subscriber | None:
        row = self._s.get(SubscriberRow, subscriber_no)
        return Subscriber(row.subscriber_no, row.full_name) if row else None


class SqlAlchemyUsageRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, record: UsageRecord) -> UsageRecord:
        row = UsageRecordRow(
            subscriber_no=record.subscriber_no,
            year=record.year,
            month=record.month,
            usage_type=record.usage_type.value,
            quantity=record.quantity,
            unit=record.unit.value,
            recorded_at=record.recorded_at,
        )
        self._s.add(row)
        self._s.flush()
        return _to_usage(row)

    def totals(self, subscriber_no: str, year: int, month: int) -> UsageTotals:
        stmt = (
            select(UsageRecordRow.usage_type, func.coalesce(func.sum(UsageRecordRow.quantity), 0))
            .where(
                UsageRecordRow.subscriber_no == subscriber_no,
                UsageRecordRow.year == year,
                UsageRecordRow.month == month,
            )
            .group_by(UsageRecordRow.usage_type)
        )
        sums = {usage_type: int(total) for usage_type, total in self._s.execute(stmt)}
        return UsageTotals(
            phone_minutes=sums.get(UsageType.PHONE.value, 0),
            internet_mb=sums.get(UsageType.INTERNET.value, 0),
        )

    def list_page(self, subscriber_no: str, year: int, month: int, page: int, page_size: int) -> Page[UsageRecord]:
        where = (
            UsageRecordRow.subscriber_no == subscriber_no,
            UsageRecordRow.year == year,
            UsageRecordRow.month == month,
        )
        total = self._s.scalar(select(func.count()).select_from(UsageRecordRow).where(*where)) or 0
        rows = self._s.scalars(
            select(UsageRecordRow)
            .where(*where)
            .order_by(UsageRecordRow.recorded_at, UsageRecordRow.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return Page(items=[_to_usage(r) for r in rows], page=page, page_size=page_size, total_items=total)


class SqlAlchemyBillRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def _select(self, subscriber_no: str, year: int, month: int):
        return select(BillRow).where(
            BillRow.subscriber_no == subscriber_no, BillRow.year == year, BillRow.month == month
        )

    def get(self, subscriber_no: str, year: int, month: int) -> Bill | None:
        row = self._s.scalar(self._select(subscriber_no, year, month))
        return _to_bill(row) if row else None

    def get_for_update(self, subscriber_no: str, year: int, month: int) -> Bill | None:
        row = self._s.scalar(self._select(subscriber_no, year, month).with_for_update())
        return _to_bill(row) if row else None

    def save(self, bill: Bill) -> Bill:
        row = self._s.get(BillRow, bill.id) if bill.id is not None else None
        if row is None:
            row = BillRow(subscriber_no=bill.subscriber_no, year=bill.year, month=bill.month)
            self._s.add(row)
        row.phone_minutes = bill.phone_minutes
        row.internet_mb = bill.internet_mb
        row.phone_charge = bill.phone_charge
        row.internet_charge = bill.internet_charge
        row.total = bill.total
        row.paid_amount = bill.paid_amount
        row.status = bill.status.value
        row.calculated_at = bill.calculated_at
        _flush(self._s, "DUPLICATE_RECORD", "The bill was changed by a concurrent request. Please retry.")
        return _to_bill(row)


class SqlAlchemyPaymentRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def exists(self, transaction_id: str) -> bool:
        return self._s.scalar(select(PaymentRow.id).where(PaymentRow.transaction_id == transaction_id)) is not None

    def add(self, payment: Payment) -> Payment:
        row = PaymentRow(
            bill_id=payment.bill_id,
            transaction_id=payment.transaction_id,
            amount=payment.amount,
            created_at=payment.created_at,
        )
        self._s.add(row)
        _flush(self._s, "DUPLICATE_TRANSACTION", f"Transaction {payment.transaction_id} was already processed.")
        return Payment(id=row.id, bill_id=row.bill_id, transaction_id=row.transaction_id,
                       amount=Decimal(row.amount), created_at=row.created_at)


class SqlAlchemyUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.subscribers = SqlAlchemySubscriberRepository(session)
        self.usage = SqlAlchemyUsageRepository(session)
        self.bills = SqlAlchemyBillRepository(session)
        self.payments = SqlAlchemyPaymentRepository(session)

    def commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            # A concurrent request inserted the same transaction id or bill period first.
            raise ConflictError("The operation conflicts with an existing record.", code="DUPLICATE_RECORD") from exc

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
