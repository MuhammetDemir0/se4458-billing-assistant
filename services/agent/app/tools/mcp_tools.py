"""MCP client side: tools are discovered from the MCP server, not hard-coded.

At startup the agent calls `list_tools` and turns each MCP tool (name,
description, JSON input schema) into a function declaration for the LLM.
When the LLM selects one, the agent calls it with `call_tool` over Streamable
HTTP. The connection goes straight to the MCP server, not through the API
Gateway (required separation of the two paths).
"""

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.identity import UserContext
from app.llm.base import ToolSpec
from app.trace import TraceStep

logger = logging.getLogger("agent.mcp")
SessionFactory = Callable[[], AbstractAsyncContextManager[ClientSession]]


def http_session_factory(url: str, token: str | None, timeout: float = 30.0) -> SessionFactory:
    @asynccontextmanager
    async def open_session() -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout)) as http_client:
            async with streamable_http_client(url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    return open_session


class McpToolProvider:
    kind = "mcp"

    def __init__(self, session_factory: SessionFactory) -> None:
        self._open = session_factory
        self._specs: list[ToolSpec] = []

    @property
    def ready(self) -> bool:
        return bool(self._specs)

    async def refresh(self) -> list[ToolSpec]:
        async with self._open() as session:
            listed = await session.list_tools()
        self._specs = [ToolSpec(name=t.name, description=t.description or "", parameters=t.inputSchema)
                       for t in listed.tools]
        logger.info("loaded MCP tools: %s", [s.name for s in self._specs])
        return self._specs

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, args: dict[str, Any], ctx: UserContext, step: TraceStep) -> dict[str, Any]:
        async with self._open() as session:
            result = await session.call_tool(name, args)
        if result.isError:
            message = " ".join(getattr(c, "text", "") for c in result.content).strip() or "MCP tool error"
            step.status = "error"
            return {"error": {"source": "mcp", "detail": message}}
        if result.structuredContent is not None:
            content = dict(result.structuredContent)
            # FastMCP wraps non-model return values as {"result": ...}; unwrap for the LLM.
            if set(content) == {"result"} and isinstance(content["result"], dict):
                content = content["result"]
            return content
        text = " ".join(getattr(c, "text", "") for c in result.content).strip()
        try:
            parsed = json.loads(text)  # tools without an output schema return JSON as text
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except ValueError:
            return {"text": text}
