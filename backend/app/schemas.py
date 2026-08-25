"""API request schemas (pydantic)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = "New chat"
    model: str | None = None
    provider: str | None = None
    system_prompt: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    provider: str | None = None
    system_prompt: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    favorite: bool | None = None


class ChatCompletionRequest(BaseModel):
    conversation_id: str | None = None
    content: str = Field(min_length=1)
    model: str | None = None
    provider: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ChatStopRequest(BaseModel):
    request_id: str


class RegenerateRequest(BaseModel):
    message_id: str
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class SettingsUpdate(BaseModel):
    free_only: bool | None = None
    max_spend: float | None = Field(default=None, ge=0.0)
    default_model: str | None = None
    num_ctx: int | None = Field(default=None, ge=512)
    keep_alive: str | None = None
    custom_instructions: str | None = Field(default=None, max_length=8000)


class ModelTestRequest(BaseModel):
    provider: str = "ollama"
    name: str


class ModelPullRequest(BaseModel):
    name: str
    provider: str = "ollama"


class ModelFavoriteRequest(BaseModel):
    favorite: bool
