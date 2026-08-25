# AI Command Center — PROJECT.md

**The complete project document.** Everything about this codebase in one place:
what it is, what it does, how it is built, file by file, exactly how it was
verified, what it cost to build (€0 infra, local-first), and its honest limits.

- **Version:** 0.12.0 · **Status:** all roadmap phases P0–P10 complete
- **Tests:** 239/239 pytest passing · frontend `tsc --noEmit` clean ·
  production build clean · live end-to-end smokes 19/19 + 21/21 + 14/14
- **Repo:** `jonas050210/ai-command-center` · branch of record: this session's
  working branch · **Platform target:** Windows 11 (works on Linux/macOS)
- **Built for:** Intel i7-12700F · RTX 4060 Ti 8GB · 32GB RAM · Python 3.11 · Node 22

---

## 1. What this product is

AI Command Center is a **local-first AI workspace** — a desktop app and web UI
that turns one or more local (Ollama) and free-cloud (OpenRouter `:free`)
models into a complete working environment:

| Mode | One-line truth |
|---|---|
| **Chat** | Real SSE streaming chat with token accounting labeled exact/estimated |
| **Agent Mode** | Tool-calling agent with human approval for *every* write/exec action |
| **Projects** | Sandboxed per-project workspaces the agent can be scoped into |
| **Compare Mode** | One prompt → 2–4 models streamed side-by-side (VRAM-honest) |
| **Team Mode** | Planner→executor→reviewer pipelines of 2–4 models with verdicts |
| **Research Mode** | Web-grounded answers with `[n]` citations, SSRF-guarded fetch |
| **Git/GitHub** | Real git ops inside the sandbox + GitHub REST via vaulted PAT |
| **Memory & Skills** | Persistent memories + AGENT.md standing instructions |
| **Model Center** | Live catalog, speed tests, pull/delete, favorites, costs |
| **Desktop** | Windows EXE (PyInstaller) + Inno Setup installer + CI release |

**Hard product rules (never broken):**
1. **No fake features.** Every button is wired UI → API → service →
   DB/provider/tool → result → UI. Any boundary that isn't implemented answers
   honestly; nothing pretends.
2. **€0 cost protection** (`FREE_ONLY=true`, `MAX_SPEND=0.00` defaults) is
   enforced **server-side before any network call**; tested against bypasses.
3. **No silent provider switching.** Ollama and OpenRouter stay strictly
   separate; no cross-provider "fallback" that could spend money.
4. **Every dangerous model operation** passes capability policy → argument
   validation → human approval → sandbox → audit row. There is no
   unrestricted shell or filesystem path anywhere in the product.
5. **Honesty in UI:** unknown values render as *Unknown*, truncated content
   is marked truncated, queued work is shown as queued, denials are audited.

---

## 2. Proof of quality (exact verification results)

| Gate | Result |
|---|---|
| Backend pytest (unit + API + SSE flows, all networks faked) | **239 passed, 0 failed** |
| Frontend typecheck (`tsc --noEmit`, strict TS) | **clean** |
| Frontend production build (`vite build`) | **clean** (~615 kB JS, 188 kB gzip) |
| Live E2E P6 (server + mock Ollama over real HTTP/SSE) | **19/19 PASS** |
| Live E2E P7 (git ops against a real git repo remote) | **21/21 PASS** |
| Live E2E P8 (memory/agent approvals, mid-stream approval POST) | **14/14 PASS** |
| Desktop smoke (`main_desktop.py --smoke`) | **exit 0, backend healthy** |
| `test_overall.py` 4-suite runner | backend tests → tsc → build → e2e, all green |

Per-file test counts (239 total): `test_agent 26 · test_research 36 ·
test_openrouter 19 · test_guards 23 · test_git 20 · test_chat_api 13 ·
test_memory 11 · test_desktop 11 · test_providers 11 · test_models_api 9 ·
test_system_api 9 · test_security 8 · test_database 8 · test_team 7 ·
test_conversations_api 6 · test_cost_guard 6 · test_compare 5 ·
test_projects 5 · test_config 3 · test_tokens 3`.

