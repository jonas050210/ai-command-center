"""API request schemas (pydantic)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = "New chat"
    model: str | None = None
    provider: str | None = None
    system_prompt: str | None = None
    project_id: int | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = None
    provider: str | None = None
    system_prompt: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    favorite: bool | None = None
    project_id: int | None = None


class ChatCompletionRequest(BaseModel):
    conversation_id: str | None = None
    content: str = Field(min_length=1)
    model: str | None = None
    provider: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    project_id: int | None = None


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
    agent_max_steps: int | None = Field(default=None, ge=3, le=100)
    agent_max_fix_rounds: int | None = Field(default=None, ge=0, le=10)
    agent_cmd_timeout: float | None = Field(default=None, ge=5, le=300)
    team_max_rounds: int | None = Field(default=None, ge=0, le=6)
    search_engine: str | None = Field(default=None, max_length=40)
    research_max_sources: int | None = Field(default=None, ge=1, le=8)


class ModelTestRequest(BaseModel):
    provider: str = "ollama"
    name: str


class ModelPullRequest(BaseModel):
    name: str
    provider: str = "ollama"


class ModelFavoriteRequest(BaseModel):
    favorite: bool


# ── Agent / Team / Compare / Research / Projects / Git ───────────────
class AgentRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    project_id: int | None = None
    provider: str | None = None
    model: str | None = None


class TeamRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=12000)
    models: list[str] = Field(min_length=2, max_length=4)
    provider: str | None = None
    project_id: int | None = None
    roles: dict[str, str] | None = None


class TeamTaskPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(todo|in_progress|review|done)$")
    assignee: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)


class CompareRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    models: list[str] = Field(min_length=1, max_length=6)
    provider: str | None = None
    project_id: int | None = None


class CompareSelectRequest(BaseModel):
    answer_id: int


class ResearchRunRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    project_id: int | None = None
    synthesize: bool = True
    provider: str | None = None
    model: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = None


class ProjectTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=4, max_length=500)
    paths: list[str] | None = None


class GithubTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)
