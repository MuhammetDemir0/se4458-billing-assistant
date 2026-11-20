"""Application errors and their RFC 7807 (application/problem+json) representation.

Services raise these exceptions; they never build HTTP responses themselves.
The API layer translates them into problem details in one place.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    title = "Internal server error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code


class BadRequestError(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"
    title = "Invalid request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    title = "Authentication required"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    title = "Access denied"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    title = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    title = "Conflict"


class BusinessRuleError(AppError):
    status_code = 422
    code = "BUSINESS_RULE_VIOLATION"
    title = "Business rule violation"


def problem(request: Request, status: int, title: str, detail: str, code: str, errors=None) -> JSONResponse:
    body = {
        "type": f"https://errors.billing.example/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "instance": request.url.path,
    }
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["requestId"] = request_id
    if errors:
        body["errors"] = errors
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return problem(request, exc.status_code, exc.title, exc.detail, exc.code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in err.get("loc", []) if p != "body"), "message": err.get("msg")}
            for err in exc.errors()
        ]
        return problem(request, 400, "Invalid request", "One or more fields are invalid.", "VALIDATION_ERROR", errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return problem(request, exc.status_code, str(exc.detail), str(exc.detail), code)
