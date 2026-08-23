"""Compatibility import for the shared OpenAI-compatible model client."""

from regression_lab.openai_compatible import (
    ModelClientError,
    ModelReply,
    OpenAICompatibleClient,
    ToolCall,
    parse_chat_completion,
)


__all__ = [
    "ModelClientError",
    "ModelReply",
    "OpenAICompatibleClient",
    "ToolCall",
    "parse_chat_completion",
]
