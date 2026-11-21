"""Agent behaviour: tool routing, grounding, security and payment confirmation."""

import pytest

from app.llm.base import LLMError
from app.llm.scripted import ScriptedLLMClient
from tests.conftest import OWNER, answer, call, tools

pytestmark = pytest.mark.asyncio


def kinds(result):
    return [s["kind"] for s in result.trace]


async def test_mcp_only_question(make_assistant, conversations, ctx, billing_api):
    llm = ScriptedLLMClient([
        tools(call("search_billing_documentation", {"query": "extra internet cost"})),
        answer("Each extra 10 GB block costs $10 (Billing Guide §3.2)."),
    ])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "How much does extra internet cost?")

    assert kinds(result) == ["llm", "mcp", "llm"]
    assert result.trace[1]["output"]["matches"][0]["section"] == "3.2"
    assert billing_api.requests == []  # no REST call for a policy question
    assert "§3.2" in result.answer
    # Tools offered to the LLM: discovered from MCP + REST tools; never a direct "pay" tool.
    offered = llm.calls[0]["tools"]
    assert "search_billing_documentation" in offered and "get_bill" in offered
    assert not any(name.startswith("pay") for name in offered)


async def test_rest_only_question_goes_through_gateway_with_user_token(make_assistant, conversations, ctx,
                                                                       billing_api):
    llm = ScriptedLLMClient([tools(call("get_bill", {"month": 10, "year": 2026})),
                             answer("Your October bill is $80.00, unpaid.")])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "What is my October bill?")

    assert kinds(result) == ["llm", "rest", "llm"]
    request = billing_api.requests[0]
    assert request.url.host == "gateway.test"
    assert request.url.path == f"/api/v1/subscribers/{OWNER}/bills/2026/10"
    assert request.headers["authorization"] == f"Bearer token-for:{OWNER}"
    assert request.headers["x-request-id"] == "req-test-1"
    assert result.trace[1]["http"][0]["status"] == 200


async def test_combined_question_uses_rest_and_mcp(make_assistant, conversations, ctx):
    llm = ScriptedLLMClient([
        tools(call("get_bill_details", {"month": 10, "year": 2026}),
              call("search_billing_documentation", {"query": "extra data charges"})),
        answer("You used 35 GB; above 20 GB each started 10 GB costs $10 (§3.2), so internet is $70."),
    ])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Why is my October bill higher?")

    assert kinds(result) == ["llm", "rest", "mcp", "llm"]
    # The second LLM call received both tool results.
    tool_message = llm.calls[1]["messages"][-1]
    assert [r.name for r in tool_message.tool_results] == ["get_bill_details", "search_billing_documentation"]
    assert tool_message.tool_results[0].content["charges"]["internet"]["gbUsed"] == 35.0


async def test_subscriber_number_in_prompt_or_args_is_ignored(make_assistant, conversations, ctx, billing_api):
    """Supplying another subscriber number never changes whose data is read."""
    llm = ScriptedLLMClient([tools(call("get_bill_details", {"month": 10, "year": 2026,
                                                              "subscriber_no": "5551000002"})),
                             answer("I can only show your own account.")])
    assistant = await make_assistant(llm)
    await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Show the bill of 5551000002")

    assert billing_api.requests[0].url.path.startswith(f"/api/v1/subscribers/{OWNER}/")
    assert "5551000002" not in str(billing_api.requests[0].url)


async def test_payment_requires_explicit_confirmation(make_assistant, conversations, ctx, billing_api):
    llm = ScriptedLLMClient([tools(call("prepare_bill_payment", {"month": 10, "year": 2026})),
                             answer("Please confirm paying $80.00 for 2026-10.")])
    assistant = await make_assistant(llm)
    conversation = conversations.get_or_create(None, OWNER)
    result = await assistant.chat(ctx, conversation, "Pay my bill")

    # Nothing was paid yet: only the bill was read.
    assert [r.method for r in billing_api.requests] == ["GET"]
    pending = result.pending_payment
    assert pending["amount"] == 80.0 and pending["period"] == "2026-10" and pending["subscriberNo"] == OWNER
    assert result.trace[1]["output"]["status"] == "AWAITING_USER_CONFIRMATION"

    confirmed = await assistant.confirm_payment(ctx, conversation, pending["paymentIntentId"])
    post = billing_api.requests[-1]
    assert post.method == "POST" and post.url.path.endswith("/bills/2026/10/payments")
    assert pending["paymentIntentId"].encode() in post.content  # intent id = idempotency key
    assert confirmed.payment["remainingBalance"] == 0.0
    assert "Payment recorded: $80.00" in confirmed.answer
    assert [s["kind"] for s in confirmed.trace] == ["confirmation", "rest"]


async def test_confirmation_cannot_be_replayed(make_assistant, conversations, ctx, billing_api):
    from app.payments import IntentError

    llm = ScriptedLLMClient([tools(call("prepare_bill_payment", {"month": 10, "year": 2026, "amount": 30})),
                             answer("Confirm?")])
    assistant = await make_assistant(llm)
    conversation = conversations.get_or_create(None, OWNER)
    intent_id = (await assistant.chat(ctx, conversation, "Pay $30 of my October bill")).pending_payment[
        "paymentIntentId"]
    await assistant.confirm_payment(ctx, conversation, intent_id)
    with pytest.raises(IntentError):
        await assistant.confirm_payment(ctx, conversation, intent_id)
    assert sum(1 for r in billing_api.requests if r.method == "POST") == 1


