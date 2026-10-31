"""Conversations with your own LLM (bring your own key).

One interface for every provider: Anthropic's Messages API, and the
OpenAI-compatible chat completions of OpenAI, DeepSeek, Mistral and OpenRouter.
A conversation can ask for JSON answers or offer tools to the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx2

from .config import PROVIDERS, LLMSettings

MAX_OUTPUT_TOKENS = 32_000
# A large model on a long document or a big repository can take minutes
TIMEOUT = httpx2.Timeout(30, read=300)

HINTS = {
    401: "the key is not valid: check LLM_API_KEY",
    402: "the account has no credit left",
    429: "too many requests: try again shortly",
}


class LLMError(RuntimeError):
    pass


class OutputTruncated(LLMError):
    """The answer hit the model's output limit."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None  # None when the model sent arguments that are not a JSON object
    raw_arguments: str


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


def parse_json(text: str) -> Any:
    """The JSON object of an answer, also inside a code fence; None if there is none."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text or "", re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


class Conversation:
    """A conversation that keeps its history in the provider's own format."""

    def __init__(
        self,
        settings: LLMSettings,
        system: str,
        *,
        tools: list[dict] | None = None,
        json_mode: bool = False,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        self.settings = settings
        self.system = system
        self.tools = tools or []  # name, description, input_schema
        self.json_mode = json_mode and not self.tools
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    @property
    def anthropic(self) -> bool:
        return self.settings.provider == "anthropic"

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[str, str]]) -> None:
        """The answers to the tool calls of the last reply: (call id, text)."""
        if self.anthropic:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": call_id, "content": text} for call_id, text in results
                    ],
                }
            )
        else:
            self.messages.extend(
                {"role": "tool", "tool_call_id": call_id, "content": text} for call_id, text in results
            )

    async def reply(self) -> Reply:
        endpoint, _ = PROVIDERS[self.settings.provider]
        async with httpx2.AsyncClient(timeout=TIMEOUT) as http:
            if self.anthropic:
                response = await http.post(
                    endpoint,
                    headers={
                        "x-api-key": self.settings.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    json=self._anthropic_body(),
                )
            else:
                response = await http.post(
                    endpoint, headers={"Authorization": f"Bearer {self.settings.api_key}"}, json=self._openai_body()
                )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message")
            except (ValueError, AttributeError):
                detail = None
            raise LLMError(
                f"{self.settings.provider} {response.status_code}: "
                f"{HINTS.get(response.status_code) or detail or response.text[:300] or 'error'}"
            )
        body = response.json()
        return self._read_anthropic(body) if self.anthropic else self._read_openai(body)

    # Anthropic Messages API

    def _anthropic_body(self) -> dict:
        system = self.system + ("\nReply with one JSON object and nothing else." if self.json_mode else "")
        body: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": self.messages,
        }
        if self.tools:
            body["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                for t in self.tools
            ]
        return body

    def _read_anthropic(self, body: dict) -> Reply:
        usage = body.get("usage", {})
        self.usage["input_tokens"] += usage.get("input_tokens", 0)
        self.usage["output_tokens"] += usage.get("output_tokens", 0)
        self.usage["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)
        blocks = body.get("content", [])
        self.messages.append({"role": "assistant", "content": blocks})
        if body.get("stop_reason") == "max_tokens":
            raise OutputTruncated("the answer hit the model's output limit")
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        calls = [
            ToolCall(
                block["id"],
                block["name"],
                block.get("input") if isinstance(block.get("input"), dict) else None,
                json.dumps(block.get("input")),
            )
            for block in blocks
            if block.get("type") == "tool_use"
        ]
        return Reply(text, calls)

    # OpenAI-compatible chat completions

    def _openai_body(self) -> dict:
        body: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.max_tokens,
            "stream": False,
            "messages": [{"role": "system", "content": self.system}, *self.messages],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": {
                            k: v for k, v in (t["input_schema"] or {"type": "object"}).items() if k != "$schema"
                        },
                    },
                }
                for t in self.tools
            ]
            body["tool_choice"] = "auto"
        if self.settings.provider == "openrouter":
            # JSON mode and tools reach only the upstream providers that support them
            body["provider"] = {"require_parameters": True}
        return body

    def _read_openai(self, body: dict) -> Reply:
        usage = body.get("usage") or {}
        self.usage["input_tokens"] += usage.get("prompt_tokens", 0)
        self.usage["output_tokens"] += usage.get("completion_tokens", 0)
        self.usage["cache_read_input_tokens"] += usage.get("prompt_cache_hit_tokens") or (
            usage.get("prompt_tokens_details") or {}
        ).get("cached_tokens", 0)
        choices = body.get("choices") or []
        if not choices or not choices[0].get("message"):
            raise LLMError(f"{self.settings.provider} returned an empty answer")
        message, finish = choices[0]["message"], choices[0].get("finish_reason")
        # The whole message goes back, reasoning_content included: in thinking
        # mode DeepSeek needs it to continue after a tool call.
        self.messages.append(message)
        if finish == "length":
            raise OutputTruncated("the answer hit the model's output limit")
        if finish == "content_filter":
            raise LLMError(f"{self.settings.provider} blocked the answer (content_filter)")
        if finish == "insufficient_system_resource":
            raise LLMError(f"{self.settings.provider} is overloaded: try again shortly")
        calls = []
        for call in message.get("tool_calls") or []:
            raw = call.get("function", {}).get("arguments") or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError:
                arguments = None
            calls.append(
                ToolCall(call["id"], call["function"]["name"], arguments if isinstance(arguments, dict) else None, raw)
            )
        return Reply(message.get("content") or "", calls)


async def complete_json(settings: LLMSettings, system: str, user: str) -> Any:
    """One question, answered with a JSON object. JSON mode can return an empty
    answer now and then (DeepSeek documents it), so the question is asked twice
    at most."""
    for _ in range(2):
        conversation = Conversation(settings, system, json_mode=True)
        conversation.add_user(user)
        answer = parse_json((await conversation.reply()).content)
        if isinstance(answer, dict):
            return answer
    raise LLMError("the model did not answer with a JSON object")
