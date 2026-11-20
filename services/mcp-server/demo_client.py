"""Retrieval demo using the real MCP Streamable HTTP client.

Connects to a running server, lists its tools and runs three distinct
retrieval queries through ``search_billing_documentation``, printing JSON.

    python demo_client.py                                  # http://localhost:8000/mcp
    python demo_client.py --url http://host:8000/mcp --token <MCP_TOKEN>

The token defaults to the ``MCP_TOKEN`` environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEMO_QUERIES = [
    "How much does extra internet cost?",
    "Can I pay only part of my bill?",
    "When is my bill due and what happens if I pay late?",
]


async def run(url: str, token: str | None, top_k: int) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0, read=300.0)) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print(f"# Connected to {init.serverInfo.name} (protocol {init.protocolVersion}) at {url}")

                tools = await session.list_tools()
                print("# Tools:")
                print(
                    json.dumps(
                        [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools.tools],
                        indent=2,
                    )
                )

                failures = 0
                for query in DEMO_QUERIES:
                    result = await session.call_tool(
                        "search_billing_documentation", {"query": query, "top_k": top_k}
                    )
                    print(f"\n# Query: {query}")
                    if result.isError:
                        failures += 1
                        print(json.dumps({"error": [c.model_dump() for c in result.content]}, indent=2))
                        continue
                    payload = result.structuredContent
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                    sections = [m["section"] for m in payload["matches"]]
                    print(f"# -> sections {sections}")
                return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("MCP_URL", "http://localhost:8000/mcp"))
    parser.add_argument("--token", default=os.getenv("MCP_TOKEN"))
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args.url, args.token, args.top_k))
    except* httpx.HTTPStatusError as group:
        status = group.exceptions[0].response.status_code
        hint = " (check --token / MCP_TOKEN)" if status == 401 else ""
        print(f"error: server returned HTTP {status}{hint}", file=sys.stderr)
        code = 2
    except* httpx.TransportError as group:
        print(f"error: cannot reach {args.url}: {group.exceptions[0]!r}", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
