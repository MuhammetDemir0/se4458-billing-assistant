"""Application factory for the Billing REST API."""

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.v1.routers import bills, payments, usage
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.security import build_token_verifier
from app.db.session import build_engine, build_session_factory, ping

API_V1 = "/api/v1"

DESCRIPTION = """
Billing REST API of the Mobile Provider Billing Assistant (SE 4458 Midterm, Group 2).

* All public routes are served through the API Gateway under `/api/v1`.
* Protected operations need a bearer **access token** (Amazon Cognito in the cloud,
  a development token locally). Use the **Authorize** button to paste one.
* Errors use RFC 7807 `application/problem+json` with a machine-readable `code`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Mobile Provider Billing API",
        version="1.0.0",
        description=DESCRIPTION,
        docs_url=f"{API_V1}/docs",
        redoc_url=None,
        openapi_url=f"{API_V1}/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.session_factory = build_session_factory(app.state.engine)
    app.state.token_verifier = build_token_verifier(settings)

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    v1 = APIRouter(prefix=API_V1)
    v1.include_router(usage.router)
    v1.include_router(bills.router)
    v1.include_router(payments.router)

    @v1.get("/health", tags=["Health"], summary="Health check (includes database connectivity)")
    def health(request: Request):
        db_ok = ping(request.app.state.engine)
        return JSONResponse(
            {"status": "ok" if db_ok else "degraded", "database": "up" if db_ok else "down", "version": "v1"},
            status_code=200 if db_ok else 503,
        )

    app.include_router(v1)

    @app.get("/health", include_in_schema=False)
    def liveness():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(f"{API_V1}/docs")

    return app


app = create_app()