---

## 3. Architecture

### 3.1 Layer cake (each layer only talks downward)

```
frontend (React 18 + strict TS + Vite + Tailwind v4)
        │  REST + SSE (fetch + ReadableStream)
routers (FastAPI, thin HTTP: validation, SSE envelopes, one error shape)
        │
services / engines (chat, agent, team, compare, research, gitops, memory,
        │            models, settings, tokens, context, projects)
providers (Ollama streaming/tool-calling · OpenRouter SSE · registry)
        │
db (aiosqlite, repository layer, V1–V5 migrations) + security
(crypto vault, capability policy, guards, rate limit) + workspace sandbox
```

Composition root: `backend/app/main.py` builds `Services` and injects it via
`routers/deps.py`. `backend/app/config.py` is the **single source of truth**
for `APP_VERSION`, `DEFAULT_MODEL_NAME`, and every env-backed setting
(real env > `.env` > defaults; frozen-app aware paths for the desktop build).

### 3.2 Data flow — any LLM call

request → **ModelRouter** (explicit provider › synced catalog › default
provider; *no cross-provider fallback*) → **CostGuard** (blocks
paid/budget-breakers *pre-network*) → provider stream → exact token
accounting → usage ledger → session metrics.

### 3.3 Tool flow — agent

model `tool_call` → **ToolExecutor gateway**
(1 capability check → 2 argument validation → 3 human approval for
write/exec tier, 10-minute validity, exact diff preview → 4 sandboxed
execution → 5 audit row — *always written, including denials*) → result back
to the model.

### 3.4 Security model (defense in depth)

- **Capability policy** (deny-by-default, 6 capabilities; toggles persisted in
  DB, enforced only in backend): `filesystem:read` ✓ · `filesystem:write` ✓ ·
  `command:execute` ✓ · `network:fetch` ✓ (defaults on) · `memory` ✓ (on) ·
  `git:operate` ✗ (**off by default**, opt-in).
- **Workspace path-containment sandbox**: every file path resolved and proven
  inside the workspace root; blocks `../`, `..\\`, UNC, symlink escapes.
- **Shell tool**: allow-listed executables (python/pytest/git/node/npm/ruff…),
  no chaining (`&&`, `;`, `|…` rejected), dangerous-argument scanner,
  output caps with honest truncation.
- **Web layer**: DuckDuckGo search + SSRF-guarded fetch — scheme/host checks,
  private/loopback/link-local ranges blocked, redirect chain re-validated,
  hard byte caps, pages marked `truncated` when capped.
- **Credentials vault**: Fernet-encrypted at rest (key in `DATA_DIR/secret.key`,
  never in repo); PAT usable for git only via `GIT_ASKPASS` (never in argv,
  never logged, delivered only to `github.com`).
- **HTTP hardening**: security headers, host/origin guard, optional API-token
  middleware, per-route rate limits, JSON logs with rotation + secret
  redaction.

### 3.5 Providers

- **Ollama** (`providers/ollama.py`): `/api/show` (context length,
  capabilities), `/api/chat` NDJSON streaming incl. **tool_calls**,
  `/api/tags`, pull (progress SSE), delete, measured speed tests.
- **OpenRouter** (`providers/openrouter.py`): OpenAI-compatible streaming SSE
  with `usage`, live catalog filtered to `:free` models under FREE_ONLY, key
  validation, vault-stored key, real error surfacing (never silent).
- **Registry**: providers are independent plugins; adding a paid provider
  would immediately fall under CostGuard — by design.

---

## 4. Complete file inventory (every file, line counts, purpose)

Total tracked source: **~25,700 lines** (code + docs + config).
`__pycache__`, `node_modules`, `dist`, `.ruff_cache`, `.git` excluded.

