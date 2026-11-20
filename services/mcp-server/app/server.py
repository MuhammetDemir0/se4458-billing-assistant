"""MCP server exposing ``search_billing_documentation`` over the billing guide.

Transports:
  * Streamable HTTP (default): stateless, ``http://<host>:8000/mcp``, protected
    by ``Authorization: Bearer <MCP_TOKEN>``; ``GET /health`` is public.
  * stdio: ``python -m app.server --stdio`` (for local MCP clients/inspectors).

The BM25 index is loaded once at startup (built at Docker build time by
``python -m app.indexer``); queries only score against the loaded index.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.auth import BearerTokenMiddleware
from app.indexer import DEFAULT_DOCUMENT_PATH, DEFAULT_INDEX_PATH, BillingIndex

logger = logging.getLogger("mcp_server")

TOOL_NAME = "search_billing_documentation"
MAX_TOP_K = 10
MAX_QUERY_LENGTH = 500

SERVER_INSTRUCTIONS = (
    "Searches the Mobile Provider Billing Guide (MOB-BILLING-2026-01) for billing policy and pricing rules. "
    "Results cite section numbers; quote them when explaining policies. The guide has no live subscriber data: "
    "use the Billing REST API for usage, bill totals and payments."
)


class Match(BaseModel):
    section: str = Field(description='Section number in the guide, e.g. "3.2"')
    heading: str = Field(description="Section heading")
    parent_heading: str | None = Field(description="Heading of the enclosing top-level section, if any")
    text: str = Field(description="Full text of the section")
    score: float = Field(description="BM25 relevance score (higher is more relevant)")


class SearchResult(BaseModel):
    document: str = Field(description="Source document file name")
    document_id: str = Field(description="Source document identifier")
    query: str = Field(description="The query that was searched")
    matches: list[Match] = Field(description="Best matching sections, most relevant first")


def create_server(index: BillingIndex, *, host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    """Create the FastMCP server bound to an already loaded index."""
    mcp = FastMCP(
        name="billing-docs",
        instructions=SERVER_INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        # Access control is the bearer token; the server is reached by private
        # DNS names inside the VPC, so Host-header allow-listing is disabled.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool(
        name=TOOL_NAME,
        description=(
            "Search the Mobile Provider Billing Guide for policy and pricing passages. "
            "Returns the most relevant sections with section number, heading, parent heading, text and score."
        ),
    )
    def search_billing_documentation(
        query: Annotated[str, Field(description="Natural-language question or keywords")],
        top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K, description="Number of sections to return (1-10)")] = 3,
    ) -> SearchResult:
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query must be at most {MAX_QUERY_LENGTH} characters")
        if not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}")

        matches = index.search(query, top_k)
        logger.info("search query=%r top_k=%d -> %s", query, top_k, [m["section"] for m in matches])
        return SearchResult(
            document=index.document,
            document_id=index.document_id,
            query=query,
            matches=[Match(**m) for m in matches],
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "document_id": index.document_id, "sections": len(index.chunks), "tool": TOOL_NAME}
        )

    return mcp


def create_http_app(mcp: FastMCP, token: str | None) -> ASGIApp:
    """Wrap the FastMCP Streamable HTTP app with bearer-token auth."""
    if not token:
        logger.warning("MCP_TOKEN is not set: the MCP endpoint is UNAUTHENTICATED (development only)")
    return BearerTokenMiddleware(mcp.streamable_http_app(), token=token)


def load_index() -> BillingIndex:
    index_path = Path(os.getenv("MCP_INDEX_PATH", DEFAULT_INDEX_PATH))
    document_path = Path(os.getenv("MCP_DOCUMENT_PATH", DEFAULT_DOCUMENT_PATH))
    return BillingIndex.load_or_build(index_path, document_path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Billing documentation MCP server")
    parser.add_argument("--stdio", action="store_true", help="Serve over stdio instead of Streamable HTTP")
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8000")))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    index = load_index()
    mcp = create_server(index, host=args.host, port=args.port)

    if args.stdio:
        mcp.run(transport="stdio")
        return

    import uvicorn

    app = create_http_app(mcp, os.getenv("MCP_TOKEN"))
    uvicorn.run(app, host=args.host, port=args.port, log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
