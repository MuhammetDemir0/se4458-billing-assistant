"""The agent loop.

    user question
      -> LLM chooses tools (function calling)
      -> agent executes them: MCP tools via the MCP client, REST tools via the API Gateway
      -> results go back to the LLM
      -> repeat (max N rounds) until the LLM answers in text

The LLM never talks to a backend itself and never holds a token. Payments are
only executed by confirm_payment(), which is triggered by the user, not the LLM.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.conversations import Conversation
from app.identity import UserContext
from app.llm.base import LLMClient, LLMError, Message, ToolResult, ToolSpec
from app.payments import PaymentIntentStore
from app.prompts import build_system_prompt
from app.tools.base import ToolProvider
from app.tools.billing_tools import BillingGatewayClient
from app.trace import Trace

logger = logging.getLogger("agent")


@dataclass
class ChatResult:
    conversation_id: str
    answer: str
    trace: list[dict]
    pending_payment: dict | None = None
    payment: dict | None = None
    usage: dict[str, int] = field(default_factory=dict)


class BillingAssistant:
    def __init__(self, llm: LLMClient, providers: list[ToolProvider], gateway: BillingGatewayClient,
                 intents: PaymentIntentStore, max_rounds: int = 5,
                 today: Callable[[], date] = lambda: datetime.now(UTC).date()) -> None:
        self._llm = llm
        self._providers = providers
        self._gateway = gateway
        self._intents = intents
        self._max_rounds = max_rounds
        self._today = today

    def _tools(self) -> tuple[list[ToolSpec], dict[str, ToolProvider]]:
        specs, owners = [], {}
        for provider in self._providers:
            for spec in provider.specs():
                specs.append(spec)
                owners[spec.name] = provider
        return specs, owners

    async def chat(self, ctx: UserContext, conversation: Conversation, question: str) -> ChatResult:
        ctx = replace(ctx, conversation_id=conversation.id)
        trace = Trace(ctx.request_id)
        specs, owners = self._tools()
        system = build_system_prompt(ctx.subscriber_no, self._today())
        checkpoint = len(conversation.messages)
        conversation.messages.append(Message.user(question))
        usage: dict[str, int] = {}

        try:
            for _ in range(self._max_rounds):
                with trace.step("llm", self._llm.model, {"tools": [s.name for s in specs]}) as step:
                    response = await self._llm.generate(system, conversation.window(), specs)
                    step.name = self._llm.model  # the model that actually answered (may be the fallback)
                    step.output = {"toolCalls": [{"name": c.name, "args": c.args} for c in response.tool_calls],
                                   "text": response.text, "usage": response.usage}
                for key, value in response.usage.items():
                    usage[key] = usage.get(key, 0) + value
                conversation.messages.append(Message(role="assistant", text=response.text,
                                                     tool_calls=list(response.tool_calls),
                                                     provider_raw=response.provider_raw))
                if not response.tool_calls:
                    return self._result(conversation, response.text, trace, usage)

                results = []
                for call in response.tool_calls:
                    provider = owners.get(call.name)
                    with trace.step(provider.kind if provider else "agent", call.name, call.args) as step:
                        if provider is None:
                            step.status = "error"
                            content: dict[str, Any] = {"error": {"source": "agent",
                                                                 "detail": f"Unknown tool {call.name}"}}
                        else:
                            try:
                                content = await provider.call(call.name, call.args, ctx, step)
                            except (httpx.HTTPError, OSError) as exc:
                                step.status = "error"
                                content = {"error": {"source": provider.kind,
                                                     "detail": f"Service unavailable: {type(exc).__name__}"}}
                        step.output = content
                    results.append(ToolResult(call_id=call.id, name=call.name, content=content))
                conversation.messages.append(Message(role="tool", tool_results=results))

            answer = ("I could not finish this request within the allowed number of steps. "
                      "Please ask a more specific question.")
            conversation.messages.append(Message(role="assistant", text=answer))
            return self._result(conversation, answer, trace, usage)
        except LLMError as exc:
            del conversation.messages[checkpoint:]  # keep history consistent for the next question
            return self._result(conversation, f"Sorry, the assistant is temporarily unavailable: {exc}", trace, usage)
        finally:
            trace.log("chat", conversationId=conversation.id, subscriber=ctx.subscriber_no)

    def _result(self, conversation: Conversation, answer: str, trace: Trace, usage: dict) -> ChatResult:
        pending = self._intents.pending_for(conversation.id)
        return ChatResult(conversation_id=conversation.id, answer=answer, trace=trace.to_list(),
                          pending_payment=pending.public_view() if pending else None, usage=usage)

    async def confirm_payment(self, ctx: UserContext, conversation: Conversation, intent_id: str) -> ChatResult:
        """Execute a prepared payment after the user's explicit confirmation (not an LLM decision)."""
        trace = Trace(ctx.request_id)
        try:
            with trace.step("confirmation", "user_confirmed_payment", {"paymentIntentId": intent_id}) as step:
                intent = await self._intents.claim(intent_id, ctx.subscriber_no, "CONFIRMED")
                step.output = intent.public_view()

            with trace.step("rest", "pay_bill", {"period": f"{intent.year}-{intent.month:02d}",
                                                 "amount": float(intent.amount), "transactionId": intent.id}) as step:
                try:
                    status, body = await self._gateway.pay_bill(ctx, intent.year, intent.month, intent.amount,
                                                                intent.id, step)
                except httpx.HTTPError:
                    await self._intents.release(intent.id)
                    raise
                step.output = body
                if status >= 400:
                    step.status = "error"
        finally:
            trace.log("confirm_payment", conversationId=conversation.id, subscriber=ctx.subscriber_no)

        period = f"{intent.year}-{intent.month:02d}"
        payment = None
        if status == 201:
            payment = body
            answer = (f"Payment recorded: ${Decimal(str(body['paidAmount'])):.2f} for the {period} bill of "
                      f"subscriber {ctx.subscriber_no} (transaction {body['transactionId']}). "
                      f"Remaining balance: ${Decimal(str(body['remainingBalance'])):.2f}, "
                      f"bill status: {body['billStatus']}.")
        elif status == 409 and body.get("code") == "DUPLICATE_TRANSACTION":
            answer = f"This payment (transaction {intent.id}) was already processed. No second payment was made."
        else:
            if status >= 500:
                await self._intents.release(intent.id)
            answer = f"The payment was not completed: {body.get('detail') or body.get('message') or status}."

        conversation.messages.append(Message.user(f"I confirm payment request {intent.id}."))
        conversation.messages.append(Message(role="assistant", text=answer))
        return ChatResult(conversation_id=conversation.id, answer=answer, trace=trace.to_list(), payment=payment)

    async def cancel_payment(self, ctx: UserContext, conversation: Conversation, intent_id: str) -> ChatResult:
        trace = Trace(ctx.request_id)
        with trace.step("confirmation", "user_cancelled_payment", {"paymentIntentId": intent_id}) as step:
            intent = await self._intents.claim(intent_id, ctx.subscriber_no, "CANCELLED")
            step.output = intent.public_view()
        answer = "The payment request was cancelled. No payment was made."
        conversation.messages.append(Message.user(f"I cancel payment request {intent.id}."))
        conversation.messages.append(Message(role="assistant", text=answer))
        return ChatResult(conversation_id=conversation.id, answer=answer, trace=trace.to_list())
