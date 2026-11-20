"""Bill controller: thin HTTP adapter over IBillingService."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Month, SubscriberNo, Year, get_billing_service, require_admin, require_subscriber_access
from app.api.v1.schemas import (
    BillDetailsResponse,
    BillSummaryResponse,
    CalculateBillResponse,
    ProblemDetails,
    to_bill_details,
    to_bill_summary,
)
from app.services.interfaces import IBillingService

router = APIRouter(prefix="/subscribers/{subscriber_no}/bills/{year}/{month}", tags=["Bills"])

Service = Annotated[IBillingService, Depends(get_billing_service)]


def _errors(*codes: int) -> dict:
    return {code: {"model": ProblemDetails} for code in codes}


@router.post(
    "/calculate",
    response_model=CalculateBillResponse,
    responses=_errors(400, 401, 403, 404, 409),
    summary="Calculate Bill",
    description=(
        "Calculates the bill from recorded usage and stores it. Safe to repeat: recalculation keeps "
        "payments already made. Requires billing-admin."
    ),
    dependencies=[Depends(require_admin)],
)
def calculate_bill(subscriber_no: SubscriberNo, year: Year, month: Month, service: Service) -> CalculateBillResponse:
    return CalculateBillResponse(bill=to_bill_summary(service.calculate_bill(subscriber_no, year, month)))


@router.get(
    "",
    response_model=BillSummaryResponse,
    responses=_errors(400, 404),
    summary="Query Bill",
    description="Bill total, paid amount, remaining balance and status. Public by specification (no authentication).",
)
def query_bill(subscriber_no: SubscriberNo, year: Year, month: Month, service: Service) -> BillSummaryResponse:
    return to_bill_summary(service.get_bill(subscriber_no, year, month))


@router.get(
    "/details",
    response_model=BillDetailsResponse,
    responses=_errors(400, 401, 403, 404),
    summary="Query Bill Detailed",
    description=(
        "Summary plus phone minutes, internet MB/GB, charge breakdown and a paged list of usage records. "
        "Only the account owner or billing-admin."
    ),
    dependencies=[Depends(require_subscriber_access)],
)
def query_bill_detailed(
    subscriber_no: SubscriberNo,
    year: Year,
    month: Month,
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
) -> BillDetailsResponse:
    return to_bill_details(service.get_bill_details(subscriber_no, year, month, page, page_size))
