"""Agent service HTTP API (served behind the API Gateway under /agent).

  GET  /agent/chat                            web chat (static page)
  POST /agent/v1/auth/login                   username/password -> access token (Cognito / local)
  POST /agent/v1/chat                         ask a question (bearer token)
  POST /agent/v1/payments/{id}/confirm        execute a prepared payment (bearer token)
  POST /agent/v1/payments/{id}/cancel         cancel a prepared payment (bearer token)
  GET  /agent/v1/health, /health              health checks
"""

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.conversations import ConversationNotFound, ConversationStore
from app.identity import AuthError, IdentityProvider, TokenVerifier, UserContext, build_identity
from app.llm.base import LLMClient, LLMError
from app.orchestrator import BillingAssistant, ChatResult
from app.payments import IntentError, PaymentIntentStore
from app.tools.billing_tools import BillingGatewayClient, BillingToolProvider
from app.tools.mcp_tools import McpToolProvider, http_session_factory

logger = logging.getLogger("agent")
STATIC = Path(__file__).parent / "static"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    conversationId: str | None = Field(default=None, max_length=64)


class PaymentActionRequest(BaseModel):
    conversationId: str = Field(min_length=1, max_length=64)


class UnconfiguredLLM:
    """Placeholder so the service (and its health check) still starts without an API key."""

    def __init__(self, model: str) -> None:
        self.model = model

    async def generate(self, *args, **kwargs):
        raise LLMError("GEMINI_API_KEY is not configured on the agent service")


def problem(status: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse({"title": title, "status": status, "detail": detail}, status_code=status,
                        media_type="application/problem+json")


def chat_response(result: ChatResult, model: str) -> dict:
    body = asdict(result)
    return {
        "conversationId": body["conversation_id"],
        "answer": body["answer"],
        "pendingPayment": body["pending_payment"],
        "payment": body["payment"],
        "model": model,
        "usage": body["usage"],
        "trace": body["trace"],
    }


async def _load_mcp_tools(provider: McpToolProvider, attempts: int = 10) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await provider.refresh()
            return
        except Exception as exc:  # noqa: BLE001 - keep retrying; the server may still be starting
            logger.warning("MCP tool discovery failed (attempt %d): %s", attempt, exc)
            await asyncio.sleep(min(2 * attempt, 10))


def create_app(settings: Settings | None = None, *, llm: LLMClient | None = None,
               mcp_provider: McpToolProvider | None = None, gateway: BillingGatewayClient | None = None,
               identity: tuple[IdentityProvider, TokenVerifier] | None = None,
               discover_mcp_on_startup: bool = True) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if llm is None:
        if settings.gemini_api_key:
            from app.llm.gemini import GeminiLLMClient
            llm = GeminiLLMClient(settings.gemini_api_key, settings.gemini_model, settings.llm_max_output_tokens,
                                  fallback_model=settings.gemini_fallback_model or None,
                                  timeout_seconds=settings.llm_timeout_seconds)
        else:
            logger.error("GEMINI_API_KEY is not set: chat requests will fail until it is configured")
            llm = UnconfiguredLLM(settings.gemini_model)
    mcp_provider = mcp_provider or McpToolProvider(http_session_factory(settings.mcp_url, settings.mcp_token))
    gateway = gateway or BillingGatewayClient(settings.gateway_url, settings.http_timeout_seconds)
    identity_provider, verifier = identity or build_identity(settings)
    intents = PaymentIntentStore(settings.payment_intent_ttl_seconds)
    conversations = ConversationStore(settings.conversation_ttl_seconds)
    assistant = BillingAssistant(llm, [mcp_provider, BillingToolProvider(gateway, intents)], gateway, intents,
                                 max_rounds=settings.max_tool_rounds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(_load_mcp_tools(mcp_provider)) if discover_mcp_on_startup else None
        yield
        if task:
            task.cancel()
        await gateway.aclose()

    app = FastAPI(title="Billing Assistant Agent", version="1.0.0", lifespan=lifespan,
                  docs_url="/agent/v1/docs", openapi_url="/agent/v1/openapi.json", redoc_url=None)

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError):
        return problem(exc.status, "Authentication failed", exc.detail)

    @app.exception_handler(ConversationNotFound)
    async def _conv_error(_: Request, exc: ConversationNotFound):
        return problem(404, "Conversation not found", str(exc))

    @app.exception_handler(IntentError)
    async def _intent_error(_: Request, exc: IntentError):
        return problem(409, "Payment request not available", str(exc))

    bearer = HTTPBearer(auto_error=False)

    def current_user(request: Request,
                     credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> UserContext:
        if credentials is None:
            raise AuthError("Missing bearer token.")
        ctx = verifier.verify(credentials.credentials)
        if settings.subscriber_group not in ctx.groups:
            raise AuthError("The assistant is available to subscribers only.", status=403)
        incoming = request.headers.get("x-request-id", "")
        return replace(ctx, request_id=incoming if _SAFE_ID.match(incoming) else str(uuid.uuid4()))

    User = Annotated[UserContext, Depends(current_user)]

    async def ensure_tools() -> None:
        if not mcp_provider.ready:
            await _load_mcp_tools(mcp_provider, attempts=1)

    @app.get("/health", include_in_schema=False)
    async def liveness():
        return {"status": "ok"}

    @app.get("/agent/v1/health")
    async def health():
        return {"status": "ok", "model": llm.model, "fallbackModel": getattr(llm, "fallback", None),
                "mcpToolsLoaded": [s.name for s in mcp_provider.specs()],
                "gateway": settings.gateway_url}

    @app.get("/", include_in_schema=False)
    @app.get("/agent", include_in_schema=False)
    @app.get("/agent/", include_in_schema=False)
    async def to_ui():
        return RedirectResponse("/agent/chat")

    @app.get("/agent/chat", include_in_schema=False)
    async def ui():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.post("/agent/v1/auth/login")
    async def login(body: LoginRequest):
        tokens = await identity_provider.login(body.username, body.password)
        return {"accessToken": tokens.access_token, "expiresIn": tokens.expires_in, "username": body.username}

    @app.post("/agent/v1/chat")
    async def chat(body: ChatRequest, user: User):
        await ensure_tools()
        conversation = conversations.get_or_create(body.conversationId, user.username)
        result = await assistant.chat(user, conversation, body.message.strip())
        return chat_response(result, llm.model)

    @app.post("/agent/v1/payments/{intent_id}/confirm")
    async def confirm(intent_id: str, body: PaymentActionRequest, user: User):
        conversation = conversations.get_or_create(body.conversationId, user.username)
        result = await assistant.confirm_payment(user, conversation, intent_id)
        return chat_response(result, llm.model)

    @app.post("/agent/v1/payments/{intent_id}/cancel")
    async def cancel(intent_id: str, body: PaymentActionRequest, user: User):
        conversation = conversations.get_or_create(body.conversationId, user.username)
        result = await assistant.cancel_payment(user, conversation, intent_id)
        return chat_response(result, llm.model)

    return app


def _default_app() -> FastAPI:
    return create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:_default_app", factory=True, host="0.0.0.0", port=8000)
