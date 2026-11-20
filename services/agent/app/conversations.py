"""Server-side conversation memory, bound to the signed-in user.

The browser only holds a conversation id. Keeping the history (including tool
results) on the server means a client cannot forge earlier tool results.
In-memory with a TTL is enough for one agent task; with several tasks this
would move to Redis or DynamoDB.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.llm.base import Message

MAX_CONVERSATIONS = 1000
HISTORY_WINDOW = 30


class ConversationNotFound(Exception):
    pass


@dataclass
class Conversation:
    id: str
    owner: str
    messages: list[Message] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def window(self, max_messages: int = HISTORY_WINDOW) -> list[Message]:
        """Recent history, always starting at a real user question so tool call/result pairs stay intact."""
        if len(self.messages) <= max_messages:
            return list(self.messages)
        start = len(self.messages) - max_messages
        while start < len(self.messages) and not (self.messages[start].role == "user"):
            start += 1
        return self.messages[start:]


class ConversationStore:
    def __init__(self, ttl_seconds: int, clock=lambda: datetime.now(UTC)) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._items: dict[str, Conversation] = {}

    def _expire(self) -> None:
        cutoff = self._clock() - self._ttl
        for cid in [c.id for c in self._items.values() if c.updated_at < cutoff]:
            del self._items[cid]
        while len(self._items) > MAX_CONVERSATIONS:
            oldest = min(self._items.values(), key=lambda c: c.updated_at)
            del self._items[oldest.id]

    def get_or_create(self, conversation_id: str | None, owner: str) -> Conversation:
        self._expire()
        if conversation_id:
            conv = self._items.get(conversation_id)
            if conv is None or conv.owner != owner:
                raise ConversationNotFound("Conversation not found or expired. Start a new one.")
            conv.updated_at = self._clock()
            return conv
        conv = Conversation(id=str(uuid.uuid4()), owner=owner, updated_at=self._clock())
        self._items[conv.id] = conv
        return conv
