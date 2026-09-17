"""LLM layer: provider-neutral interface, adapters, and the offline mock."""

from .base import (
    LLMClient,
    LLMError,
    LLMResponse,
    NullLLMClient,
    ToolCall,
    ToolSpec,
    extract_json,
)
from .mock import MockLLMClient
from .providers import build_client, build_vision_client, describe_client

__all__ = [
    "LLMClient", "LLMError", "LLMResponse", "NullLLMClient",
    "ToolCall", "ToolSpec", "extract_json",
    "MockLLMClient", "build_client", "build_vision_client", "describe_client",
]