### 4.1 Root

| Lines | File | Purpose |
|---|---|---|
| 17 | `main.py` | Entry point: uvicorn against `create_app()` |
| 68 | `start.py` | Cross-platform launcher (prefers `.venv`, sets env) |
| 94 | `setup.py` | Idempotent first-run setup (venv, deps, frontend build, `.env`) |
| 312 | `test_overall.py` | The 4-suite system gate: pytest → tsc → vite build → e2e |
| 138 | `README.md` | User-facing overview + quick start + config reference |
| — | `PROJECT.md` | This document |
| — | `ROADMAP` | Phase history P1–P10, all DONE |
| — | `requirements.txt` | Backend deps (fastapi 0.115.12, uvicorn[standard] 0.34.2, pydantic 2.11.4, pydantic-settings 2.9.1, httpx 0.28.1, aiosqlite 0.21.0, cryptography 44.0.2, ddgs 9.15.0, trafilatura 2.2.0, lxml 6.1.2, lxml_html_clean 0.4.5, pytest 8.3.5, pytest-asyncio 0.26.0) |
| — | `requirements-desktop.txt` | pyinstaller 6.11.1 · pywebview 5.4 |
| — | `pytest.ini` | `asyncio_mode=auto`, test discovery |
| — | `.env.example` | Every configurable variable with defaults |
| — | `.gitignore` | venv/data/dist/builds/logs/node_modules |

### 4.2 Backend — app core

| Lines | File | Purpose |
|---|---|---|
| 193 | `backend/app/config.py` | Settings (env/.env), `APP_VERSION`, frozen-app path resolution (`is_frozen`, `bundle_root`, `default_data_dir`) |
| 258 | `backend/app/main.py` | App factory + composition root, middleware wiring, SPA static mount |
| 101 | `backend/app/core/errors.py` | `AppError` → one JSON error shape for every endpoint |
| 81 | `backend/app/db/database.py` | aiosqlite connection management, WAL, migration runner |
| 317 | `backend/app/db/migrations.py` | Migrations V1–V5 (V5 = `memories` table) |
| 645 | `backend/app/db/repo.py` | Repository layer: conversations/messages/models/usage/executions/projects/research/teams/runs/approvals **and** MemoriesRepo |

### 4.3 Backend — providers

| Lines | File | Purpose |
|---|---|---|
| 133 | `backend/app/providers/base.py` | Provider ABC: stream tool-capable chat, catalog, errors |
| 301 | `backend/app/providers/ollama.py` | Ollama: NDJSON streaming, tool_calls, show/tags/pull/delete, speed test |
| 403 | `backend/app/providers/openrouter.py` | OpenRouter: OpenAI-SSE streaming w/ usage, catalog, key validation |
| 33 | `backend/app/providers/registry.py` | Named provider instances, capability listing |

### 4.4 Backend — services

| Lines | File | Purpose |
|---|---|---|
| 92 | `backend/app/services/model_router.py` | Explicit provider › catalog › default; **no cross-provider fallback** |
| 78 | `backend/app/services/cost_guard.py` | €0 CostGuard — blocks paid/budget-breakers **before network**; bypass-tested |
| 278 | `backend/app/services/chat_service.py` | Chat orchestration, SSE envelopes, stop/regenerate, auto-titles |
| 97 | `backend/app/services/models_service.py` | Live catalog sync, categories, recents, favorites, speed tests |
| 29 | `backend/app/services/tokens.py` | Exact vs estimated token accounting helpers |
| 102 | `backend/app/services/settings_service.py` | Runtime settings (type-checked, persisted, capability map) |
| 121 | `backend/app/services/credentials_service.py` | Vault CRUD incl. non-provider scopes (github); provider key loading |
| 117 | `backend/app/services/context.py` | Context-window management + honest compaction notes |
| 130 | `backend/app/services/project_service.py` | Project lifecycle: slug-dedup, path-proven dirs, archive-only |
| 174 | `backend/app/services/compare_service.py` | Compare runs: per-provider VRAM serialization, parallel clouds |

