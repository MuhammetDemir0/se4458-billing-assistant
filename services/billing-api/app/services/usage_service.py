from collections.abc import Callable
from datetime import UTC, datetime

from app.core.errors import NotFoundError
from app.domain.enums import UsageType
from app.domain.models import UsageRecord
from app.domain.pricing import USAGE_PER_EVENT
from app.repositories.interfaces import IUnitOfWork
from app.services.validation import ensure_period


class UsageService:
    """Records usage events. Each call adds a fixed unit: 10 minutes or 1 MB."""

    def __init__(self, uow: IUnitOfWork, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._uow = uow
        self._clock = clock

    def add_usage(self, subscriber_no: str, year: int, month: int, usage_type: UsageType) -> UsageRecord:
        ensure_period(year, month)
        if self._uow.subscribers.get(subscriber_no) is None:
            raise NotFoundError(f"Subscriber {subscriber_no} does not exist.", code="SUBSCRIBER_NOT_FOUND")

        quantity, unit = USAGE_PER_EVENT[usage_type]
        record = self._uow.usage.add(
            UsageRecord(
                subscriber_no=subscriber_no,
                year=year,
                month=month,
                usage_type=usage_type,
                quantity=quantity,
                unit=unit,
                recorded_at=self._clock(),
            )
        )
        self._uow.commit()
        return record
