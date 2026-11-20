"""Dependency injection wiring (composition root).

FastAPI's Depends() builds the object graph per request:
  Unit of Work (DB session) -> Service -> Controller
Controllers only ask for the *interface* (IBillingService, ...). Tests override
get_uow / get_token_verifier to run without PostgreSQL or Cognito.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import Principal, TokenVerifier
from app.repositories.interfaces import IUnitOfWork
from app.repositories.sqlalchemy_repositories import SqlAlchemyUnitOfWork
from app.services.billing_service import BillingService
from app.services.interfaces import IBillingService, IPaymentService, IUsageService
from app.services.payment_service import PaymentService
from app.services.usage_service import UsageService

SubscriberNo = Annotated[str, Path(pattern=r"^\d{10}$", description="10-digit subscriber number",
                                   examples=["5551000001"])]
Year = Annotated[int, Path(ge=2000, le=2100, examples=[2026])]
Month = Annotated[int, Path(ge=1, le=12, examples=[10])]


def get_uow(request: Request) -> Iterator[IUnitOfWork]:
    uow = SqlAlchemyUnitOfWork(request.app.state.session_factory())
    try:
        yield uow
    finally:
        uow.close()  # rolls back anything not committed


def get_usage_service(uow: Annotated[IUnitOfWork, Depends(get_uow)]) -> IUsageService:
    return UsageService(uow)


def get_billing_service(uow: Annotated[IUnitOfWork, Depends(get_uow)]) -> IBillingService:
    return BillingService(uow)


def get_payment_service(uow: Annotated[IUnitOfWork, Depends(get_uow)]) -> IPaymentService:
    return PaymentService(uow)


def get_token_verifier(request: Request) -> TokenVerifier:
    return request.app.state.token_verifier


_bearer = HTTPBearer(auto_error=False, description="Cognito access token (local: dev token)")


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Missing bearer token.")
    return verifier.verify(credentials.credentials)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise ForbiddenError("This operation requires the billing-admin role.", code="ADMIN_REQUIRED")
    return principal


def require_subscriber_access(subscriber_no: SubscriberNo, principal: CurrentPrincipal) -> Principal:
    """Owner-or-admin rule: knowing someone's number is never enough to read their data."""
    if not principal.can_access_subscriber(subscriber_no):
        raise ForbiddenError("You can only access your own subscriber account.", code="SUBSCRIBER_FORBIDDEN")
    return principal


def require_subscriber_owner(subscriber_no: SubscriberNo, principal: CurrentPrincipal) -> Principal:
    """Payments must be made by the account owner (not even an admin pays on someone's behalf)."""
    if principal.username != subscriber_no:
        raise ForbiddenError("Only the account owner can pay this bill.", code="SUBSCRIBER_FORBIDDEN")
    return principal