### 4.5 Backend — agent / team / research / gitops / memory

| Lines | File | Purpose |
|---|---|---|
| 447 | `backend/app/agent/engine.py` | Tool-calling engine: max-steps, circuit breaker, stop, skills+memory prompt injection |
| 51 | `backend/app/agent/prompts.py` | Agent system prompt (capabilities, honesty, verdict rules) |
| 349 | `backend/app/team/service.py` | Planner→executor(re-al agent runs)→reviewer pipelines, VERDICT parsing, 1 revision max |
| 49 | `backend/app/team/prompts.py` | Planner/executor/reviewer prompt templates |
| 223 | `backend/app/research/service.py` | Research runs: search→fetch→grounded answer w/ `[n]` citations, history |
| 194 | `backend/app/research/web.py` | Web edge: DDG search + SSRF-guarded fetch + extraction + size caps |
| 405 | `backend/app/gitops/service.py` | Git argv-subprocess service: status/diff/log/branch/commit/push/remote, containment + audit |
| 87 | `backend/app/gitops/github.py` | GitHub REST: user/repos/create-private; honest 401 |
| 111 | `backend/app/memory/service.py` | Memory CRUD, AGENT.md read, skills chain ws→project, prompt blocks |

### 4.6 Backend — tools / security / workspace / observability

| Lines | File | Purpose |
|---|---|---|
| 128 | `backend/app/tools/registry.py` | Tool specs (name, tier, capability, JSON-schema args) — 10 builtin tools |
| 437 | `backend/app/tools/builtin.py` | `fs_list/read/write/edit`, `shell_run`, `web_search/fetch`, `memory_search/save/forget` |
| 107 | `backend/app/tools/executor.py` | The gateway: capability → validation → approval → sandbox → audit |
| 11 | `backend/app/tools/audit.py` | Audit-row writer (executions) |
| 55 | `backend/app/security/crypto.py` | Fernet vault helpers |
| 53 | `backend/app/security/permissions.py` | Capability enum + default policy |
| 235 | `backend/app/security/guards.py` | Security headers, host/origin guard, API-token middleware |
| 114 | `backend/app/security/ratelimit.py` | Sliding-window rate limiter per route class |
| 35 | `backend/app/workspace/paths.py` | Path-containment resolution (the sandbox root) |
| 71 | `backend/app/observability/logging.py` | JSON logs, rotation, secret redaction |
| 28 | `backend/app/observability/metrics.py` | Session metrics (uptime, requests, blocked-paid counter) |

### 4.7 Backend — HTTP routers (thin)

| Lines | File | Endpoints |
|---|---|---|
| 32 | `routers/health.py` | `GET /api/health` |
| 63 | `routers/system.py` | `/api/system/status`, metrics |
| 30 | `routers/settings.py` | `GET|PUT /api/settings` |
| 49 | `routers/costs.py` | `/api/costs`, `/api/usage/tokens` |
| 78 | `routers/providers.py` | `/api/providers`, key set/delete |
| 174 | `routers/models.py` | catalog, refresh, test, pull (SSE), favorites, delete |
| 86 | `routers/conversations.py` | CRUD, pin/star/archive, message listing |
| 84 | `routers/chat.py` | completions / regenerate / stop (SSE) |
| 117 | `routers/agent.py` | runs (SSE), stop, history, approvals answer, capabilities, tools, executions |
| 50 | `routers/projects.py` | CRUD + archive |
| 39 | `routers/compare.py` | runs (SSE) |
| 105 | `routers/team.py` | teams CRUD + runs (SSE) + stop |
| 86 | `routers/research.py` | query (SSE), history, detail, stop |
| 128 | `routers/git.py` | status/log/diff/branches/init/commit/push/remote + GitHub token/user/repos |
| 84 | `routers/memory.py` | memories list/save/search/delete, AGENT.md file GET|PUT, context |

