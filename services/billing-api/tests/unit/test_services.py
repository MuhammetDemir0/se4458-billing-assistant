"""Business-layer tests with in-memory repositories (no database, no HTTP)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.errors import BadRequestError, BusinessRuleError, ConflictError, NotFoundError
from app.domain.enums import BillStatus, UsageType, UsageUnit
from app.services.billing_service import BillingService
from app.services.payment_service import PaymentService
from app.services.usage_service import UsageService
from tests.conftest import OWNER

FIXED_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def clock():
    return FIXED_NOW


def add_events(uow, usage_type, n, year=2026, month=10):
    service = UsageService(uow, clock)
    for _ in range(n):
        service.add_usage(OWNER, year, month, usage_type)


# ---------- UsageService ----------

def test_add_phone_usage_records_ten_minutes(uow):
    record = UsageService(uow, clock).add_usage(OWNER, 2026, 10, UsageType.PHONE)
    assert (record.quantity, record.unit) == (10, UsageUnit.MINUTE)
    assert record.id is not None
    assert uow.store.commits == 1


def test_add_internet_usage_records_one_mb(uow):
    record = UsageService(uow, clock).add_usage(OWNER, 2026, 10, UsageType.INTERNET)
    assert (record.quantity, record.unit) == (1, UsageUnit.MB)


def test_add_usage_unknown_subscriber(uow):
    with pytest.raises(NotFoundError) as exc:
        UsageService(uow, clock).add_usage("5559999999", 2026, 10, UsageType.PHONE)
    assert exc.value.code == "SUBSCRIBER_NOT_FOUND"


@pytest.mark.parametrize(("year", "month"), [(2026, 0), (2026, 13), (1999, 5)])
def test_add_usage_invalid_period(uow, year, month):
    with pytest.raises(BadRequestError):
        UsageService(uow, clock).add_usage(OWNER, year, month, UsageType.PHONE)


# ---------- BillingService ----------

def test_calculate_bill_from_usage(uow):
    add_events(uow, UsageType.PHONE, 135)       # 1,350 minutes
    add_events(uow, UsageType.INTERNET, 21_000)  # 21 GB
    bill = BillingService(uow, clock).calculate_bill(OWNER, 2026, 10)
    assert bill.phone_minutes == 1350 and bill.internet_mb == 21_000
    assert bill.total == Decimal("70")           # $10 phone + $60 internet
    assert bill.status == BillStatus.UNPAID
    assert bill.remaining_balance == Decimal("70")


def test_calculate_bill_without_usage_charges_base_price(uow):
    bill = BillingService(uow, clock).calculate_bill(OWNER, 2026, 9)
    assert bill.total == Decimal("50")


def test_recalculate_keeps_payments(uow):
    billing = BillingService(uow, clock)
    billing.calculate_bill(OWNER, 2026, 10)  # $50
    PaymentService(uow, clock).pay_bill(OWNER, 2026, 10, Decimal("20"), "tx-recalc-0001")
    add_events(uow, UsageType.PHONE, 101)    # 1,010 minutes -> +$10
    bill = billing.calculate_bill(OWNER, 2026, 10)
    assert bill.total == Decimal("60")
    assert bill.paid_amount == Decimal("20")
    assert bill.status == BillStatus.PARTIALLY_PAID


def test_calculate_bill_unknown_subscriber(uow):
    with pytest.raises(NotFoundError):
        BillingService(uow, clock).calculate_bill("5559999999", 2026, 10)


def test_get_bill_not_calculated(uow):
    with pytest.raises(NotFoundError) as exc:
        BillingService(uow, clock).get_bill(OWNER, 2026, 10)
    assert exc.value.code == "BILL_NOT_FOUND"


def test_bill_details_paging_and_breakdown(uow):
    add_events(uow, UsageType.PHONE, 5)
    add_events(uow, UsageType.INTERNET, 7)
    billing = BillingService(uow, clock)
    billing.calculate_bill(OWNER, 2026, 10)
    details = billing.get_bill_details(OWNER, 2026, 10, page=2, page_size=5)
    assert details.usage_records.total_items == 12
    assert details.usage_records.total_pages == 3
    assert len(details.usage_records.items) == 5
    assert details.breakdown.phone_minutes == 50
    assert details.breakdown.internet_mb == 7


def test_bill_details_rejects_bad_page_size(uow):
    with pytest.raises(BadRequestError):
        BillingService(uow, clock).get_bill_details(OWNER, 2026, 10, page=1, page_size=500)


# ---------- PaymentService ----------

@pytest.fixture
def billed(uow):
    add_events(uow, UsageType.INTERNET, 35_000)  # $70
    BillingService(uow, clock).calculate_bill(OWNER, 2026, 10)
    return uow


def test_partial_payment(billed):
    result = PaymentService(billed, clock).pay_bill(OWNER, 2026, 10, Decimal("30.00"), "tx-partial-01")
    assert result.paid_amount == Decimal("30.00")
    assert result.remaining_balance == Decimal("40.00")
    assert result.bill_status == BillStatus.PARTIALLY_PAID


def test_full_payment_marks_bill_paid(billed):
    result = PaymentService(billed, clock).pay_bill(OWNER, 2026, 10, Decimal("70"), "tx-full-0001")
    assert result.remaining_balance == Decimal("0")
    assert result.bill_status == BillStatus.PAID


def test_duplicate_transaction_rejected_and_balance_preserved(billed):
    payments = PaymentService(billed, clock)
    payments.pay_bill(OWNER, 2026, 10, Decimal("10"), "tx-dup-00001")
    with pytest.raises(ConflictError) as exc:
        payments.pay_bill(OWNER, 2026, 10, Decimal("10"), "tx-dup-00001")
    assert exc.value.code == "DUPLICATE_TRANSACTION"
    assert BillingService(billed, clock).get_bill(OWNER, 2026, 10).paid_amount == Decimal("10")


def test_amount_exceeding_balance_rejected(billed):
    with pytest.raises(BusinessRuleError) as exc:
        PaymentService(billed, clock).pay_bill(OWNER, 2026, 10, Decimal("70.01"), "tx-over-0001")
    assert exc.value.code == "AMOUNT_EXCEEDS_BALANCE"


def test_payment_on_paid_bill_rejected(billed):
    payments = PaymentService(billed, clock)
    payments.pay_bill(OWNER, 2026, 10, Decimal("70"), "tx-paid-0001")
    with pytest.raises(BusinessRuleError) as exc:
        payments.pay_bill(OWNER, 2026, 10, Decimal("1"), "tx-paid-0002")
    assert exc.value.code == "BILL_ALREADY_PAID"


@pytest.mark.parametrize("amount", ["0", "-5", "10.001"])
def test_invalid_amount_rejected(billed, amount):
    with pytest.raises(BadRequestError):
        PaymentService(billed, clock).pay_bill(OWNER, 2026, 10, Decimal(amount), "tx-bad-amount")


def test_payment_for_missing_bill(uow):
    with pytest.raises(NotFoundError):
        PaymentService(uow, clock).pay_bill(OWNER, 2026, 1, Decimal("10"), "tx-missing-01")
