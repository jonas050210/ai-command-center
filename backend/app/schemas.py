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
    default_provider: str | None = None
    num_ctx: int | None = Field(default=None, ge=512)
    keep_alive: str | None = None
    custom_instructions: str | None = Field(default=None, max_length=8000)
    eur_per_usd: float | None = Field(default=None, ge=0.2, le=5.0)


class ProviderKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)


class SettingsUpdateExt(SettingsUpdate):
    cap_filesystem_read: bool | None = None
    cap_filesystem_write: bool | None = None
    cap_command_execute: bool | None = None
    cap_network_fetch: bool | None = None
    cap_git_operate: bool | None = None


class AgentRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    provider: str | None = None
    model: str | None = None
    skills: str | None = Field(default=None, max_length=20000)
    project_id: int | None = None


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=4000)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, pattern="^(active|archived)$")


class CompareRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    models: list[str] = Field(min_length=2, max_length=4)
    # each entry: "<provider>/<model>" or bare "<model>" (catalog-resolved)


class TeamMemberSpec(BaseModel):
    role: str = Field(pattern="^(planner|executor|reviewer)$")
    model: str = Field(min_length=1, max_length=200)
    provider: str | None = None
    responsibility: str = Field(default="", max_length=1000)


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    members: list[TeamMemberSpec] = Field(min_length=2, max_length=4)


class TeamRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)


class ResearchQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    model: str | None = None
    provider: str | None = None


class GitPathRequest(BaseModel):
    path: str | None = Field(default=".", max_length=300)


class GitInitRequest(BaseModel):
    path: str | None = Field(default=".", max_length=300)


class GitBranchCreateRequest(BaseModel):
    path: str | None = Field(default=".", max_length=300)
    name: str = Field(min_length=1, max_length=100)


class GitCommitRequest(BaseModel):
    path: str | None = Field(default=".", max_length=300)
    message: str = Field(min_length=1, max_length=500)
    files: list[str] | None = Field(default=None, max_length=200)


class GitPushRequest(BaseModel):
    path: str | None = Field(default=".", max_length=300)
    remote: str = Field(default="origin", max_length=60)
    set_upstream: bool = False


class GitRemoteAddRequest(BaseModel):
    path: str | None = Field(default=".", max_length=300)
    url: str = Field(min_length=1, max_length=300)
    remote: str = Field(default="origin", max_length=60)


class GithubTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=300)


class GithubRepoCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100,
                      pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    private: bool = True
    description: str = Field(default="", max_length=350)


class AgentStopRequest(BaseModel):
    run_id: str


class ApprovalDecisionRequest(BaseModel):
    approve: bool


class ModelTestRequest(BaseModel):
    provider: str = "ollama"
    name: str


class ModelPullRequest(BaseModel):
    name: str
    provider: str = "ollama"


class ModelFavoriteRequest(BaseModel):
    favorite: bool
