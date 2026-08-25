"""Provider abstraction (providers boundary).

Every AI runtime — local (Ollama) or future cloud APIs — implements the
``Provider`` interface. The rest of the application never talks to a
runtime directly; it goes through the registry → router → provider.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ProviderStatus:
    name: str
    status: str                      # "running" | "unavailable" | "error"
    is_local: bool = True
    version: str | None = None
    latency_ms: float | None = None
    models_count: int | None = None
    detail: str | None = None        # human-readable error when not running


@dataclass
class ModelInfo:
    """Normalized model description — built only from real provider data."""
    provider: str
    name: str
    display_name: str
    is_local: bool = True
    is_free: bool = True
    cost_input_per_mtok: float = 0.0
    cost_output_per_mtok: float = 0.0
    context_length: int | None = None     # None → displayed as "Unknown"
    size_bytes: int | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    family: str | None = None
    families: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    modified_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str          # system | user | assistant | tool
    content: str
    # Tool-calling support (normalized OpenAI-style shape; providers
    # serialize to their native format):
    #   assistant → tool_calls it requested
    #   tool      → result addressing one call
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class StreamChunk:
    """One streamed piece of a chat completion."""
    content: str = ""
    done: bool = False
    # Final chunk carries EXACT token accounting when the runtime reports it:
    input_tokens: int | None = None
    output_tokens: int | None = None
    eval_duration_ns: int | None = None
    # Completed tool calls the model requested (normalized:
    # {"id", "type": "function", "function": {"name", "arguments": dict}})
    tool_calls: list[dict[str, Any]] | None = None

    @property
    def output_tps(self) -> float | None:
        if self.output_tokens and self.eval_duration_ns:
            return self.output_tokens / (self.eval_duration_ns / 1e9)
        return None


@dataclass
class ChatOptions:
    num_ctx: int | None = None
    temperature: float | None = None
    keep_alive: str | None = None
    # OpenAI-style tool schemas: [{"type": "function", "function": {...}}]
    tools: list[dict[str, Any]] | None = None
    # Structured output constraint (provider-native: Ollama "format" /
    # OpenRouter "response_format")
    format: dict[str, Any] | None = None
    max_tokens: int | None = None


class Provider(ABC):
    """Contract every runtime must fulfil."""

    name: str = "abstract"
    display_name: str = "Abstract"
    is_local: bool = True
    # Cost per million tokens. Local runtimes are €0.00 — future cloud
    # providers override this and are then subject to the CostGuard.
    cost_input_per_mtok: float = 0.0
    cost_output_per_mtok: float = 0.0
    # Management capabilities (routers hide/disallow what isn't supported)
    supports_pull: bool = False
    supports_delete: bool = False
    requires_api_key: bool = False

    @abstractmethod
    async def status(self) -> ProviderStatus: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    async def show_model(self, name: str) -> dict[str, Any]: ...

    async def enrich(self, info: ModelInfo) -> ModelInfo:
        """Best-effort fill of extra real metadata (context length,
        capabilities). Default: no-op (provider reports what it knows)."""
        return info

    @abstractmethod
    def chat_stream(self, model: str, messages: list[ChatMessage],
                    options: ChatOptions, cancel: asyncio.Event) -> AsyncIterator[StreamChunk]:
        """Yield StreamChunks; honour *cancel* by stopping promptly."""

    async def pull_model(self, name: str,
                         cancel: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError  # optional capability
        yield  # pragma: no cover

    async def delete_model(self, name: str) -> bool:
        raise NotImplementedError  # optional capability
