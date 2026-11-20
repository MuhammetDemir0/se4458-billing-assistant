"""Test doubles for the agent: no LLM, no network, no cloud.

* LLM          -> ScriptedLLMClient (decides tool calls deterministically)
* MCP server   -> a small in-process FastMCP server, reached through the real MCP client code
* API Gateway  -> httpx.MockTransport emulating the Billing API contract and recording every request
"""

import json
from contextlib import asynccontextmanager
from datetime import date

import httpx
import jwt
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import Settings
from app.conversations import ConversationStore
from app.identity import LocalIdentityProvider, LocalTokenVerifier, UserContext
from app.llm.base import LLMResponse, ToolCall
from app.orchestrator import BillingAssistant
from app.payments import PaymentIntentStore
from app.tools.billing_tools import BillingGatewayClient, BillingToolProvider
from app.tools.mcp_tools import McpToolProvider

SECRET = "agent-test-secret-0123456789-abcdef"
ISSUER = "agent-test-issuer"
OWNER = "5551000001"


def build_fake_mcp_server() -> FastMCP:
    server = FastMCP("fake-billing-docs")

    @server.tool(name="search_billing_documentation", description="Search the billing guide.")
    def search_billing_documentation(query: str, top_k: int = 3) -> dict:
        return {"document": "Mobile_Provider_Billing_Guide.md", "document_id": "MOB-BILLING-2026-01",
                "query": query,
                "matches": [{"section": "3.2", "heading": "Additional internet blocks",
                             "text": "Each additional 10 GB block costs $10.", "score": 4.2}]}

    return server


@pytest.fixture
def mcp_provider():
    server = build_fake_mcp_server()

    @asynccontextmanager
    async def open_session():
        async with create_connected_server_and_client_session(server) as session:
            yield session

    return McpToolProvider(open_session)


class FakeBillingApi:
    """Emulates the gateway + Billing API for subscriber 5551000001 and records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bills = {
            (OWNER, 2026, 9): {"billTotal": 50.0, "paidAmount": 50.0, "status": "PAID"},
            (OWNER, 2026, 10): {"billTotal": 80.0, "paidAmount": 0.0, "status": "UNPAID"},
        }
        self.transactions: set[str] = set()
        self.fail_payments_with: int | None = None

    def summary(self, no, y, m):
        b = self.bills[(no, y, m)]
        return {"subscriberNo": no, "year": y, "month": m, "currency": "USD", "billTotal": b["billTotal"],
                "paidAmount": b["paidAmount"], "remainingBalance": round(b["billTotal"] - b["paidAmount"], 2),
                "status": b["status"], "calculatedAt": "2026-11-01T00:00:00Z"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parts = request.url.path.strip("/").split("/")  # api v1 subscribers {no} bills {y} {m} [details|payments]
        no, y, m = parts[3], int(parts[5]), int(parts[6])
        raw_token = request.headers.get("authorization", "").removeprefix("Bearer ")
        token_user = (raw_token.removeprefix("token-for:") if raw_token.startswith("token-for:")
                      else jwt.decode(raw_token, options={"verify_signature": False}).get("username"))
        if (no, y, m) not in self.bills:
            return httpx.Response(404, json={"code": "BILL_NOT_FOUND", "detail": "No calculated bill."})
        if len(parts) == 7:
            return httpx.Response(200, json=self.summary(no, y, m))
        if token_user != no:
            return httpx.Response(403, json={"code": "SUBSCRIBER_FORBIDDEN", "detail": "Not your account."})
        if parts[7] == "details":
            return httpx.Response(200, json={**self.summary(no, y, m),
                                             "charges": {"phone": {"minutesUsed": 1350, "amount": 10.0},
                                                         "internet": {"mbUsed": 35000, "gbUsed": 35.0,
                                                                      "amount": 70.0}, "total": 80.0},
                                             "usageRecords": {"items": [], "page": 1, "pageSize": 10,
                                                              "totalItems": 35135, "totalPages": 3514}})
        if parts[7] == "payments":
            if self.fail_payments_with:
                return httpx.Response(self.fail_payments_with, json={"detail": "upstream failure"})
            body = json.loads(request.content)
            if body["transactionId"] in self.transactions:
                return httpx.Response(409, json={"code": "DUPLICATE_TRANSACTION", "detail": "duplicate"})
            self.transactions.add(body["transactionId"])
            bill = self.bills[(no, y, m)]
            bill["paidAmount"] = round(bill["paidAmount"] + body["amount"], 2)
            bill["status"] = "PAID" if bill["paidAmount"] >= bill["billTotal"] else "PARTIALLY_PAID"
            s = self.summary(no, y, m)
            return httpx.Response(201, json={"paymentStatus": "SUCCESS", "transactionId": body["transactionId"],
                                             "paidAmount": body["amount"], "totalPaid": s["paidAmount"],
                                             "remainingBalance": s["remainingBalance"],
                                             "billStatus": s["status"]})
        return httpx.Response(404, json={"detail": "unknown route"})


@pytest.fixture
def billing_api() -> FakeBillingApi:
    return FakeBillingApi()


@pytest.fixture
def gateway(billing_api) -> BillingGatewayClient:
    return BillingGatewayClient("http://gateway.test", 5, transport=httpx.MockTransport(billing_api.handler))


@pytest.fixture
def intents() -> PaymentIntentStore:
    return PaymentIntentStore(ttl_seconds=300)


@pytest.fixture
def ctx() -> UserContext:
    # The fake API reads the subscriber from the token text "token-for:<user>".
    return UserContext(username=OWNER, groups=frozenset({"subscriber"}), access_token=f"token-for:{OWNER}",
                       request_id="req-test-1")


@pytest.fixture
def conversations() -> ConversationStore:
    return ConversationStore(ttl_seconds=600)


@pytest.fixture
def make_assistant(mcp_provider, gateway, intents):
    async def build(llm, max_rounds=5):
        await mcp_provider.refresh()
        return BillingAssistant(llm, [mcp_provider, BillingToolProvider(gateway, intents)], gateway, intents,
                                max_rounds=max_rounds, today=lambda: date(2026, 11, 20))
    return build


@pytest.fixture
def settings() -> Settings:
    return Settings(auth_mode="local", jwt_secret=SECRET, jwt_issuer=ISSUER, gemini_api_key="unused")


@pytest.fixture
def identity():
    users = {OWNER: {"password": "pw-owner", "groups": ["subscriber"]},
             "5551000002": {"password": "pw-other", "groups": ["subscriber"]},
             "ops-admin": {"password": "pw-admin", "groups": ["billing-admin"]}}
    return LocalIdentityProvider(users, SECRET, ISSUER), LocalTokenVerifier(SECRET, ISSUER)


def call(name, args, call_id=None):
    return ToolCall(id=call_id or f"call-{name}", name=name, args=args)


def tools(*calls, text=""):
    return LLMResponse(text=text, tool_calls=list(calls))


def answer(text):
    return LLMResponse(text=text, tool_calls=[])
