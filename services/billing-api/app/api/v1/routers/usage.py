"""Usage controller: thin HTTP adapter over IUsageService."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import SubscriberNo, get_usage_service, require_admin
from app.api.v1.schemas import AddUsageRequest, AddUsageResponse, ProblemDetails, to_usage_dto
from app.services.interfaces import IUsageService

router = APIRouter(prefix="/subscribers/{subscriber_no}", tags=["Usage"])

ERRORS = {code: {"model": ProblemDetails} for code in (400, 401, 403, 404)}


@router.post(
    "/usage",
    status_code=status.HTTP_201_CREATED,
    response_model=AddUsageResponse,
    responses=ERRORS,
    summary="Add Usage",
    description="Records one usage event: PHONE adds 10 minutes, INTERNET adds 1 MB. Requires billing-admin.",
    dependencies=[Depends(require_admin)],
)
def add_usage(
    subscriber_no: SubscriberNo,
    body: AddUsageRequest,
    service: Annotated[IUsageService, Depends(get_usage_service)],
) -> AddUsageResponse:
    record = service.add_usage(subscriber_no, body.year, body.month, body.usage_type)
    return AddUsageResponse(subscriber_no=subscriber_no, year=record.year, month=record.month,
                            record=to_usage_dto(record))
