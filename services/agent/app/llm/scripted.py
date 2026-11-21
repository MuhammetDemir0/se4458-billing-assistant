"""Deterministic LLM fake for automated tests (no network, no API key).

Each step is either an LLMResponse or a function(messages) -> LLMResponse, so a
test can decide the next "model decision" based on the tool results it sees.
"""

from collections.abc import Callable

from app.llm.base import LLMError, LLMResponse, Message, ToolSpec

Step = LLMResponse | Callable[[list[Message]], LLMResponse] | Exception


class ScriptedLLMClient:
    model = "scripted-test-llm"

    def __init__(self, steps: list[Step]) -> None:
        self._steps = list(steps)
        self.calls: list[dict] = []

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       timeout: float | None = None) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": [t.name for t in tools],
                           "timeout": timeout})
        if not self._steps:
            raise LLMError("Script exhausted")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step
