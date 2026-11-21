"""Provider-neutral LLM contract.

The orchestrator only knows these types. Gemini is one implementation
(app/llm/gemini.py); tests use a scripted fake (app/llm/scripted.py), so the
agent logic is testable without a network or an API key.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema of the arguments


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # The provider's own representation of an assistant turn (e.g. Gemini Content with
    # thought signatures). It is sent back unchanged so multi-step tool use keeps working.
    provider_raw: Any = None

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", text=text)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: dict[str, int] = field(default_factory=dict)
    provider_raw: Any = None


class LLMError(Exception):
    """The model could not be reached or refused the request (rate limit, invalid key, ...)."""


class LLMClient(Protocol):
    model: str

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       timeout: float | None = None) -> LLMResponse:
        """`timeout` is the time left in the current chat turn; the call must not take longer."""
        ...
