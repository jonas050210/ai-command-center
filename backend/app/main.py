"""FastAPI application factory.

Wires configuration → database (+migrations) → repositories →
providers → services → routers, and serves the built frontend as an
SPA (same origin in production — no CORS surface).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..app.agent import AgentEngine
from ..app.config import Settings, get_settings
from ..app.core.errors import register_exception_handlers
from ..app.db.database import Database
from ..app.db.migrations import migrate
from ..app.db.repo import (AgentRepo, CompareRepo, ConversationsRepo,
                           CredentialsRepo, ExecutionsRepo, MessagesRepo,
                           ModelsRepo, ProjectsRepo, ProvidersRepo, ResearchRepo,
                           SettingsRepo, TeamsRepo, UsageRepo)
from ..app.gitops import GitService, GithubClient
from ..app.observability.logging import setup_logging
from ..app.observability.metrics import metrics
from ..app.providers.ollama import OllamaProvider
from ..app.providers.registry import ProviderRegistry
from ..app.research import ResearchEngine
from ..app.security.crypto import CredentialVault
from ..app.services.chat_service import ChatService, RequestManager
from ..app.services.compare import CompareService
from ..app.services.cost_guard import CostGuard
from ..app.services.model_router import ModelRouter
from ..app.services.model_runner import ModelRunner
from ..app.services.models_service import ModelsService
from ..app.services.settings_service import SettingsService
from ..app.team import TeamEngine

log = logging.getLogger("aicc.app")


@dataclass
class Services:
    """Composition root — every dependency, in one place."""

    settings: Settings
    db: Database
    vault: CredentialVault
    settings_repo: SettingsRepo
    credentials_repo: CredentialsRepo
    providers_repo: ProvidersRepo
    models_repo: ModelsRepo
    conversations_repo: ConversationsRepo
    messages_repo: MessagesRepo
    usage_repo: UsageRepo
    executions_repo: ExecutionsRepo
    projects_repo: ProjectsRepo
    teams_repo: TeamsRepo
    agent_repo: AgentRepo
    research_repo: ResearchRepo
    compare_repo: CompareRepo
    settings_service: SettingsService
    providers_registry: ProviderRegistry
    router: ModelRouter
    guard: CostGuard
    model_runner: ModelRunner
    chat: ChatService
    models_service: ModelsService
    requests: RequestManager
    agent: AgentEngine
    team: TeamEngine
    compare: CompareService
    research: ResearchEngine
    git: GitService
    github: GithubClient


def build_services(settings: Settings) -> Services:
    db = Database(settings.db_path)
    settings_repo = SettingsRepo(db)
    credentials_repo = CredentialsRepo(db)
    providers_repo = ProvidersRepo(db)
    models_repo = ModelsRepo(db)
    conversations_repo = ConversationsRepo(db)
    messages_repo = MessagesRepo(db)
    usage_repo = UsageRepo(db)
    executions_repo = ExecutionsRepo(db)
    settings_service = SettingsService(settings_repo, settings)

    registry = ProviderRegistry()
    registry.register(OllamaProvider(settings.ollama_host, timeout=settings.ollama_timeout))

    router = ModelRouter(registry, models_repo, settings_service)
    guard = CostGuard(settings_service)
    requests = RequestManager()
    model_runner = ModelRunner(router, guard, models_repo, usage_repo,
                               settings_service)
    chat = ChatService(conversations=conversations_repo, messages=messages_repo,
                       usage=usage_repo, models=models_repo, router=router,
                       guard=guard, settings=settings_service, requests=requests,
                       runner=model_runner)
    models_service = ModelsService(models_repo, providers_repo, runner=model_runner)

    vault = CredentialVault(settings)
    projects_repo = ProjectsRepo(db)
    teams_repo = TeamsRepo(db)
    agent_repo = AgentRepo(db)
    research_repo = ResearchRepo(db)
    compare_repo = CompareRepo(db)

    agent = AgentEngine(runner=model_runner, settings=settings_service,
                        agents=agent_repo, usage=usage_repo,
                        workspace_root=settings.resolved_workspace_root,
                        executions_repo=executions_repo)
    team = TeamEngine(runner=model_runner, settings=settings_service,
                      teams=teams_repo, models=models_repo, usage=usage_repo)
    compare = CompareService(runner=model_runner, compare=compare_repo,
                             settings=settings_service)
    research = ResearchEngine(research=research_repo, runner=model_runner,
                              settings=settings_service)

    return Services(
        settings=settings, db=db, vault=vault,
        settings_repo=settings_repo, credentials_repo=credentials_repo,
        providers_repo=providers_repo, models_repo=models_repo,
        conversations_repo=conversations_repo, messages_repo=messages_repo,
        usage_repo=usage_repo, executions_repo=executions_repo,
        projects_repo=projects_repo, teams_repo=teams_repo,
        agent_repo=agent_repo, research_repo=research_repo,
        compare_repo=compare_repo,
        settings_service=settings_service, providers_registry=registry,
        router=router, guard=guard, model_runner=model_runner, chat=chat,
        models_service=models_service, requests=requests, agent=agent,
        team=team, compare=compare, research=research,
        git=GitService(workspace_root=settings.resolved_workspace_root,
                       projects=projects_repo, executions=executions_repo),
        github=GithubClient(vault, credentials_repo))


def create_app(settings: Settings | None = None, *, seed: bool = False) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    services = build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await services.db.connect()
        applied = await migrate(services.db)
        if applied:
            log.info("database migrations applied: %s", applied)
        log.info("%s v%s ready — Ollama host: %s — default model: %s — FREE_ONLY: %s",
                 settings.app_name, settings.version, settings.ollama_host,
                 settings.default_model, settings.free_only)
        yield
        await services.db.close()

    app = FastAPI(title=settings.app_name, version=settings.version,
                  docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    app.state.services = services

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        metrics.http_requests += 1
        return await call_next(request)

    from ..app.routers import (agent, chat, compare, conversations, costs, git,
                               health, messages, models, projects, providers,
                               research, settings as settings_router, system,
                               team)

    app.include_router(health.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(costs.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(messages.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(team.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")
    app.include_router(research.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(git.router, prefix="/api")

    # ── frontend (production build) with SPA fallback ────────────────
    dist = settings.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"error": {"code": "NOT_FOUND",
                                               "message": "Unknown API route.",
                                               "details": {}}}, status_code=404)
            if path and (dist / path).is_file():
                return FileResponse(dist / path)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({
                "app": settings.app_name, "version": settings.version,
                "detail": "Frontend not built yet — run `python start.py` "
                          "(which builds frontend/) and restart.",
                "api_docs": "/api/docs",
            })

    return app
