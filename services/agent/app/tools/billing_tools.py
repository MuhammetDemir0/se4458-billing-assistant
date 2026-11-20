"""REST tools: live billing data through the API Gateway.

Security properties:
* Every call goes to GATEWAY_URL (never to the Billing API directly) with the
  signed-in user's access token and the request's X-Request-ID.
* The subscriber number is taken from the token (UserContext), never from LLM
  arguments: the tool schemas do not even have a subscriber parameter, and any
  extra argument the model invents is ignored.
* There is no "pay" tool. `prepare_bill_payment` only creates a payment intent
  that the user must confirm (see app/payments.py).
"""

import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import httpx

from app.identity import UserContext
from app.llm.base import ToolSpec
from app.payments import PaymentIntentStore
from app.trace import TraceStep

CENT = Decimal("0.01")
PERIOD_PROPS = {
    "month": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Billing month, 1-12"},
    "year": {"type": "integer", "minimum": 2000, "maximum": 2100, "description": "Billing year, e.g. 2026"},
}


class BillingGatewayClient:
    """Typed HTTP client for the Billing API's public contract (/api/v1) behind the gateway."""

    def __init__(self, gateway_url: str, timeout: float, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=gateway_url.rstrip("/"), timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, ctx: UserContext, step: TraceStep | None = None,
                      **kwargs: Any) -> tuple[int, dict[str, Any]]:
        headers = {"Authorization": f"Bearer {ctx.access_token}", "Accept": "application/json"}
        if ctx.request_id:
            headers["X-Request-ID"] = ctx.request_id
        started = time.perf_counter()
        response = await self._client.request(method, path, headers=headers, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:200]}
        if step is not None:
            step.http.append({"method": method, "path": path, "status": response.status_code,
                              "latencyMs": round((time.perf_counter() - started) * 1000, 1)})
        return response.status_code, body

    @staticmethod
    def bill_path(ctx: UserContext, year: int, month: int) -> str:
        return f"/api/v1/subscribers/{ctx.subscriber_no}/bills/{year}/{month}"

    async def get_bill(self, ctx, year, month, step=None):
        return await self.request("GET", self.bill_path(ctx, year, month), ctx, step)

    async def get_bill_details(self, ctx, year, month, page, page_size, step=None):
        return await self.request("GET", self.bill_path(ctx, year, month) + "/details", ctx, step,
                                  params={"page": page, "pageSize": page_size})

    async def pay_bill(self, ctx, year, month, amount: Decimal, transaction_id: str, step=None):
        return await self.request("POST", self.bill_path(ctx, year, month) + "/payments", ctx, step,
                                  json={"amount": float(amount), "transactionId": transaction_id})


def _error(status: int, body: dict) -> dict:
    return {"error": {"source": "billing-api", "httpStatus": status, "code": body.get("code"),
                      "detail": body.get("detail") or body.get("message") or body.get("title")}}


def _period(args: dict) -> tuple[int, int]:
    year, month = int(args["year"]), int(args["month"])
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        raise ValueError("month must be 1-12 and year 2000-2100")
    return year, month


class BillingToolProvider:
    kind = "rest"

    def __init__(self, client: BillingGatewayClient, intents: PaymentIntentStore) -> None:
        self._client = client
        self._intents = intents

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="get_bill",
                description=("Query Bill API: stored bill total, paid amount, remaining balance and payment status of "
                             "the signed-in subscriber for one month."),
                parameters={"type": "object", "properties": PERIOD_PROPS, "required": ["month", "year"]},
            ),
            ToolSpec(
                name="get_bill_details",
                description=("Query Bill Detailed API: phone minutes, internet MB/GB, charge breakdown and a page of "
                             "usage records of the signed-in subscriber for one month. Use it to explain charges."),
                parameters={
                    "type": "object",
                    "properties": {
                        **PERIOD_PROPS,
                        "page": {"type": "integer", "minimum": 1, "description": "Usage record page (default 1)"},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 50,
                                      "description": "Usage records per page (default 10)"},
                    },
                    "required": ["month", "year"],
                },
            ),
            ToolSpec(
                name="prepare_bill_payment",
                description=("Prepare (NOT execute) a payment of the signed-in subscriber's bill. Returns a payment "
                             "request that the user must explicitly confirm with the Confirm button. Omit amount to "
                             "pay the full remaining balance."),
                parameters={
                    "type": "object",
                    "properties": {**PERIOD_PROPS,
                                   "amount": {"type": "number", "exclusiveMinimum": 0,
                                              "description": "Amount in USD; omit for the full remaining balance"}},
                    "required": ["month", "year"],
                },
            ),
        ]

    async def call(self, name: str, args: dict[str, Any], ctx: UserContext, step: TraceStep) -> dict[str, Any]:
        try:
            year, month = _period(args)
        except (KeyError, TypeError, ValueError) as exc:
            step.status = "error"
            return {"error": {"source": "agent", "detail": f"Invalid arguments: {exc}"}}

        if name == "get_bill":
            status, body = await self._client.get_bill(ctx, year, month, step)
        elif name == "get_bill_details":
            page = max(1, int(args.get("page") or 1))
            page_size = min(50, max(1, int(args.get("page_size") or 10)))
            status, body = await self._client.get_bill_details(ctx, year, month, page, page_size, step)
        elif name == "prepare_bill_payment":
            return await self._prepare_payment(ctx, year, month, args.get("amount"), step)
        else:
            step.status = "error"
            return {"error": {"source": "agent", "detail": f"Unknown tool {name}"}}

        if status >= 400:
            step.status = "error"
            return _error(status, body)
        return body

    async def _prepare_payment(self, ctx: UserContext, year: int, month: int, amount_arg: Any,
                               step: TraceStep) -> dict[str, Any]:
        status, bill = await self._client.get_bill(ctx, year, month, step)
        if status >= 400:
            step.status = "error"
            return _error(status, bill)
        remaining = Decimal(str(bill["remainingBalance"])).quantize(CENT)
        total = Decimal(str(bill["billTotal"])).quantize(CENT)
        if remaining <= 0:
            return {"status": "NOTHING_TO_PAY", "detail": "This bill is already fully paid.", "bill": bill}
        try:
            amount = remaining if amount_arg in (None, "") else Decimal(str(amount_arg)).quantize(
                CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            step.status = "error"
            return {"error": {"source": "agent", "detail": "amount must be a number"}}
        if amount <= 0 or amount > remaining:
            step.status = "error"
            return {"error": {"source": "agent", "code": "INVALID_AMOUNT",
                              "detail": f"Amount must be between 0.01 and the remaining balance {remaining}."}}

        intent = await self._intents.create(ctx.subscriber_no, ctx.conversation_id, year, month, amount,
                                            remaining, total)
        return {
            **intent.public_view(),
            "status": "AWAITING_USER_CONFIRMATION",
            "instructions": ("No payment has been made. Show the subscriber, period, amount and remaining balance, "
                             "then ask the user to press Confirm (or Cancel). Never say the payment succeeded."),
        }
