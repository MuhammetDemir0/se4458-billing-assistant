"""SQLAlchemy ORM models: the physical PostgreSQL schema (see docs/er-diagram.md)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(12, 2)


class Base(DeclarativeBase):
    pass


class SubscriberRow(Base):
    __tablename__ = "subscribers"

    subscriber_no: Mapped[str] = mapped_column(String(15), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageRecordRow(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_usage_month"),
        CheckConstraint("quantity > 0", name="ck_usage_quantity"),
        CheckConstraint("usage_type IN ('PHONE','INTERNET')", name="ck_usage_type"),
        CheckConstraint("unit IN ('MINUTE','MB')", name="ck_usage_unit"),
        Index("ix_usage_subscriber_period", "subscriber_no", "year", "month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscriber_no: Mapped[str] = mapped_column(ForeignKey("subscribers.subscriber_no"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    usage_type: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(10))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BillRow(Base):
    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("subscriber_no", "year", "month", name="uq_bill_subscriber_period"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_bill_month"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= total", name="ck_bill_paid_range"),
        CheckConstraint("status IN ('UNPAID','PARTIALLY_PAID','PAID')", name="ck_bill_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscriber_no: Mapped[str] = mapped_column(ForeignKey("subscribers.subscriber_no"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    phone_minutes: Mapped[int] = mapped_column(Integer)
    internet_mb: Mapped[int] = mapped_column(Integer)
    phone_charge: Mapped[Decimal] = mapped_column(MONEY)
    internet_charge: Mapped[Decimal] = mapped_column(MONEY)
    total: Mapped[Decimal] = mapped_column(MONEY)
    paid_amount: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentRow(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_payment_amount"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
