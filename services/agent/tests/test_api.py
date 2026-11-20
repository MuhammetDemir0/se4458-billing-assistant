"""Agent HTTP endpoints: authentication, chat and payment actions."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.identity import CognitoIdentityProvider
from app.llm.scripted import ScriptedLLMClient
from app.main import create_app
from tests.conftest import OWNER, answer, call, tools


@pytest.fixture
def app_factory(settings, mcp_provider, gateway, identity):
    def build(steps):
        llm = ScriptedLLMClient(steps)
        app = create_app(settings, llm=llm, mcp_provider=mcp_provider, gateway=gateway, identity=identity,
                         discover_mcp_on_startup=False)
        return TestClient(app), llm
    return build


def login(client, username=OWNER, password="pw-owner"):
    r = client.post("/agent/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def test_login_and_chat(app_factory):
    client, _ = app_factory([tools(call("search_billing_documentation", {"query": "extra internet"})),
                             answer("$10 per extra 10 GB (§3.2).")])
    with client:
        r = client.post("/agent/v1/chat", json={"message": "How much does extra internet cost?"},
                        headers=login(client))
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("$10") and body["conversationId"]
    assert [s["kind"] for s in body["trace"]] == ["llm", "mcp", "llm"]


def test_wrong_password(app_factory):
    client, _ = app_factory([])
    r = client.post("/agent/v1/auth/login", json={"username": OWNER, "password": "nope"})
    assert r.status_code == 401


def test_chat_requires_token(app_factory):
    client, _ = app_factory([])
    assert client.post("/agent/v1/chat", json={"message": "hi"}).status_code == 401


def test_chat_rejects_tampered_token(app_factory):
    client, _ = app_factory([])
    headers = login(client)
    headers["Authorization"] += "x"
    assert client.post("/agent/v1/chat", json={"message": "hi"}, headers=headers).status_code == 401


def test_admin_is_not_a_subscriber(app_factory):
    client, _ = app_factory([])
    headers = login(client, "ops-admin", "pw-admin")
    assert client.post("/agent/v1/chat", json={"message": "hi"}, headers=headers).status_code == 403


def test_conversation_is_private_to_its_owner(app_factory):
    client, _ = app_factory([answer("Hi"), answer("Hi again")])
    with client:
        conv = client.post("/agent/v1/chat", json={"message": "hi"}, headers=login(client)).json()["conversationId"]
        other = login(client, "5551000002", "pw-other")
        r = client.post("/agent/v1/chat", json={"message": "hi", "conversationId": conv}, headers=other)
    assert r.status_code == 404


def test_payment_confirm_endpoint(app_factory, billing_api):
    client, _ = app_factory([tools(call("prepare_bill_payment", {"month": 10, "year": 2026})),
                             answer("Please confirm.")])
    with client:
        headers = login(client)
        chat = client.post("/agent/v1/chat", json={"message": "Pay my bill"}, headers=headers).json()
        pending = chat["pendingPayment"]
        assert pending and not [r for r in billing_api.requests if r.method == "POST"]
        r = client.post(f"/agent/v1/payments/{pending['paymentIntentId']}/confirm",
                        json={"conversationId": chat["conversationId"]}, headers=headers)
        assert r.status_code == 200 and r.json()["payment"]["billStatus"] == "PAID"
        again = client.post(f"/agent/v1/payments/{pending['paymentIntentId']}/confirm",
                            json={"conversationId": chat["conversationId"]}, headers=headers)
    assert again.status_code == 409


def test_payment_cancel_endpoint(app_factory, billing_api):
    client, _ = app_factory([tools(call("prepare_bill_payment", {"month": 10, "year": 2026})), answer("Confirm?")])
    with client:
        headers = login(client)
        chat = client.post("/agent/v1/chat", json={"message": "Pay my bill"}, headers=headers).json()
        r = client.post(f"/agent/v1/payments/{chat['pendingPayment']['paymentIntentId']}/cancel",
                        json={"conversationId": chat["conversationId"]}, headers=headers)
    assert r.status_code == 200 and "No payment was made" in r.json()["answer"]
    assert not [req for req in billing_api.requests if req.method == "POST"]


def test_message_validation(app_factory):
    client, _ = app_factory([])
    r = client.post("/agent/v1/chat", json={"message": ""}, headers=login(client))
    assert r.status_code == 422


def test_web_ui_is_served(app_factory):
    client, _ = app_factory([])
    r = client.get("/agent/chat")
    assert r.status_code == 200 and "Billing Assistant" in r.text
    assert client.get("/agent", follow_redirects=False).headers["location"] == "/agent/chat"


@pytest.mark.asyncio
async def test_cognito_login_uses_initiate_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["target"] = request.headers["x-amz-target"]
        seen["body"] = request.content
        return httpx.Response(200, json={"AuthenticationResult": {"AccessToken": "abc", "ExpiresIn": 3600}})

    provider = CognitoIdentityProvider("eu-central-1", "client-123", transport=httpx.MockTransport(handler))
    tokens = await provider.login(OWNER, "secret")
    assert tokens.access_token == "abc"
    assert seen["target"] == "AWSCognitoIdentityProviderService.InitiateAuth"
    assert b"USER_PASSWORD_AUTH" in seen["body"]


def test_local_auth_is_refused_outside_local_environments():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError, match="AUTH_MODE=local"):
        Settings(environment="aws", auth_mode="local")
    assert Settings(environment="aws", auth_mode="cognito").auth_mode == "cognito"
