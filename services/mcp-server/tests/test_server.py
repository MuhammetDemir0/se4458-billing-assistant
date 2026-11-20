"""End-to-end tests through the MCP SDK (in-memory session) and the HTTP app."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from app.server import TOOL_NAME, create_http_app, create_server

TOKEN = "test-token-123"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}


@pytest.fixture
def mcp_server(billing_index):
    return create_server(billing_index)


async def test_list_tools_exposes_schema(mcp_server):
    async with create_connected_server_and_client_session(mcp_server) as session:
        tools = (await session.list_tools()).tools
    assert [t.name for t in tools] == [TOOL_NAME]
    schema = tools[0].inputSchema
    assert schema["required"] == ["query"]
    assert schema["properties"]["top_k"]["default"] == 3
    assert schema["properties"]["top_k"]["minimum"] == 1
    assert schema["properties"]["top_k"]["maximum"] == 10
    assert "matches" in tools[0].outputSchema["properties"]


async def test_call_tool_end_to_end(mcp_server):
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(TOOL_NAME, {"query": "How much does extra internet cost?", "top_k": 2})
    assert not result.isError
    payload = result.structuredContent
    assert payload["document"] == "Mobile_Provider_Billing_Guide.md"
    assert payload["document_id"] == "MOB-BILLING-2026-01"
    assert payload["query"] == "How much does extra internet cost?"
    assert len(payload["matches"]) == 2
    assert payload["matches"][0]["section"] in {"3.2", "8.1"}
    # Unstructured content carries the same JSON for clients that ignore structuredContent.
    assert json.loads(result.content[0].text) == payload


@pytest.mark.parametrize(
    "arguments",
    [{"query": ""}, {"query": "   "}, {"query": "internet", "top_k": 0}, {"query": "internet", "top_k": 11}],
)
async def test_call_tool_validation_errors(mcp_server, arguments):
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(TOOL_NAME, arguments)
    assert result.isError


def _client(billing_index, token):
    return TestClient(create_http_app(create_server(billing_index), token))


def test_health_is_public(billing_index):
    with _client(billing_index, TOKEN) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["document_id"] == "MOB-BILLING-2026-01"


@pytest.mark.parametrize("auth", [None, "Bearer wrong-token", "Basic dGVzdA==", "Bearer ", TOKEN])
def test_mcp_rejects_missing_or_wrong_token(billing_index, auth):
    headers = dict(MCP_HEADERS)
    if auth is not None:
        headers["Authorization"] = auth
    with _client(billing_index, TOKEN) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_mcp_accepts_valid_token(billing_index):
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    with _client(billing_index, TOKEN) as client:
        init = client.post("/mcp", json=INITIALIZE, headers=headers)
        call = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": TOOL_NAME, "arguments": {"query": "Can I pay only part of my bill?"}},
            },
            headers=headers,
        )
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "billing-docs"
    assert call.status_code == 200
    matches = call.json()["result"]["structuredContent"]["matches"]
    assert matches[0]["section"] in {"5.1", "8.3"}


def test_no_token_configured_allows_requests(billing_index, caplog):
    with caplog.at_level("WARNING"):
        app = create_http_app(create_server(billing_index), None)
    assert "UNAUTHENTICATED" in caplog.text
    with TestClient(app) as client:
        response = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200
