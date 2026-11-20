from typing import Any, Protocol

from app.identity import UserContext
from app.llm.base import ToolSpec
from app.trace import TraceStep


class ToolProvider(Protocol):
    """A family of tools the agent can execute for the LLM (MCP tools, REST tools, ...)."""

    kind: str  # shown in the trace: "mcp" or "rest"

    def specs(self) -> list[ToolSpec]: ...

    async def call(self, name: str, args: dict[str, Any], ctx: UserContext, step: TraceStep) -> dict[str, Any]:
        """Execute a tool and return a JSON-serialisable result for the LLM.

        Expected failures (HTTP 4xx, MCP tool errors) are returned as {"error": {...}}
        so the model can explain them; unexpected failures raise.
        """
        ...
