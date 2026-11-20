"""Bearer-token ASGI middleware protecting the MCP endpoint.

Every HTTP request must carry ``Authorization: Bearer <MCP_TOKEN>`` except the
paths listed in ``public_paths`` (``/health`` by default, used by the ECS /
load-balancer health check). Lifespan and other non-HTTP scopes pass through.

If no token is configured the middleware lets everything through; the server
logs a warning in that case. This is meant for local development only.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str | None, public_paths: Iterable[str] = ("/health",)):
        self.app = app
        self.token = token or None
        self.public_paths = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.token is None or scope.get("path") in self.public_paths:
            await self.app(scope, receive, send)
            return

        if self._is_authorized(scope):
            await self.app(scope, receive, send)
            return

        body = json.dumps({"error": "unauthorized", "detail": "Missing or invalid bearer token"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Bearer realm="mcp"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _is_authorized(self, scope: Scope) -> bool:
        header = b""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                header = value
                break
        scheme, _, credentials = header.decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not credentials:
            return False
        assert self.token is not None
        return hmac.compare_digest(credentials.strip().encode(), self.token.encode())
