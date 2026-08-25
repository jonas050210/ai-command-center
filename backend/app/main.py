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

from ..app.config import Settings, get_settings
from ..app.core.errors import register_exception_handlers
from ..app.db.database import Database
from ..app.db.migrations import migrate
from ..app.db.repo import (AgentRunsRepo, ApprovalsRepo, ConversationsRepo,
                           CredentialsRepo, ExecutionsRepo, MessagesRepo,
                           ModelsRepo, ProjectsRepo, ProvidersRepo,
                           ResearchRepo, SettingsRepo,
                           TeamRunsRepo, TeamsRepo, UsageRepo)
from ..app.observability.logging import setup_logging
from ..app.observability.metrics import metrics
from ..app.providers.ollama import OllamaProvider
from ..app.providers.openrouter import OpenRouterProvider
from ..app.providers.registry import ProviderRegistry
from ..app.agent.engine import AgentEngine, RunManager
from ..app.research.service import ResearchRunManager, ResearchService
from ..app.security.crypto import CredentialVault
from ..app.security.guards import (ApiTokenManager, ApiTokenMiddleware,
                                   HostOriginGuardMiddleware,
                                   SecurityHeadersMiddleware)
from ..app.security.ratelimit import RateLimitMiddleware
from ..app.gitops.service import GitService
from ..app.services.chat_service import ChatService, RequestManager
from ..app.services.compare_service import CompareService
from ..app.services.cost_guard import CostGuard
from ..app.services.credentials_service import CredentialsService
from ..app.services.model_router import ModelRouter
from ..app.services.models_service import ModelsService
from ..app.services.project_service import ProjectService
from ..app.services.settings_service import SettingsService
from ..app.team.service import TeamRunManager, TeamService
from ..app.tools.builtin import register_builtin_tools
from ..app.tools.executor import ToolExecutor
from ..app.tools.registry import ToolRegistry

log = logging.getLogger("aicc.app")


@dataclass
class Services:
    """Composition root — every dependency, in one place."""

    settings: Settings
    db: Database
    vault: CredentialVault
    credentials_service: CredentialsService
    settings_repo: SettingsRepo
    providers_repo: ProvidersRepo
    models_repo: ModelsRepo
    conversations_repo: ConversationsRepo
    messages_repo: MessagesRepo
    usage_repo: UsageRepo
    executions_repo: ExecutionsRepo
    projects_repo: ProjectsRepo
    teams_repo: TeamsRepo
    settings_service: SettingsService
    providers_registry: ProviderRegistry
    router: ModelRouter
    guard: CostGuard
    chat: ChatService
    models_service: ModelsService
    requests: RequestManager
    api_tokens: ApiTokenManager
    tools: ToolRegistry
    executor: ToolExecutor
    agent: AgentEngine
    projects: ProjectService
    compare: CompareService
    team: TeamService
    research: ResearchService
    git: GitService


def build_services(settings: Settings) -> Services:
    db = Database(settings.db_path)
    settings_repo = SettingsRepo(db)
    providers_repo = ProvidersRepo(db)
    models_repo = ModelsRepo(db)
    conversations_repo = ConversationsRepo(db)
    messages_repo = MessagesRepo(db)
    usage_repo = UsageRepo(db)
    executions_repo = ExecutionsRepo(db)
    settings_service = SettingsService(settings_repo, settings)

    registry = ProviderRegistry()
    registry.register(OllamaProvider(settings.ollama_host, timeout=settings.ollama_timeout))
    # OpenRouter is registered from boot but stays "unavailable" until a key
    # is stored through the vault-backed credentials service.
    registry.register(OpenRouterProvider(base_url=settings.openrouter_base_url,
                                         timeout=settings.ollama_timeout,
                                         eur_per_usd=settings.eur_per_usd))

    vault = CredentialVault(settings)
    credentials_service = CredentialsService(CredentialsRepo(db), vault, registry)

    router = ModelRouter(registry, models_repo, settings_service)
    guard = CostGuard(settings_service)
    requests = RequestManager()
    chat = ChatService(conversations=conversations_repo, messages=messages_repo,
                       usage=usage_repo, models=models_repo, router=router,
                       guard=guard, settings=settings_service, requests=requests)
    models_service = ModelsService(models_repo, providers_repo, settings_service)
    projects = ProjectService(ProjectsRepo(db), settings.resolved_workspace_root)
    compare = CompareService(router=router, guard=guard, usage=usage_repo,
                             settings=settings_service)

    # ── agent mode: tool registry → gateway executor → engine ──
    tools = ToolRegistry()
    register_builtin_tools(tools)
    executor = ToolExecutor(tools, executions_repo)
    runs_manager = RunManager()
    agent = AgentEngine(
        runs=AgentRunsRepo(db), approvals=ApprovalsRepo(db), usage=usage_repo,
        registry=registry, router=router, guard=guard,
        settings=settings_service, tools=tools, executor=executor,
        runs_manager=runs_manager,
        workspace_root=settings.resolved_workspace_root,
        projects=projects)
    team = TeamService(
        teams=TeamsRepo(db), team_runs=TeamRunsRepo(db),
        agent_runs=AgentRunsRepo(db), usage=usage_repo,
        registry=registry, router=router, guard=guard,
        settings=settings_service, agent_engine=agent,
        run_manager=TeamRunManager())
    research = ResearchService(
        repo=ResearchRepo(db), usage=usage_repo, router=router, guard=guard,
        settings=settings_service, run_manager=ResearchRunManager())
    git = GitService(executions=executions_repo,
                     workspace_root=settings.resolved_workspace_root,
                     data_dir=settings.data_dir, settings=settings_service)

    return Services(
        settings=settings, db=db, vault=vault,
        credentials_service=credentials_service,
        tools=tools, executor=executor, agent=agent,
        projects=projects, compare=compare, team=team, research=research,
        git=git,
        settings_repo=settings_repo, providers_repo=providers_repo,
        models_repo=models_repo, conversations_repo=conversations_repo,
        messages_repo=messages_repo, usage_repo=usage_repo,
        executions_repo=executions_repo, projects_repo=ProjectsRepo(db),
        teams_repo=TeamsRepo(db), settings_service=settings_service,
        providers_registry=registry, router=router, guard=guard, chat=chat,
        models_service=models_service, requests=requests,
        api_tokens=ApiTokenManager(settings))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()          # workspace root etc. must exist (idempotent)
    setup_logging(settings.log_level, settings.log_dir,
                  settings.log_max_bytes, settings.log_backups)
    services = build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await services.db.connect()
        applied = await migrate(services.db)
        if applied:
            log.info("database migrations applied: %s", applied)
        await services.credentials_service.load_into_providers()
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
        allow_headers=["*", "Authorization", "X-API-Key"],
    )
    # Hardening guards — added after CORS so they run first (outermost last).
    # Order of execution: SecurityHeaders → RateLimit → HostOrigin → ApiToken → CORS.
    app.add_middleware(ApiTokenMiddleware, settings=settings, tokens=services.api_tokens)
    app.add_middleware(HostOriginGuardMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        metrics.http_requests += 1
        return await call_next(request)

    from ..app.routers import (agent as agent_router, chat, compare, conversations,
                               costs, git, health, models, projects, providers,
                               research, settings as settings_router, system, team)

    app.include_router(health.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(costs.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")
    app.include_router(team.router, prefix="/api")
    app.include_router(research.router, prefix="/api")
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
                "detail": "Frontend not built yet — run `python setup.py` "
                          "(which builds frontend/) and restart.",
                "api_docs": "/api/docs",
            })

    return app
