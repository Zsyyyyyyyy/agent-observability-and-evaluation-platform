"""Dependency-free OpenAI-compatible Chat Completions client."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelClientError(RuntimeError):
    """A safe-to-record model request failure; never includes credentials."""


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
    """Small client usable with OpenAI and Chat-Completions-compatible gateways."""

    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout: int = 60,
                 temperature: float = 0.0, top_p: float = 1.0, seed: int | None = None):
        if not api_key or not model:
            raise ModelClientError("AGENT_API_KEY and AGENT_MODEL must both be configured")
        if not 0.0 <= temperature <= 2.0 or not 0.0 <= top_p <= 1.0:
            raise ModelClientError("temperature must be in [0, 2] and top_p in [0, 1]")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleClient":
        try:
            temperature = float(os.environ.get("AGENT_TEMPERATURE", "0"))
            top_p = float(os.environ.get("AGENT_TOP_P", "1"))
            seed_value = os.environ.get("AGENT_SEED")
            seed = int(seed_value) if seed_value is not None and seed_value.strip() else None
        except ValueError as exc:
            raise ModelClientError("invalid model sampling configuration") from exc
        return cls(
            api_key=os.environ.get("AGENT_API_KEY", ""), model=os.environ.get("AGENT_MODEL", ""),
            base_url=os.environ.get("AGENT_BASE_URL", "https://api.openai.com/v1"),
            timeout=int(os.environ.get("AGENT_REQUEST_TIMEOUT_SECONDS", "60")),
            temperature=temperature, top_p=top_p, seed=seed,
        )

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> ModelReply:
        payload = {
            "model": self.model, "messages": messages, "tools": tools,
            "tool_choice": "auto", "max_tokens": max_tokens,
            "temperature": self.temperature, "top_p": self.top_p,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        raw = self._request(payload)
        return parse_chat_completion(raw)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ModelClientError(f"model HTTP error: {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelClientError(f"model request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise ModelClientError("model returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ModelClientError("model returned an invalid response shape")
        return decoded


def parse_chat_completion(payload: dict[str, Any]) -> ModelReply:
    """Parse only the provider-neutral subset required by the ReAct loop."""

    try:
        choice = payload["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelClientError("model response is missing choices[0].message") from exc
    if not isinstance(message, dict):
        raise ModelClientError("model response message must be an object")
    calls: list[ToolCall] = []
    for item in message.get("tool_calls") or []:
        function = item.get("function") if isinstance(item, dict) else None
        try:
            arguments = json.loads(function["arguments"] or "{}")
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
            calls.append(ToolCall(str(item["id"]), str(function["name"]), arguments))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ModelClientError("model returned an invalid tool call") from exc
    usage = payload.get("usage") or {}
    return ModelReply(
        text=str(message.get("content") or ""),
        tool_calls=tuple(calls),
        finish_reason=choice.get("finish_reason"),
        usage={key: int(value) for key, value in usage.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)},
    )
