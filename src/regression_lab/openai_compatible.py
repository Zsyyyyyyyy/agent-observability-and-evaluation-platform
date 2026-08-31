"""Shared dependency-free OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelClientError(RuntimeError):
    """Safe diagnostic that deliberately excludes credentials and prompts."""

    def __init__(self, message: str, *, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    text: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str | None
    usage: dict[str, int]


class OpenAICompatibleClient:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout: int = 60,
                 temperature: float = 0.0, top_p: float = 1.0, seed: int | None = None):
        if not api_key or not model:
            raise ModelClientError("AGENT_API_KEY and AGENT_MODEL must both be configured", kind="configuration")
        if not 0.0 <= temperature <= 2.0 or not 0.0 <= top_p <= 1.0:
            raise ModelClientError("temperature must be in [0, 2] and top_p in [0, 1]", kind="configuration")
        self.api_key, self.model, self.base_url, self.timeout = api_key, model, base_url.rstrip("/"), timeout
        self.temperature, self.top_p, self.seed = temperature, top_p, seed

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleClient":
        try:
            temperature = float(os.environ.get("AGENT_TEMPERATURE", "0"))
            top_p = float(os.environ.get("AGENT_TOP_P", "1"))
            seed_value = os.environ.get("AGENT_SEED")
            seed = int(seed_value) if seed_value is not None and seed_value.strip() else None
        except ValueError as exc:
            raise ModelClientError("invalid model sampling configuration", kind="configuration") from exc
        return cls(
            api_key=os.environ.get("AGENT_API_KEY", ""), model=os.environ.get("AGENT_MODEL", ""),
            base_url=os.environ.get("AGENT_BASE_URL", "https://api.openai.com/v1"),
            timeout=int(os.environ.get("AGENT_REQUEST_TIMEOUT_SECONDS", "60")),
            temperature=temperature, top_p=top_p, seed=seed,
        )

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> ModelReply:
        body: dict[str, Any] = {
            "model": self.model, "messages": messages, "tools": tools,
            "tool_choice": "auto", "max_tokens": max_tokens,
            "temperature": self.temperature, "top_p": self.top_p,
        }
        if self.seed is not None:
            body["seed"] = self.seed
        request = Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode("utf-8"), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                kind = "http_429"
            elif 500 <= exc.code <= 599:
                kind = "http_5xx"
            elif 400 <= exc.code <= 499:
                kind = "http_4xx"
            else:
                kind = "http_other"
            raise ModelClientError(f"model HTTP error: {exc.code}", kind=kind) from exc
        except TimeoutError as exc:
            raise ModelClientError("model request timed out", kind="timeout") from exc
        except (URLError, OSError) as exc:
            raise ModelClientError(f"model request failed: {type(exc).__name__}", kind="network") from exc
        except json.JSONDecodeError as exc:
            raise ModelClientError("model returned invalid JSON", kind="invalid_response") from exc
        return parse_chat_completion(payload)


def parse_chat_completion(payload: dict[str, Any]) -> ModelReply:
    """Parse the provider-neutral Chat Completions subset used by Agent loops."""

    try:
        choice = payload["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelClientError(
            "model response is missing choices[0].message", kind="invalid_response"
        ) from exc
    if not isinstance(message, dict):
        raise ModelClientError("model response message must be an object", kind="invalid_response")
    calls: list[ToolCall] = []
    for item in message.get("tool_calls") or []:
        function = item.get("function") if isinstance(item, dict) else None
        try:
            arguments = json.loads(function["arguments"] or "{}")
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
            calls.append(ToolCall(str(item["id"]), str(function["name"]), arguments))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ModelClientError(
                "model returned an invalid tool call", kind="invalid_tool_call"
            ) from exc
    usage = payload.get("usage") or {}
    return ModelReply(
        str(message.get("content") or ""),
        tuple(calls),
        choice.get("finish_reason"),
        {
            key: int(value)
            for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, int)
            and not isinstance(value, bool)
        },
    )
