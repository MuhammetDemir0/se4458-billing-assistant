"""Tool trace: what the LLM chose, what the agent executed, and what came back.

Returned with every answer (shown in the web UI and CLI) and written to the
log as one JSON line, so each demo scenario has a reproducible record.
"""

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("agent.trace")
MAX_ITEMS = 5
MAX_TEXT = 600


def shorten(value: Any) -> Any:
    """Keep traces readable: cut long lists (e.g. 35,000 usage records) and long texts."""
    if isinstance(value, dict):
        return {k: shorten(v) for k, v in value.items()}
    if isinstance(value, list):
        head = [shorten(v) for v in value[:MAX_ITEMS]]
        return head + ([f"... {len(value) - MAX_ITEMS} more"] if len(value) > MAX_ITEMS else [])
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "..."
    return value


@dataclass
class TraceStep:
    step: int
    kind: str  # llm | mcp | rest | agent | confirmation
    name: str
    input: Any = None
    output: Any = None
    status: str = "ok"
    latency_ms: float = 0.0
    http: list[dict] = field(default_factory=list)


class Trace:
    def __init__(self, request_id: str = "") -> None:
        self.request_id = request_id
        self.steps: list[TraceStep] = []

    @contextmanager
    def step(self, kind: str, name: str, input: Any = None):
        entry = TraceStep(step=len(self.steps) + 1, kind=kind, name=name, input=shorten(input))
        started = time.perf_counter()
        try:
            yield entry
        except Exception as exc:
            entry.status = "error"
            entry.output = {"error": str(exc)}
            raise
        finally:
            entry.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            entry.output = shorten(entry.output)
            self.steps.append(entry)

    def to_list(self) -> list[dict]:
        return [asdict(s) for s in self.steps]

    def log(self, event: str, **extra: Any) -> None:
        logger.info(json.dumps({"event": event, "requestId": self.request_id, "trace": self.to_list(), **extra},
                               default=str))
