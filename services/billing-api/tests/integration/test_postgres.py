"""Integration tests against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set).

  docker compose up -d postgres
  TEST_DATABASE_URL=postgresql+psycopg://billing:billing@localhost:5432/billing_test pytest -m integration

They check what the in-memory fakes cannot: the migration, SQL aggregation,
row locking under concurrent payments and the unique transaction id constraint.
"""

import os
import threading
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.errors import BusinessRuleError, ConflictError
from app.db.session import build_session_factory
from app.domain.enums import UsageType
from app.repositories.sqlalchemy_repositories import SqlAlchemyUnitOfWork
from app.services.billing_service import BillingService
from app.services.payment_service import PaymentService
from app.services.usage_service import UsageService

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")]
NO = "5551000001"


@pytest.fixture(scope="module")
def session_factory():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = DB_URL
    command.upgrade(cfg, "head")
    yield build_session_factory(engine)
    engine.dispose()


_open_uows: list[SqlAlchemyUnitOfWork] = []


@pytest.fixture(autouse=True)
def clean(session_factory):
    with session_factory() as s:
        s.execute(text("TRUNCATE payments, bills, usage_records, subscribers RESTART IDENTITY CASCADE"))
        s.execute(text("INSERT INTO subscribers (subscriber_no, full_name) VALUES (:no, 'Test User')"), {"no": NO})
        s.commit()
    yield
    # Like the per-request get_uow dependency in the API: always close the session afterwards.
    while _open_uows:
        _open_uows.pop().close()


def uow(session_factory):
    unit = SqlAlchemyUnitOfWork(session_factory())
    _open_uows.append(unit)
    return unit


def test_usage_aggregation_and_bill_storage(session_factory):
    for _ in range(101):
        UsageService(uow(session_factory)).add_usage(NO, 2026, 10, UsageType.PHONE)
    for _ in range(3):
        UsageService(uow(session_factory)).add_usage(NO, 2026, 10, UsageType.INTERNET)
    bill = BillingService(uow(session_factory)).calculate_bill(NO, 2026, 10)
    assert (bill.phone_minutes, bill.internet_mb, bill.total) == (1010, 3, Decimal("60.00"))
    details = BillingService(uow(session_factory)).get_bill_details(NO, 2026, 10, page=2, page_size=50)
    assert details.usage_records.total_items == 104
    assert len(details.usage_records.items) == 50


def test_duplicate_transaction_id_rejected(session_factory):
    BillingService(uow(session_factory)).calculate_bill(NO, 2026, 10)
    PaymentService(uow(session_factory)).pay_bill(NO, 2026, 10, Decimal("10"), "it-dup-000001")
    with pytest.raises(ConflictError):
        PaymentService(uow(session_factory)).pay_bill(NO, 2026, 10, Decimal("10"), "it-dup-000001")


def test_concurrent_payments_cannot_overpay(session_factory):
    """Two simultaneous $30 payments on a $50 bill: the row lock lets exactly one succeed."""
    BillingService(uow(session_factory)).calculate_bill(NO, 2026, 10)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def attempt(tx: str) -> None:
        service = PaymentService(uow(session_factory))
        barrier.wait()
        try:
            service.pay_bill(NO, 2026, 10, Decimal("30"), tx)
            outcomes.append("ok")
        except BusinessRuleError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=attempt, args=(f"it-race-{i:05d}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["ok", "rejected"]
    bill = BillingService(uow(session_factory)).get_bill(NO, 2026, 10)
    assert bill.paid_amount == Decimal("30.00")


def test_rotated_password_is_refreshed_on_connect():
    """Simulates an RDS password rotation: the cached password is rejected, the hook refreshes and retries."""
    from app.core.config import Settings
    from app.db.session import build_engine, ping

    class RotatedSecret:
        refreshed = False

        def get(self, refresh: bool = False):
            if refresh:
                self.refreshed = True
                return "billing", "billing"          # current password
            return "billing", "stale-password"       # value cached before the rotation

    secret = RotatedSecret()
    engine = build_engine(Settings(database_url=DB_URL), credentials=secret)
    try:
        assert ping(engine) and secret.refreshed
    finally:
        engine.dispose()


def run_concurrently(session_factory, calls) -> list[str]:
    """Run pay_bill calls at the same moment; record 'ok', 'conflict' or any other exception name."""
    outcomes: list[str] = []
    barrier = threading.Barrier(len(calls))

    def attempt(args) -> None:
        service = PaymentService(uow(session_factory))
        barrier.wait()
        try:
            service.pay_bill(*args)
            outcomes.append("ok")
        except ConflictError:
            outcomes.append("conflict")
        except Exception as exc:  # noqa: BLE001 - an unexpected error (e.g. a 500) must fail the test
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=attempt, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(outcomes)


def test_concurrent_retry_with_same_transaction_id_is_a_conflict_not_an_error(session_factory):
    """A client retrying the same payment twice at once: one succeeds, the other gets 409 (never 500)."""
    BillingService(uow(session_factory)).calculate_bill(NO, 2026, 10)  # $50
    call = (NO, 2026, 10, Decimal("10"), "it-same-tx-0001")
    assert run_concurrently(session_factory, [call, call]) == ["conflict", "ok"]
    assert BillingService(uow(session_factory)).get_bill(NO, 2026, 10).paid_amount == Decimal("10.00")


def test_same_transaction_id_on_two_bills_is_a_conflict(session_factory):
    """Different bills are locked separately; the UNIQUE constraint still rejects the reused id."""
    BillingService(uow(session_factory)).calculate_bill(NO, 2026, 9)
    BillingService(uow(session_factory)).calculate_bill(NO, 2026, 10)
    calls = [(NO, 2026, 9, Decimal("10"), "it-two-bills-01"), (NO, 2026, 10, Decimal("10"), "it-two-bills-01")]
    assert run_concurrently(session_factory, calls) == ["conflict", "ok"]
