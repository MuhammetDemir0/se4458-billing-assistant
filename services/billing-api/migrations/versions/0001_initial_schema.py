"""Initial billing schema: subscribers, usage_records, bills, payments.

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

MONEY = sa.Numeric(12, 2)


def upgrade() -> None:
    op.create_table(
        "subscribers",
        sa.Column("subscriber_no", sa.String(15), primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("subscriber_no", sa.String(15), sa.ForeignKey("subscribers.subscriber_no"), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("usage_type", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit", sa.String(10), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_usage_month"),
        sa.CheckConstraint("quantity > 0", name="ck_usage_quantity"),
        sa.CheckConstraint("usage_type IN ('PHONE','INTERNET')", name="ck_usage_type"),
        sa.CheckConstraint("unit IN ('MINUTE','MB')", name="ck_usage_unit"),
    )
    op.create_index("ix_usage_subscriber_period", "usage_records", ["subscriber_no", "year", "month"])

    op.create_table(
        "bills",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("subscriber_no", sa.String(15), sa.ForeignKey("subscribers.subscriber_no"), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("phone_minutes", sa.Integer, nullable=False),
        sa.Column("internet_mb", sa.Integer, nullable=False),
        sa.Column("phone_charge", MONEY, nullable=False),
        sa.Column("internet_charge", MONEY, nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("paid_amount", MONEY, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subscriber_no", "year", "month", name="uq_bill_subscriber_period"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_bill_month"),
        sa.CheckConstraint("paid_amount >= 0 AND paid_amount <= total", name="ck_bill_paid_range"),
        sa.CheckConstraint("status IN ('UNPAID','PARTIALLY_PAID','PAID')", name="ck_bill_status"),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("bill_id", sa.Integer, sa.ForeignKey("bills.id"), nullable=False),
        sa.Column("transaction_id", sa.String(64), nullable=False, unique=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_payment_amount"),
    )
    op.create_index("ix_payments_bill_id", "payments", ["bill_id"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("bills")
    op.drop_index("ix_usage_subscriber_period", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_table("subscribers")
