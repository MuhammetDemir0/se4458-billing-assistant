"""Load demo data for the instructor and the demo video.

    python -m app.seed            # idempotent: adds what is missing
    python -m app.seed --reset    # wipe all billing data first (restores the initial demo state)

Idempotent: subscribers are upserted, usage is only inserted for periods that
have none, and bills are (re)calculated through BillingService, i.e. the same
business logic the API uses. Usage events are inserted in bulk with
generate_series because one Add Usage call records only 10 minutes or 1 MB
(35 GB would be 35,000 API calls).

Scenario data (all fictitious):
  5551000001  Sep 2026:   800 min, 18 GB -> $50  (paid in full)
              Oct 2026: 1,350 min, 35 GB -> $80  ("why is my October bill higher?")
              Nov 2026:   300 min,  2 GB -> usage only, bill not calculated yet
  5551000002  Oct 2026: 2,100 min, 12 GB -> $70  (used to show cross-account denial)
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.repositories.sqlalchemy_repositories import SqlAlchemyUnitOfWork
from app.services.billing_service import BillingService
from app.services.payment_service import PaymentService

SUBSCRIBERS = {"5551000001": "Deniz Yilmaz", "5551000002": "Ece Kaya"}

# (subscriber, year, month, phone_minutes, internet_mb, calculate_bill)
USAGE = [
    ("5551000001", 2026, 9, 800, 18_000, True),
    ("5551000001", 2026, 10, 1_350, 35_000, True),
    ("5551000001", 2026, 11, 300, 2_000, False),
    ("5551000002", 2026, 10, 2_100, 12_000, True),
]

INSERT_EVENTS = text(
    """
    INSERT INTO usage_records (subscriber_no, year, month, usage_type, quantity, unit, recorded_at)
    SELECT :no, :year, :month, :usage_type, :quantity, :unit,
           make_timestamptz(:year, :month, 1, 0, 0, 0, 'UTC')
             + (g * interval '1 second' * (2419200 / GREATEST(:events, 1)))
    FROM generate_series(0, :events - 1) AS g
    """
)


def seed(reset: bool = False) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    if reset:
        # Demo reset (e.g. before a demo video): removes ALL billing data, then reloads the scenario data.
        with session_factory() as session:
            session.execute(text("TRUNCATE payments, bills, usage_records, subscribers RESTART IDENTITY CASCADE"))
            session.commit()
        print("demo data reset: all subscribers, usage, bills and payments removed")

    with session_factory() as session:
        for no, name in SUBSCRIBERS.items():
            session.execute(
                text("INSERT INTO subscribers (subscriber_no, full_name) VALUES (:no, :name) "
                     "ON CONFLICT (subscriber_no) DO UPDATE SET full_name = EXCLUDED.full_name"),
                {"no": no, "name": name},
            )
        for no, year, month, minutes, mb, _ in USAGE:
            exists = session.execute(
                text("SELECT 1 FROM usage_records WHERE subscriber_no=:no AND year=:y AND month=:m LIMIT 1"),
                {"no": no, "y": year, "m": month},
            ).first()
            if exists:
                continue
            for usage_type, quantity, unit, events in (("PHONE", 10, "MINUTE", minutes // 10),
                                                      ("INTERNET", 1, "MB", mb)):
                if events:
                    session.execute(INSERT_EVENTS, {"no": no, "year": year, "month": month, "usage_type": usage_type,
                                                    "quantity": quantity, "unit": unit, "events": events})
        session.commit()

    for no, year, month, _, _, calculate in USAGE:
        if not calculate:
            continue
        with session_factory() as session:
            bill = BillingService(SqlAlchemyUnitOfWork(session)).calculate_bill(no, year, month)
            print(f"bill {no} {year}-{month:02d}: total={bill.total} paid={bill.paid_amount} status={bill.status}")

    # September is already paid in full, so "pay my bill" naturally targets October.
    with session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        sep = uow.bills.get("5551000001", 2026, 9)
        if sep and sep.remaining_balance > 0:
            PaymentService(uow).pay_bill("5551000001", 2026, 9, sep.remaining_balance, "seed-sep-2026-full")
            print("seeded full payment for 5551000001 2026-09")

    engine.dispose()
    print(f"seed completed at {datetime.now(UTC).isoformat()} (amounts in USD, e.g. {Decimal('80.00')})")


if __name__ == "__main__":
    import sys

    seed(reset="--reset" in sys.argv[1:])
