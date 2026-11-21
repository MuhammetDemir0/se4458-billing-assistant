"""Gemini adapter conversions, tested offline (no API call)."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from app.llm.base import LLMError, Message, ToolCall, ToolResult, ToolSpec
from app.llm.gemini import SKIP_SIGNATURE, GeminiLLMClient, GeminiTurn, parse_response, to_contents, to_tools


def test_tool_specs_become_function_declarations():
    tools = to_tools([ToolSpec("get_bill", "Query bill", {"type": "object", "properties": {}})])
    declaration = tools[0].function_declarations[0]
    assert declaration.name == "get_bill"
    assert declaration.parameters_json_schema == {"type": "object", "properties": {}}
    assert to_tools([]) is None


def test_parse_function_calls_and_usage():
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name="get_bill", args={"month": 10, "year": 2026})),
            types.Part(function_call=types.FunctionCall(id="abc", name="search_billing_documentation",
                                                        args={"query": "late fee"})),
        ]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=120, candidates_token_count=15),
    )
    parsed = parse_response(response)
    assert [c.name for c in parsed.tool_calls] == ["get_bill", "search_billing_documentation"]
    assert parsed.tool_calls[0].args == {"month": 10, "year": 2026}
    assert parsed.tool_calls[1].id == "abc"
    assert parsed.usage["promptTokens"] == 120
    assert isinstance(parsed.provider_raw.content, types.Content)


def test_parse_text_ignores_thoughts():
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
        types.Part(text="internal reasoning", thought=True), types.Part(text="Your bill is $80."),
    ]))])
    parsed = parse_response(response)
    assert parsed.text == "Your bill is $80." and parsed.tool_calls == []


def history(raw_model: str):
    raw = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="get_bill", args={}),
                                                        thought_signature=b"sig")])
    return raw, [
        Message.user("What is my bill?"),
        Message(role="assistant", tool_calls=[ToolCall("local-1", "get_bill", {})],
                provider_raw=GeminiTurn(raw_model, raw)),
        Message(role="tool", tool_results=[ToolResult("local-1", "get_bill", {"billTotal": 80.0})]),
    ]


def test_history_round_trip():
    raw, messages = history("model-a")
    contents = to_contents(messages, "model-a")
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1] is raw  # same model: content (and thought signatures) sent back unchanged
    response = contents[2].parts[0].function_response
    assert response.name == "get_bill" and response.response == {"billTotal": 80.0} and response.id is None


def test_other_model_gets_rebuilt_parts_without_foreign_signatures():
    raw, messages = history("model-a")
    rebuilt = to_contents(messages, "model-b")[1]
    assert rebuilt is not raw
    assert rebuilt.parts[0].function_call.name == "get_bill"
    # never the other model's signature; the documented "skip validation" placeholder instead
    assert rebuilt.parts[0].thought_signature == SKIP_SIGNATURE != b"sig"


class FakeModels:
    """Stands in for client.aio.models: fails for the models listed in `failing`."""

    def __init__(self, failing: dict[str, int], delays: dict[str, float] | None = None):
        self.failing = failing
        self.delays = delays or {}
        self.calls: list[str] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        if model in self.delays:
            await asyncio.sleep(self.delays[model])
        if model in self.failing:
            raise errors.ServerError(self.failing[model], {"error": {"message": "busy"}})
        return types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text=f"answer from {model}")]))])


def make_client(failing, delays=None, timeout_seconds=10.0):
    client = GeminiLLMClient("test-key", "primary", fallback_model="backup", timeout_seconds=timeout_seconds)
    fake = FakeModels(failing, delays)
    client._client = SimpleNamespace(aio=SimpleNamespace(models=fake))
    return client, fake


@pytest.mark.asyncio
async def test_primary_model_is_used_when_healthy():
    client, fake = make_client({})
    response = await client.generate("sys", [Message.user("hi")], [])
    assert response.text == "answer from primary" and fake.calls == ["primary"] and client.model == "primary"


@pytest.mark.asyncio
async def test_overloaded_primary_falls_back_and_stays_there():
    client, fake = make_client({"primary": 503})
    first = await client.generate("sys", [Message.user("hi")], [])
    second = await client.generate("sys", [Message.user("again")], [])
    assert first.text == "answer from backup" and client.model == "backup"
    assert second.text == "answer from backup"
    assert fake.calls == ["primary", "backup", "backup"]  # sticky: primary not retried immediately


@pytest.mark.asyncio
async def test_slow_primary_is_cut_off_and_fallback_answers():
    """A primary that hangs (the 504-after-19s case) is abandoned at its time limit."""
    client, fake = make_client({}, delays={"primary": 30}, timeout_seconds=2.1)
    started = time.monotonic()
    response = await client.generate("sys", [Message.user("hi")], [], timeout=25)
    assert response.text == "answer from backup" and fake.calls == ["primary", "backup"]
    assert time.monotonic() - started < 4


@pytest.mark.asyncio
async def test_little_budget_left_skips_primary():
    """With less time left than primary limit + fallback reserve, go straight to the fast fallback."""
    client, fake = make_client({})
    response = await client.generate("sys", [Message.user("hi")], [], timeout=7)
    assert response.text == "answer from backup" and fake.calls == ["backup"]


def test_thinking_level_is_sent_to_gemini():
    client = GeminiLLMClient("test-key", "primary", thinking_level="low")
    assert client._thinking_level == "low"


@pytest.mark.asyncio
async def test_both_models_down_raises_llm_error():
    client, _ = make_client({"primary": 503, "backup": 504})
    with pytest.raises(LLMError, match="busy or unavailable"):
        await client.generate("sys", [Message.user("hi")], [])


def test_assistant_message_without_raw_is_rebuilt():
    contents = to_contents([Message(role="assistant", text="ok",
                                    tool_calls=[ToolCall("x", "get_bill", {"month": 1})])])
    parts = contents[0].parts
    assert parts[0].text == "ok" and parts[1].function_call.name == "get_bill"
