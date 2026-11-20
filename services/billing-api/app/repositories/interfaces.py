"""Data-access contracts (the "CourseAccess" layer in the lecture's class diagram).

Services depend only on these interfaces. The PostgreSQL implementation lives
in app/repositories/sqlalchemy_repositories.py; tests use in-memory fakes.
"""

from typing import Protocol

from app.domain.models import Bill, Page, Payment, Subscriber, UsageRecord, UsageTotals


class ISubscriberRepository(Protocol):
    def get(self, subscriber_no: str) -> Subscriber | None: ...


class IUsageRepository(Protocol):
    def add(self, record: UsageRecord) -> UsageRecord: ...

    def totals(self, subscriber_no: str, year: int, month: int) -> UsageTotals: ...

    def list_page(self, subscriber_no: str, year: int, month: int, page: int, page_size: int) -> Page[UsageRecord]: ...


class IBillRepository(Protocol):
    def get(self, subscriber_no: str, year: int, month: int) -> Bill | None: ...

    def get_for_update(self, subscriber_no: str, year: int, month: int) -> Bill | None:
        """Read a bill and lock its row until the transaction ends (SELECT ... FOR UPDATE)."""
        ...

    def save(self, bill: Bill) -> Bill: ...


class IPaymentRepository(Protocol):
    def exists(self, transaction_id: str) -> bool: ...

    def add(self, payment: Payment) -> Payment: ...


class IUnitOfWork(Protocol):
    """One business transaction: either every change is committed or none is."""

    subscribers: ISubscriberRepository
    usage: IUsageRepository
    bills: IBillRepository
    payments: IPaymentRepository

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
