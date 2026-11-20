"""Three distinct MCP retrieval tests, run with the agent's own MCP client.

The MCP server is private (not behind the gateway), so the demo runs where the agent runs:
  local : docker compose exec agent python -m app.mcp_demo
  AWS   : aws ecs execute-command --cluster billing-assistant-cluster --task <agent-task> \
              --container agent --interactive --command "python -m app.mcp_demo"
"""

import asyncio
import json
import os
import sys

from app.identity import UserContext
from app.tools.mcp_tools import McpToolProvider, http_session_factory
from app.trace import TraceStep

QUERIES = [
    "How much does extra internet cost?",
    "Can I pay only part of my bill?",
    "When is my bill due and what happens if I pay late?",
]


async def main() -> int:
    url, token = os.environ["MCP_URL"], os.environ.get("MCP_TOKEN")
    provider = McpToolProvider(http_session_factory(url, token))
    specs = await provider.refresh()
    print(f"# MCP server: {url}")
    print("# Tools discovered with list_tools:")
    print(json.dumps([{"name": s.name, "description": s.description, "inputSchema": s.parameters} for s in specs],
                     indent=2))
    nobody = UserContext(username="demo", groups=frozenset(), access_token="")
    for i, query in enumerate(QUERIES, 1):
        result = await provider.call("search_billing_documentation", {"query": query, "top_k": 3}, nobody,
                                     TraceStep(i, "mcp", "search_billing_documentation"))
        print(f"\n# Retrieval test {i}: {query}")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
