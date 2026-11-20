"""Payment controller: thin HTTP adapter over IPaymentService."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import Month, SubscriberNo, Year, get_payment_service, require_subscriber_owner
from app.api.v1.schemas import PayBillRequest, PayBillResponse, ProblemDetails, to_payment_response
from app.services.interfaces import IPaymentService

router = APIRouter(prefix="/subscribers/{subscriber_no}/bills/{year}/{month}", tags=["Payments"])


@router.post(
    "/payments",
    status_code=status.HTTP_201_CREATED,
    response_model=PayBillResponse,
    responses={code: {"model": ProblemDetails} for code in (400, 401, 403, 404, 409, 422)},
    summary="Pay Bill",
    description=(
        "Records a simulated full or partial payment (no card processing). Rejects duplicate transaction ids "
        "(409) and amounts above the remaining balance (422). Only the account owner."
    ),
    dependencies=[Depends(require_subscriber_owner)],
)
def pay_bill(
    subscriber_no: SubscriberNo,
    year: Year,
    month: Month,
    body: PayBillRequest,
    service: Annotated[IPaymentService, Depends(get_payment_service)],
) -> PayBillResponse:
    result = service.pay_bill(subscriber_no, year, month, body.amount, body.transaction_id)
    return to_payment_response(result)
