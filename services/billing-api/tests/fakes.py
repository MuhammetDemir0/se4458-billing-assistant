"""In-memory implementations of the repository interfaces.

Because services depend on interfaces (not on SQLAlchemy), the whole business
layer can be tested without a database. The fakes keep their data in a shared
InMemoryStore so it survives across requests within one test.
"""

from dataclasses import dataclass, field, replace
from itertools import count

from app.domain.enums import UsageType
from app.domain.models import Bill, Page, Payment, Subscriber, UsageRecord, UsageTotals


@dataclass
class InMemoryStore:
    subscribers: dict[str, Subscriber] = field(default_factory=dict)
    usage: list[UsageRecord] = field(default_factory=list)
    bills: dict[tuple[str, int, int], Bill] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)
    ids: count = field(default_factory=lambda: count(1))
    commits: int = 0


class FakeSubscriberRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._st = store

    def get(self, subscriber_no):
        return self._st.subscribers.get(subscriber_no)


class FakeUsageRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._st = store

    def add(self, record):
        saved = replace(record, id=next(self._st.ids))
        self._st.usage.append(saved)
        return saved

    def _period(self, no, year, month):
        return [u for u in self._st.usage if (u.subscriber_no, u.year, u.month) == (no, year, month)]

    def totals(self, no, year, month):
        records = self._period(no, year, month)
        return UsageTotals(
            phone_minutes=sum(r.quantity for r in records if r.usage_type == UsageType.PHONE),
            internet_mb=sum(r.quantity for r in records if r.usage_type == UsageType.INTERNET),
        )

    def list_page(self, no, year, month, page, page_size):
        records = self._period(no, year, month)
        start = (page - 1) * page_size
        return Page(items=records[start:start + page_size], page=page, page_size=page_size,
                    total_items=len(records))


class FakeBillRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._st = store

    def get(self, no, year, month):
        bill = self._st.bills.get((no, year, month))
        return replace(bill) if bill else None  # copy, like a fresh DB read

    get_for_update = get

    def save(self, bill):
        saved = replace(bill, id=bill.id or next(self._st.ids))
        self._st.bills[(bill.subscriber_no, bill.year, bill.month)] = saved
        return replace(saved)


class FakePaymentRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._st = store

    def exists(self, transaction_id):
        return transaction_id in self._st.payments

    def add(self, payment):
        saved = replace(payment, id=next(self._st.ids))
        self._st.payments[payment.transaction_id] = saved
        return saved


class FakeUnitOfWork:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store
        self.subscribers = FakeSubscriberRepository(store)
        self.usage = FakeUsageRepository(store)
        self.bills = FakeBillRepository(store)
        self.payments = FakePaymentRepository(store)

    def commit(self):
        self.store.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass
