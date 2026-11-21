"""Google Gemini implementation of LLMClient (google-genai SDK, function calling).

Automatic function calling is disabled: Gemini only *selects* tools and
arguments; the agent executes them and sends the results back.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import errors, types

from app.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

SKIP_SIGNATURE = b"skip_thought_signature_validator"


@dataclass(frozen=True)
class GeminiTurn:
    """Gemini's own content for an assistant turn, tagged with the model that produced it."""

    model: str
    content: types.Content


def to_contents(messages: list[Message], model: str | None = None) -> list[types.Content]:
    contents: list[types.Content] = []
    for m in messages:
        if m.role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=m.text)]))
        elif m.role == "assistant":
            raw = m.provider_raw
            if isinstance(raw, GeminiTurn) and (model is None or raw.model == model):
                contents.append(raw.content)  # same model: keep thought signatures intact
                continue
            # Rebuilt for another model (e.g. after a fallback). Gemini 3 rejects function calls without a
            # thought signature, and a foreign signature is invalid, so the first call carries the documented
            # placeholder that tells the API to skip signature validation for transferred history.
            parts = [types.Part(text=m.text)] if m.text else []
            parts += [types.Part(function_call=types.FunctionCall(name=c.name, args=c.args),
                                 thought_signature=SKIP_SIGNATURE if i == 0 else None)
                      for i, c in enumerate(m.tool_calls)]
            contents.append(types.Content(role="model", parts=parts))
        else:  # tool results go back as function responses in a user turn
            contents.append(types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(
                    id=None if r.call_id.startswith("local-") else r.call_id, name=r.name, response=r.content))
                for r in m.tool_results
            ]))
    return contents


def to_tools(specs: list[ToolSpec]) -> list[types.Tool] | None:
    if not specs:
        return None
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(name=s.name, description=s.description, parameters_json_schema=s.parameters)
        for s in specs
    ])]


def parse_response(response: types.GenerateContentResponse, model: str = "") -> LLMResponse:
    candidate = (response.candidates or [None])[0]
    content = candidate.content if candidate and candidate.content else types.Content(role="model", parts=[])
    texts, calls = [], []
    for part in content.parts or []:
        if part.function_call:
            fc = part.function_call
            calls.append(ToolCall(id=fc.id or f"local-{uuid.uuid4().hex[:8]}", name=fc.name, args=dict(fc.args or {})))
        elif part.text and not part.thought:
            texts.append(part.text)
    usage = {}
    if response.usage_metadata:
        um = response.usage_metadata
        usage = {"promptTokens": um.prompt_token_count or 0, "outputTokens": um.candidates_token_count or 0,
                 "thinkingTokens": um.thoughts_token_count or 0}
    return LLMResponse(text="".join(texts).strip(), tool_calls=calls, usage=usage,
                       provider_raw=GeminiTurn(model=model, content=content))


RETRYABLE = {429, 500, 503, 504}
FALLBACK_RESERVE_SECONDS = 6.0  # time kept for the fallback model when the primary is tried first
MIN_ATTEMPT_SECONDS = 2.0


class GeminiLLMClient:
    """Gemini with time limits and an optional fallback model.

    * Each call gets at most `timeout_seconds`, and never more than the caller's remaining budget
      (`timeout` argument), so a whole chat turn stays below the API Gateway's 29 s limit.
    * If the primary model is overloaded, rate-limited or too slow, the request goes to the fallback model,
      and the client stays on the fallback for `sticky_seconds`. Staying put matters because Gemini 3
      models expect their own thought signatures back on the function calls of the current turn.
    """

    def __init__(self, api_key: str, model: str, max_output_tokens: int = 2048, *,
                 fallback_model: str | None = None, timeout_seconds: float = 10.0,
                 sticky_seconds: float = 120.0, thinking_level: str | None = "low") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self._client = genai.Client(api_key=api_key)
        self.primary = model
        self.fallback = fallback_model if fallback_model and fallback_model != model else None
        self.model = model  # model used for the latest response (shown in the trace)
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout_seconds
        self._sticky = sticky_seconds
        self._thinking_level = thinking_level
        self._fallback_until = 0.0

    def _order(self) -> list[str]:
        if self.fallback and time.monotonic() < self._fallback_until:
            return [self.fallback]
        return [self.primary] + ([self.fallback] if self.fallback else [])

    def _use_fallback_for_a_while(self, model: str) -> None:
        if model == self.primary and self.fallback:
            self._fallback_until = time.monotonic() + self._sticky

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSpec],
                       timeout: float | None = None) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=to_tools(tools),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=self._max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking_level) if self._thinking_level else None,
        )
        deadline = time.monotonic() + (timeout if timeout is not None else 60.0)
        order = self._order()
        last_error: Exception | None = None
        for model in order:
            remaining = deadline - time.monotonic()
            has_backup = model == self.primary and len(order) > 1
            limit = min(self._timeout, remaining - (FALLBACK_RESERVE_SECONDS if has_backup else 0.0))
            if limit < MIN_ATTEMPT_SECONDS:
                if has_backup:
                    continue  # not enough time for both: go straight to the faster fallback
                break
            try:
                # wait_for enforces the limit whatever HTTP library the SDK uses underneath.
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=model, contents=to_contents(messages, model), config=config),
                    timeout=limit,
                )
            except errors.APIError as exc:
                last_error = exc
                if exc.code in RETRYABLE:
                    self._use_fallback_for_a_while(model)
                    continue
                raise LLMError(f"The language model rejected the request ({exc.code}).") from exc
            except (TimeoutError, httpx.TimeoutException) as exc:
                last_error = exc
                self._use_fallback_for_a_while(model)
                continue
            self.model = model
            return parse_response(response, model)

        code = getattr(last_error, "code", "timeout")
        if code == 429:
            raise LLMError("The language model rate limit was reached. Please retry in a minute.") from last_error
        raise LLMError(f"The language model is busy or unavailable ({code}). Please retry shortly.") from last_error