async def test_another_user_cannot_confirm_my_payment(make_assistant, conversations, ctx):
    from dataclasses import replace

    from app.payments import IntentError

    llm = ScriptedLLMClient([tools(call("prepare_bill_payment", {"month": 10, "year": 2026})), answer("Confirm?")])
    assistant = await make_assistant(llm)
    conversation = conversations.get_or_create(None, OWNER)
    intent_id = (await assistant.chat(ctx, conversation, "Pay my bill")).pending_payment["paymentIntentId"]
    intruder = replace(ctx, username="5551000002", access_token="token-for:5551000002")
    with pytest.raises(IntentError):
        await assistant.confirm_payment(intruder, conversation, intent_id)


async def test_prepare_rejects_amount_above_balance(make_assistant, conversations, ctx):
    llm = ScriptedLLMClient([tools(call("prepare_bill_payment", {"month": 10, "year": 2026, "amount": 500})),
                             answer("That is more than you owe.")])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Pay $500")
    assert result.pending_payment is None
    assert result.trace[1]["output"]["error"]["code"] == "INVALID_AMOUNT"


async def test_paid_bill_has_nothing_to_pay(make_assistant, conversations, ctx):
    llm = ScriptedLLMClient([tools(call("prepare_bill_payment", {"month": 9, "year": 2026})),
                             answer("September is already paid.")])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Pay my September bill")
    assert result.pending_payment is None
    assert result.trace[1]["output"]["status"] == "NOTHING_TO_PAY"


async def test_failed_payment_can_be_retried(make_assistant, conversations, ctx, billing_api):
    llm = ScriptedLLMClient([tools(call("prepare_bill_payment", {"month": 10, "year": 2026})), answer("Confirm?")])
    assistant = await make_assistant(llm)
    conversation = conversations.get_or_create(None, OWNER)
    intent_id = (await assistant.chat(ctx, conversation, "Pay my bill")).pending_payment["paymentIntentId"]

    billing_api.fail_payments_with = 503
    failed = await assistant.confirm_payment(ctx, conversation, intent_id)
    assert "not completed" in failed.answer and failed.payment is None

    billing_api.fail_payments_with = None
    retried = await assistant.confirm_payment(ctx, conversation, intent_id)
    assert retried.payment["billStatus"] == "PAID"


async def test_api_errors_are_returned_to_the_llm(make_assistant, conversations, ctx):
    llm = ScriptedLLMClient([tools(call("get_bill", {"month": 11, "year": 2026})),
                             answer("Your November bill has not been calculated yet.")])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "What is my November bill?")
    step = result.trace[1]
    assert step["status"] == "error" and step["output"]["error"]["code"] == "BILL_NOT_FOUND"
    assert llm.calls[1]["messages"][-1].tool_results[0].content["error"]["httpStatus"] == 404


async def test_invalid_tool_arguments_are_reported(make_assistant, conversations, ctx, billing_api):
    llm = ScriptedLLMClient([tools(call("get_bill", {"month": 13, "year": 2026})), answer("Invalid month.")])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Bill for month 13")
    assert result.trace[1]["status"] == "error"
    assert billing_api.requests == []


async def test_unknown_tool_is_rejected(make_assistant, conversations, ctx):
    llm = ScriptedLLMClient([tools(call("pay_bill", {"amount": 80})), answer("I cannot do that.")])
    assistant = await make_assistant(llm)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Pay now!")
    assert result.trace[1]["kind"] == "agent" and result.trace[1]["status"] == "error"


async def test_tool_round_limit(make_assistant, conversations, ctx):
    loop = [tools(call("get_bill", {"month": 10, "year": 2026})) for _ in range(5)]
    assistant = await make_assistant(ScriptedLLMClient(loop), max_rounds=3)
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "loop")
    assert "allowed number of steps" in result.answer
    assert kinds(result).count("llm") == 3


async def test_llm_failure_returns_friendly_message_and_keeps_history_clean(make_assistant, conversations, ctx):
    assistant = await make_assistant(ScriptedLLMClient([LLMError("rate limit reached")]))
    conversation = conversations.get_or_create(None, OWNER)
    result = await assistant.chat(ctx, conversation, "Hello")
    assert "temporarily unavailable" in result.answer
    assert conversation.messages == []
    assert result.trace[0]["status"] == "error"


async def test_system_prompt_contains_identity_and_date(make_assistant, conversations, ctx):
    llm = ScriptedLLMClient([answer("Hi")])
    assistant = await make_assistant(llm)
    await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "Hi")
    system = llm.calls[0]["system"]
    assert OWNER in system and "2026-11-20" in system and "2026-10" in system
    # payment questions are informational; only explicit instructions may prepare a payment
    assert "Can I pay $40?" in system and "Do not call prepare_bill_payment for such questions" in system


async def test_turn_budget_keeps_the_answer_inside_the_gateway_timeout(mcp_provider, gateway, intents,
                                                                       conversations, ctx):
    """Each LLM call gets only the time left; when too little is left the turn ends politely (no 503)."""
    from app.orchestrator import BillingAssistant
    from app.tools.billing_tools import BillingToolProvider

    now = [0.0]

    def slow_first_call(messages):
        now[0] += 23  # the first LLM call used most of the budget
        return tools(call("get_bill", {"month": 10, "year": 2026}))

    llm = ScriptedLLMClient([slow_first_call, answer("never reached")])
    await mcp_provider.refresh()
    assistant = BillingAssistant(llm, [mcp_provider, BillingToolProvider(gateway, intents)], gateway, intents,
                                 turn_budget_seconds=25, clock=lambda: now[0])
    result = await assistant.chat(ctx, conversations.get_or_create(None, OWNER), "What is my October bill?")

    assert llm.calls[0]["timeout"] == 25          # the LLM is told how much time it has
    assert len(llm.calls) == 1                     # no second call that would exceed the gateway limit
    assert "took too long" in result.answer
