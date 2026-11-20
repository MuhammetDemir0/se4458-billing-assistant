"""Structured (JSON) request logging with a correlation id.

The X-Request-ID header is accepted from the caller (the agent and the API
Gateway forward it) or generated, returned in the response, and written to
every log line so a single user question can be traced across services.
"""

import json
import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
logger = logging.getLogger("billing-api")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        payload.update(getattr(record, "extra_fields", {}))
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_ID.match(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        logger.info(
            "request",
            extra={
                "extra_fields": {
                    "requestId": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "durationMs": round((time.perf_counter() - started) * 1000, 1),
                }
            },
        )
        return response