### 4.8 Frontend (React 18 + strict TS + Vite + Tailwind v4)

| Lines | File | Purpose |
|---|---|---|
| 13 | `src/main.tsx` | Bootstrap |
| 56 | `src/App.tsx` | Shell: header + 3 panels + overlays + `useGlobalShortcuts()` |
| 285 | `src/store.tsx` | Single context store (settings/system/costs/tokens/models/providers/conversations, panels, palette/help, toasts) |
| 102 | `src/api.ts` | `getJSON/sendJSON/streamSSE` + `ApiError` (code+message) |
| 391 | `src/types.ts` | All API types (mirrors backend schemas) |
| 115 | `src/utils.ts` | formatters, **one** timestamp parser (`parseTs`) reused by timeAgo/dayBucket/formatClock/dayLabel/sameDay, clipboard w/ fallback |
| 353 | `src/styles.css` | Theme tokens, glass, buttons, chips, markdown, hljs, palette/kbd/day-sep, reduced-motion |
| 58 | `src/icons.tsx` | Inline stroke icon set (currentColor) |
| 95 | `components/Header.tsx` | Brand, palette trigger pill, Ollama/provider/€0/session chips |
| 219 | `components/LeftSidebar.tsx` | Nav + chats: search, pin/star/rename/archive/delete, **date grouping** |
| 290 | `components/ChatView.tsx` | Chat workspace: SSE streaming, banners, **day separators**, **jump-to-latest** |
| 155 | `components/Composer.tsx` | Model selector, autosize textarea, pre-send token estimate, send/stop, suggestions |
| 124 | `components/MessageItem.tsx` | Bubbles, markdown, token chips, copy/regenerate, **timestamps** |
| 80 | `components/Markdown.tsx` | GFM markdown + code blocks w/ copy (highlight.js) |
| 206 | `components/RightInspector.tsx` | Model/Context/Tokens/Cost/Status/**Shortcuts** cards |
| 329 | `components/CommandPalette.tsx` | **Ctrl+K palette** (fuzzy-ranked actions/chats/models), **Ctrl+/ help**, global shortcut hook |
| 412 | `components/SettingsDrawer.tsx` | All runtime settings incl. capability toggles + Memory manager |
| 360 | `components/ModelCenter.tsx` | Catalog: search/filter/sort, speed test, pull (SSE progress), delete |
| 467 | `components/AgentView.tsx` | Agent console: runs SSE, approval cards w/ diffs, steps, tools, context chip |
| 248 | `components/CompareView.tsx` | 2–4 slots side-by-side, queued/parallel honesty, usage per slot |
| 529 | `components/TeamView.tsx` | Team builder + pipeline view (plan/review/verdicts), per-model tokens |
| 333 | `components/ResearchView.tsx` | Query, live steps, `[n]`-cited answer, source cards, history |
| 216 | `components/ProjectsView.tsx` | Project CRUD + archive, workspace path display |
| 455 | `components/GitView.tsx` | Status/diff (DiffBlock)/branches/commit/push/remote/GitHub card |
| 127 | `components/agentUi.tsx` | Shared run widgets (steps, approvals, DiffBlock) |
| 14 | `index.html` | SPA shell (dark class, fonts) |
| 20 | `vite.config.ts` | Dev proxy `/api` → backend, host-allow for preview env |

### 4.9 Desktop distribution

| Lines | File | Purpose |
|---|---|---|
| 93 | `desktop/main_desktop.py` | Desktop launcher: free port → in-process uvicorn → pywebview window (browser fallback); `--smoke` = deterministic health check (exit 0 = OK) |
| 61 | `desktop/build.py` | Build driver: npm ci+build (if stale) → PyInstaller onedir |
| — | `desktop/aicc_desktop.spec` | PyInstaller spec: onedir, SPA in `datas`, hidden imports pinned |
| — | `desktop/installer.iss` | Inno Setup: per-user install, modern wizard, shortcuts, versioned filename |
| — | `.github/workflows/release.yml` | Tag `v*` → Windows build: **pytest gates** → desktop build → frozen smoke → ISCC → artifacts + GitHub release |
| — | `.github/workflows/ci.yml` | Push CI: pytest, pip-audit, tsc, vite build, npm audit, test_overall |

### 4.10 Tests (239 pytest; live E2E harness beside repo in /tmp)

| Lines | File | Focus |
|---|---|---|
| 214 | `tests/conftest.py` | Fixtures: in-memory app, faked providers, `tools_env` |
| 508 | `tests/test_agent.py` | Engine, approvals, circuit breaker, tools registry, capabilities |
| 433 | `tests/test_research.py` | Web edge (SSRF, caps, extraction), grounded answers, SSE run |
| 398 | `tests/test_openrouter.py` | Streaming, catalog, key flows, CostGuard interplay |
| 322 | `tests/test_git.py` | Git ops, containment, remote policy, token hygiene, GitHub client |
| 296 | `tests/test_guards.py` | Headers, host/origin, API token, rate limits |
| 210 | `tests/test_team.py` | Pipelines, verdicts, revision cap, VRAM serialization |
| 193 | `tests/test_chat_api.py` | Chat SSE, regenerate/stop, titles, errors |
| 185 | `tests/test_providers.py` | Ollama + registry behavior |
| 181 | `tests/test_memory.py` | Memory CRUD, tools gating, AGENT.md chain, prompt injection |
| 130 | `tests/test_projects.py` | Project lifecycle + path proofs |
| 120 | `tests/test_database.py` | Migrations, repos, ledger |
| 116 | `tests/test_desktop.py` | Frozen paths, portable fallback, ports, packaging sanity |
| 105 | `tests/test_models_api.py` | Catalog/refresh/test/pull/delete endpoints |
| 89 | `tests/test_compare.py` | Compare service + SSE |
| 88 | `tests/test_cost_guard.py` | €0 enforcement + bypass attempts |
| 81 | `tests/test_security.py` | Path sandbox, crypto vault |
| 81 | `tests/test_system_api.py` | Status/health/metrics |
| 76 | `tests/test_conversations_api.py` | Conversation CRUD endpoints |
| 47 | `tests/test_tokens.py` | Exact/estimated accounting |
| 36 | `tests/test_config.py` | Settings resolution + frozen helpers |

Live E2E (real HTTP/SSE against mock Ollama + this server):
P6 19 checks · P7 21 checks (real `git` binary + local bare remote) ·
P8 14 checks (incl. a mid-stream approval POST during an SSE run).

---

## 5. Database (SQLite, WAL, migration-managed V1–V5)

20 tables: `conversations · messages · models · providers · settings ·
credentials · usage_events · executions · projects · files · tasks ·
research · teams · team_members · team_runs · agent_runs · agent_steps ·
approvals · memories · schema_migrations`.

Rule: schema evolves **only** through new migrations. V5 added `memories`.
All writes go through the repository layer (`db/repo.py`); no raw SQL in
routers/services.

---

## 6. API summary (all real; one error shape)

`GET /api/health` · `GET /api/system/status` · `GET|PUT /api/settings` ·
`GET /api/costs` · `GET /api/usage/tokens` · `GET /api/providers`
(+ POST/DELETE key) · `GET /api/models` (+filters) ·
`POST /api/models/refresh|test|pull(SSE)` · favorites/delete ·
`GET|POST|PATCH|DELETE /api/conversations[/{id}]` ·
`POST /api/chat/completions|regenerate|stop (SSE)` ·
`POST /api/agent/runs (SSE)` + stop/history/approvals/capabilities/tools/executions ·
`GET|POST /api/projects` (+archive) · `POST /api/compare/runs (SSE)` ·
`GET|POST /api/team` + runs (SSE)/stop · `POST /api/research/query (SSE)` +
history/detail/stop · `GET /api/git/status|log|diff|branches` ·
`POST /api/git/init|branches|commit|push|remote` ·
`/api/git/github/token|user|repos` · `GET|POST|DELETE /api/memory` ·
`GET|PUT /api/memory/file` · `GET /api/memory/context`.
Interactive docs: `/api/docs`.

---

## 7. Build history (what was delivered, phase by phase)

| Phase | Version | Delivered |
|---|---|---|
| Foundation | v0.3.0 | FastAPI + SQLite + migrations + modular boundaries + JSON logs + vault |
| Premium chat | v0.3.0 | Real SSE chat, full history UX, markdown/GFM/highlight, tokens, context meter |
| Model Center + CostGuard | v0.3.0 | Live catalog, speed tests, pull/delete, per-model ledgers, strict €0 |
| P0–P6 | v0.8.0 | Hardening pass, **OpenRouter provider**, **context compaction**, **Agent Mode** (tools+approvals+audit), **Projects**, **Compare**, **Team**, **Research** |
| P7 | v0.9.0 | **Git/GitHub** (sandboxed git + vaulted PAT + audit); deleted the old placeholder views |
| P8 | v0.10.0 | **Memory & skills** (V5 migration, memory tools, AGENT.md chaining, Memory UI) |
| P9 | v0.11.0 | **Windows distribution** (PyInstaller onedir, Inno installer, release CI, frozen-aware config, `--smoke`) |
| P10 | v0.12.0 | **Final polish**: command palette (Ctrl+K), shortcuts system, date-grouped sidebar, chat day separators + jump-to-latest, timestamps, reduced-motion/focus a11y, PROJECT.md, docs refresh |

Commits on this branch (newest first): `38fd717` P10 GUI+docs ·
`b544855` P9 desktop · `f542633` P8 memory · `23fcbcf` P7 git ·
`4099abd` P0–P6 · `f7136b8` v0.3.0 baseline.

---

## 8. How to run everything

```bash
python setup.py                 # one-time: venv + deps + frontend build + .env
ollama pull qwen3:0.6b          # the default model (single source: config.py)
python start.py                 # http://127.0.0.1:8000

python test_overall.py          # full 4-suite gate
python desktop/build.py         # Windows: dist-desktop/AICommandCenter/…exe
iscc desktop/installer.iss      # Windows: dist-installer/AICommandCenterSetup-0.12.0.exe
```

Keyboard: **Ctrl+K** palette · **Ctrl+B** sidebar · **Ctrl+.** inspector ·
**Ctrl+,** settings · **Ctrl+Alt+N** new chat · **Ctrl+/** help.

---

## 9. Genuine limitations (honest list — nothing hidden)

1. **Research browse needs real outbound network.** In sandboxed/offline
   environments, fetches fail and the run says so (fails closed, never
   hallucinates).
2. **Desktop EXE is built by CI/locally on Windows** — this dev sandbox can't
   produce Windows binaries; the pipeline + spec + smoke test are ready and
   tested from source (`--smoke` verified).
3. **pywebview** needs a system WebView2 runtime on Windows (present on
   Win11); without it the app falls back to the default browser.
4. **Team/Compare with several 7B+ local models** is VRAM-bound by design —
   locals serialize per provider; the UI shows queued honestly.
5. **No multi-user/auth** — it is a single-user, local-first app; the optional
   `API_TOKEN` hardens shared-LAN exposure only.
6. **MCP / plugin market** — deliberately deferred: current 10 builtin tools
   cover the roadmap scope; adding external tool servers would need a new
   trust/audit design before it belongs in this product.

---

## 10. Attribution

Original code and design. The interface takes only broad UX inspiration from
modern AI products — no branding, assets, or designs were copied. All HTTP
traffic to model providers happens only after the backend CostGuard approves
it; by default that means €0.00 spent, forever.
