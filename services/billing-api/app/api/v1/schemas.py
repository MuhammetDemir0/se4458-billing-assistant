"""Version 1 HTTP contract (DTOs).

DTOs are separate from domain entities and ORM rows: they define exactly what
clients send and receive, in camelCase JSON, and are what Swagger documents.
Mapping functions at the bottom convert domain objects into DTOs.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel

from app.domain.enums import BillStatus, UsageType, UsageUnit
from app.domain.models import Bill, UsageRecord
from app.domain.pricing import (
    INTERNET_BASE_PRICE,
    INTERNET_BLOCK_MB,
    INTERNET_BLOCK_PRICE,
    INTERNET_INCLUDED_MB,
    PHONE_BLOCK_MINUTES,
    PHONE_BLOCK_PRICE,
    PHONE_FREE_MINUTES,
)
from app.services.interfaces import BillDetails, PaymentResult

# Money is a Decimal internally and a JSON number with two decimals on the wire.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class Dto(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


# ---------- requests ----------

class AddUsageRequest(Dto):
    month: int = Field(ge=1, le=12, examples=[10])
    year: int = Field(ge=2000, le=2100, examples=[2026])
    usage_type: UsageType = Field(description="PHONE adds 10 minutes, INTERNET adds 1 MB.", examples=["PHONE"])


class PayBillRequest(Dto):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, examples=[40.00])
    transaction_id: str = Field(
        min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
        description="Client-generated idempotency key. Reusing it is rejected with 409.",
        examples=["7f1c2b9e-4d1a-4c3e-9a51-2f9a0d3c1b77"],
    )


# ---------- responses ----------

class UsageRecordDto(Dto):
    usage_id: int | None
    usage_type: UsageType
    quantity: int
    unit: UsageUnit
    recorded_at: datetime


class AddUsageResponse(Dto):
    transaction_status: Literal["SUCCESS"] = "SUCCESS"
    subscriber_no: str
    year: int
    month: int
    record: UsageRecordDto


class BillSummaryResponse(Dto):
    subscriber_no: str
    year: int
    month: int
    currency: Literal["USD"] = "USD"
    bill_total: Money
    paid_amount: Money
    remaining_balance: Money
    status: BillStatus
    calculated_at: datetime


class CalculateBillResponse(Dto):
    transaction_status: Literal["SUCCESS"] = "SUCCESS"
    bill: BillSummaryResponse


class PhoneChargeDto(Dto):
    minutes_used: int
    included_minutes: int = PHONE_FREE_MINUTES
    block_size_minutes: int = PHONE_BLOCK_MINUTES
    block_price: Money = PHONE_BLOCK_PRICE
    extra_blocks: int
    amount: Money


class InternetChargeDto(Dto):
    mb_used: int
    gb_used: float
    included_mb: int = INTERNET_INCLUDED_MB
    base_price: Money = INTERNET_BASE_PRICE
    block_size_mb: int = INTERNET_BLOCK_MB
    block_price: Money = INTERNET_BLOCK_PRICE
    extra_blocks: int
    amount: Money


class ChargeBreakdownDto(Dto):
    phone: PhoneChargeDto
    internet: InternetChargeDto
    total: Money


class UsageRecordPageDto(Dto):
    items: list[UsageRecordDto]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class BillDetailsResponse(BillSummaryResponse):
    charges: ChargeBreakdownDto
    usage_records: UsageRecordPageDto


class PayBillResponse(Dto):
    payment_status: Literal["SUCCESS"] = "SUCCESS"
    transaction_id: str
    paid_amount: Money
    total_paid: Money
    remaining_balance: Money
    bill_status: BillStatus


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    code: str
    instance: str
    requestId: str | None = None
    errors: list[dict] | None = None


# ---------- mappers: domain -> DTO ----------

def to_usage_dto(record: UsageRecord) -> UsageRecordDto:
    return UsageRecordDto(
        usage_id=record.id, usage_type=record.usage_type, quantity=record.quantity,
        unit=record.unit, recorded_at=record.recorded_at,
    )


def to_bill_summary(bill: Bill) -> BillSummaryResponse:
    return BillSummaryResponse(
        subscriber_no=bill.subscriber_no, year=bill.year, month=bill.month,
        bill_total=bill.total, paid_amount=bill.paid_amount, remaining_balance=bill.remaining_balance,
        status=bill.status, calculated_at=bill.calculated_at,
    )


def to_bill_details(details: BillDetails) -> BillDetailsResponse:
    b, bd, page = details.bill, details.breakdown, details.usage_records
    return BillDetailsResponse(
        **to_bill_summary(b).model_dump(),
        charges=ChargeBreakdownDto(
            phone=PhoneChargeDto(minutes_used=bd.phone_minutes, extra_blocks=bd.phone_extra_blocks,
                                 amount=bd.phone_charge),
            internet=InternetChargeDto(mb_used=bd.internet_mb, gb_used=float(bd.internet_gb),
                                       extra_blocks=bd.internet_extra_blocks, amount=bd.internet_charge),
            total=bd.total,
        ),
        usage_records=UsageRecordPageDto(
            items=[to_usage_dto(r) for r in page.items], page=page.page, page_size=page.page_size,
            total_items=page.total_items, total_pages=page.total_pages,
        ),
    )


def to_payment_response(result: PaymentResult) -> PayBillResponse:
    return PayBillResponse(
        transaction_id=result.transaction_id, paid_amount=result.paid_amount, total_paid=result.total_paid,
        remaining_balance=result.remaining_balance, bill_status=result.bill_status,
    )
