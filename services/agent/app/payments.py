"""Payment intents: the mechanism that makes "explicit confirmation" enforceable in code.

The LLM is never given a tool that pays. It can only *prepare* a payment, which
creates a short-lived intent here. The Pay Bill API is called only when the
signed-in user confirms that exact intent through a separate endpoint
(Confirm button / typing YES in the CLI). A question such as "Can I pay $40?"
therefore can never move money.

The intent id doubles as the Pay Bill transactionId (idempotency key), so a
retried confirmation cannot create a second payment.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal


@dataclass
class PaymentIntent:
    id: str
    subscriber_no: str
    conversation_id: str
    year: int
    month: int
    amount: Decimal
    remaining_balance: Decimal
    bill_total: Decimal
    expires_at: datetime
    status: Literal["PENDING", "CONFIRMED", "CANCELLED"] = "PENDING"

    def public_view(self) -> dict:
        return {
            "paymentIntentId": self.id,
            "subscriberNo": self.subscriber_no,
            "period": f"{self.year}-{self.month:02d}",
            "amount": float(self.amount),
            "currentRemainingBalance": float(self.remaining_balance),
            "billTotal": float(self.bill_total),
            "currency": "USD",
            "expiresAt": self.expires_at.isoformat(),
            "status": self.status,
        }


class IntentError(Exception):
    pass


class PaymentIntentStore:
    """In-memory store (one agent task). Production with several tasks: Redis or DynamoDB with TTL."""

    def __init__(self, ttl_seconds: int, clock=lambda: datetime.now(UTC)) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._items: dict[str, PaymentIntent] = {}
        self._lock = asyncio.Lock()

    async def create(self, subscriber_no: str, conversation_id: str, year: int, month: int, amount: Decimal,
                     remaining: Decimal, total: Decimal) -> PaymentIntent:
        async with self._lock:
            # Only one pending payment per conversation: a new request replaces the old one.
            for item in self._items.values():
                if item.conversation_id == conversation_id and item.status == "PENDING":
                    item.status = "CANCELLED"
            intent = PaymentIntent(
                id=str(uuid.uuid4()), subscriber_no=subscriber_no, conversation_id=conversation_id, year=year,
                month=month, amount=amount, remaining_balance=remaining, bill_total=total,
                expires_at=self._clock() + self._ttl,
            )
            self._items[intent.id] = intent
            return intent

    def pending_for(self, conversation_id: str) -> PaymentIntent | None:
        now = self._clock()
        for item in self._items.values():
            if item.conversation_id == conversation_id and item.status == "PENDING" and item.expires_at > now:
                return item
        return None

    async def claim(self, intent_id: str, subscriber_no: str,
                    action: Literal["CONFIRMED", "CANCELLED"]) -> PaymentIntent:
        """Atomically move a PENDING intent of this subscriber to CONFIRMED or CANCELLED."""
        async with self._lock:
            intent = self._items.get(intent_id)
            if intent is None or intent.subscriber_no != subscriber_no:
                raise IntentError("Payment request not found.")  # same message: do not reveal other users' ids
            if intent.status != "PENDING":
                raise IntentError(f"This payment request is already {intent.status.lower()}.")
            if intent.expires_at <= self._clock():
                intent.status = "CANCELLED"
                raise IntentError("This payment request has expired. Please ask again.")
            intent.status = action
            return intent

    async def release(self, intent_id: str) -> None:
        """Put a confirmed intent back to PENDING when the payment call failed for a technical reason.

        Retrying is safe: the same intent id is sent as transactionId, so the API rejects a second charge.
        """
        async with self._lock:
            intent = self._items.get(intent_id)
            if intent and intent.status == "CONFIRMED":
                intent.status = "PENDING"
