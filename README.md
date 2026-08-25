# AI Command Center

**Local-first AI workspace** — premium chat + Ollama runtime + OpenRouter (free-tier) providers, Agent Mode with human-gated tools, **Coder Mode** (project workspace + file tree), Projects, Compare, Team and Research Mode — with strict **€0 cost protection** enforced in the backend before any provider request is ever made.

Built for: Windows 11 · Intel i7-12700F · RTX 4060 Ti 8GB · 32GB RAM · Python 3.11.9 · Node.js 22 (works on Linux/macOS too).

---

## What works today (Phases 0–8, fully implemented, tests passing)

| Area | Capabilities |
|---|---|
| **Chat** | New conversation, history (date-grouped + pinned), search, rename, delete, archive, pin, favorites, **real SSE streaming**, stop, retry, regenerate, copy, markdown + GFM tables, syntax-highlighted code with per-block copy, model selector, per-chat system prompt, global custom instructions, token counters (exact/estimated), context-usage meter, day separators, jump-to-latest, message timestamps, clear error states, automatic titles. Context compaction keeps long chats inside the model's `num_ctx` honestly (a note marks what was compacted) |
| **Productivity** | **Command palette (Ctrl+K)**: fuzzy-ranked actions, view navigation, chat search, model switching — 100% keyboard navigable. Global shortcuts (`Ctrl+B` sidebar, `Ctrl+.` inspector, `Ctrl+,` settings, `Ctrl+Alt+N` new chat, `Ctrl+/` help) with in-app reference cards. Focus-visible rings, `prefers-reduced-motion` support |
| **Ollama + OpenRouter** | Ollama: detection, installed-model discovery, context length + capabilities from `/api/show`, streaming chat, safe pull & delete, measured speed tests, **tool calling**. OpenRouter: live catalog, `:free` models, key stored Fernet-encrypted, both providers stay strictly separate — no silent switching |
| **Model Center** | Live catalog (name, provider, local/cloud, availability, capabilities, context length, size, parameters, quantization, measured tok/s, token usage, cost, status), 10 categories, search, filters, sorting, favorites, recently used, speed testing, pull with live progress, delete. Unknown values are shown as **Unknown** — nothing is faked |
| **Agent Mode** | Tool-calling runs over SSE with **human approval for every write/exec action** (exact diff preview, 10-minute validity), sandboxed file tools (`fs_list/read/write/edit`), allow-listed shell (`shell_run`: python/pytest/git/node/ruff/…, no chaining, dangerous-arg scanner), circuit breaker, cooperative stop, full audit log (`executions` + per-run steps/approvals). Denials are audited, never hidden |
| **Coder Mode** | Project-scoped coding workspace: file tree + read-only preview + git chip + the **same** agent gateway. Attach an existing folder (link, never copy). Auto-injects tree + git into runs. Per-run snapshot + undo. Hardware-honest model profile for 8GB VRAM (prefers `qwen2.5-coder:7b` / `qwen3:8b`; never recommends `qwen3-coder:30b`). OpenCode is not embedded — Ollama is the runtime |
| **Projects** | Project workspaces with slug-deduped, path-proven directories; agent runs can be scoped to a project sandbox; archive-only (no destructive delete) |
| **Compare Mode** | One prompt to 2–4 models streamed side by side; local models run one-at-a-time per provider (VRAM safety, honestly shown as *queued*), clouds parallel; CostGuard blocks only the offending slot |
| **Team Mode** | Planner → executor → reviewer pipelines of 2–4 models. Executor turns are **real agent runs** (same gateway, approvals, audit); verdict parsing (`VERDICT: ACCEPTED/CHANGES_REQUESTED`) with exactly one revision max; sequential execution respects VRAM |
| **Research Mode** | Web-grounded answers with numbered citations: DuckDuckGo search → **SSRF-guarded** fetch (private/loopback/link-local blocked, redirect chain re-validated, hard size caps, page text honestly marked truncated) → answer pass with `[n]` citations. Sources that fail are dropped *and said so*; if nothing could be read, the run fails instead of hallucinating. Runs persist to history. The same web layer powers the agent's `web_search`/`web_fetch` tools (READ-tier, `network:fetch` capability) |
| **Token tracking** | Input / output / total — always labeled **exact** or **estimated**. Per message, conversation, model card and session |
| **€0 CostGuard** | `FREE_ONLY=true`, `MAX_SPEND=0.00` by default. Paid requests are **blocked server-side before any network call**: *"Paid model blocked. Free-only mode is enabled. No money was spent."* Tested against request-body bypass attempts |
| **Memory & Skills** | Persistent memory (survives runs, labeled in the prompt): user CRUD in Settings, agent `memory_search/save/forget` tools gated by the `memory` capability (saves are approval-gated + audited, source provenance kept). `AGENT.md` standing instructions chain workspace→project into every run's prompt, inspectable via `/api/memory/context` |
| **Git / GitHub** | Local git inside the workspace sandbox: status (staged/untracked/modified), diff (capped, honest truncation), log, branches (create+switch, validated names), commit (repo identity or honest fallback), push (+set-upstream; HTTPS via GIT_ASKPASS with the token **never in argv/logs** and **only ever to github.com**), remote add (github https/ssh + local). GitHub REST: user, repos, create-private-repo — PAT vault-encrypted, masked everywhere. Mutations need the `git:operate` capability (off by default) and every operation is audited. Destructive ops (reset --hard, force-push, clean) are deliberately not offered |
| **Security** | Fernet-encrypted credentials, workspace path-containment sandbox (blocks `../`, `..\\`, UNC), deny-by-default capability policy (`filesystem:read/write`, `command:execute`, `network:fetch` on by default · `git:operate` opt-in), tool execution audit log, security headers, host/origin guard, API-token middleware, rate limiting, JSON logs with rotation + secret redaction |
| **Windows distribution** | Professional desktop build: PyInstaller onedir (`desktop/aicc_desktop.spec`) with the SPA bundled, native window via pywebview (browser fallback), portable data dir beside the EXE (falls back to `%LOCALAPPDATA%`), deterministic `--smoke` self-check, Inno Setup installer (`desktop/installer.iss`), and a tag-triggered GitHub Actions release pipeline (`.github/workflows/release.yml`) that runs the full test suite before it ever builds |

**Every roadmap feature is real.** No view shows fake functionality; API boundaries that used to answer HTTP 501 are all implemented.

## Quick start

```bash
# 1. one-time setup (venv + backend deps + frontend build + .env)
python setup.py

# 2. install Ollama  →  https://ollama.com/download
#    Windows:  winget install Ollama.Ollama
#    then pull the default model (configurable, see .env):
ollama pull qwen3:0.6b

# 3. start
python start.py          # → http://127.0.0.1:8000
```

`python test_overall.py` runs all four required suites: backend tests → frontend type checking → frontend production build → end-to-end system tests.

**Keyboard shortcuts:** `Ctrl+K` command palette · `Ctrl+B` toggle sidebar ·
`Ctrl+.` toggle inspector · `Ctrl+,` settings · `Ctrl+Alt+N` new chat ·
`Ctrl+/` shortcuts help (⌘ on macOS). Full project documentation: **PROJECT.md**.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | local-first binding |
| `OLLAMA_HOST` | `http://localhost:11434` | runtime location |
| `DEFAULT_MODEL` | `qwen3:0.6b` | **single source of truth** — nothing is hardcoded |
| `OLLAMA_NUM_CTX` | `8192` | tokens; comfortable for 8GB VRAM (4096 = lighter) |
| `OLLAMA_KEEP_ALIVE` | `10m` | keeps the model hot in VRAM |
| `FREE_ONLY` | `true` | hard block on any non-€0.00 model |
| `MAX_SPEND` | `0.00` | lifetime EUR budget |
| `DATA_DIR` | `./data` | SQLite, logs, encrypted key, workspace |
| `LOG_LEVEL` | `INFO` | JSON logs at `data/logs/app.log` |

FREE_ONLY / MAX_SPEND / default model / num_ctx / custom instructions / agent capability toggles (network:fetch on by default, git:operate opt-in) can be changed at runtime (Settings drawer) — persisted in SQLite, **enforced only in the backend**.

## Architecture

```
ai-command-center/
├── main.py                 # entry point (uvicorn)
├── start.py                # cross-platform launcher (uses .venv if present)
├── setup.py                # idempotent first-run setup
├── test_overall.py         # 4-suite system test (backend, ts-check, build, e2e)
├── tools/export_source.py  # full-source snapshot generator (→ FULLSOURCE.md)
├── requirements.txt · pytest.ini · .env.example · ROADMAP
├── backend/app/
│   ├── config.py           # Settings (env/.env) + APP_VERSION — single source of truth
│   ├── main.py             # app factory + composition root (Services)
│   ├── core/errors.py      # one error shape for every API error
│   ├── db/                 # aiosqlite · migrations (V1–V4) · repositories
│   ├── providers/          # base ABC · ollama · openrouter · registry
│   ├── services/           # model_router · cost_guard · chat · models · settings
│   │                       # tokens · credentials · context · projects · compare
│   ├── coder/              # Coder Mode profile + sandboxed tree/file read (REAL)
│   ├── agent/              # tool-calling engine (REAL) — max-steps, circuit breaker
│   ├── team/               # planner/executor/reviewer pipelines (REAL)
│   ├── research/           # web search/fetch layer (SSRF-guarded) + grounded Q&A (REAL)
│   ├── gitops/             # git subprocess service (sandboxed, audited) + GitHub REST
│   ├── tools/              # registry · builtin (fs/shell/web) · gateway executor · audit
│   ├── security/           # crypto vault · permission policy · guards · rate limit
│   ├── workspace/          # path-containment sandbox
│   ├── observability/      # JSON logging (rotation+redaction) · session metrics
│   └── routers/            # health·system·settings·costs·providers·models·conversations
│                           # chat·agent·coder·projects·compare·team·research (SSE) · git
├── frontend/               # React 18 · TypeScript (strict) · Vite · Tailwind v4
│   └── src/                # store · api (REST + SSE) · views (chat/agent/compare/team/
│                           # research/projects/models) · shared agent UI components
├── desktop/                # desktop launcher · PyInstaller spec · Inno Setup installer
│                           # · build driver (npm build → PyInstaller onedir)
└── tests/                  # 239 pytest tests (unit + API + SSE flows, all faked nets)
```

## Windows desktop app (EXE + installer)

```powershell
# one-time extras
pip install -r requirements-desktop.txt        # pyinstaller, pywebview

python desktop/build.py                        # → dist-desktop\AICommandCenter\AICommandCenter.exe
iscc desktop\installer.iss                     # → dist-installer\AICommandCenterSetup-<version>.exe
```

The EXE embeds the built frontend and backend; on first start it opens a native window (or your browser), keeps its data beside the EXE when the folder is writable (portable install) or in `%LOCALAPPDATA%\AICommandCenter` otherwise, and migrates its own database. `AICommandCenter.exe --smoke` runs a deterministic health self-check (exit 0 = healthy). Tagging `v*` on GitHub builds and publishes the installer automatically (tests gate the build).

**Data flow (any LLM call):** request → ModelRouter (explicit provider › synced catalog › default provider; *no cross-provider fallback*) → **CostGuard** (blocks paid/budget-breakers pre-network) → provider stream → exact token accounting → usage ledger → session metrics.

**Tool flow (agent):** model tool_call → ToolExecutor gateway (1. capability check → 2. arg validation → 3. human approval for write/exec → 4. sandboxed execution → 5. audit row — *always, including denials*) → result back to the model.

## Database (SQLite, migration-managed)

`conversations · messages · models · providers · settings · credentials · usage_events · executions · projects · research · teams · team_members · agent_runs · agent_steps · approvals · team_runs · memories · schema_migrations` (V1–V5 migrations)

## API summary

`GET /api/health` · `GET /api/system/status` · `GET|PUT /api/settings` · `GET /api/costs` · `GET /api/usage/tokens` · `GET /api/providers` (+key endpoints) · `GET /api/models` (+filters) · `POST /api/models/refresh|test|pull(SSE)` · favorites/delete · `GET|POST|PATCH|DELETE /api/conversations[/{id}]` · `POST /api/chat/completions|regenerate|stop (SSE)` · `POST /api/agent/runs (SSE)` + stop/history/approvals/capabilities/tools/executions · `GET /api/coder/profile|tree|file` · `GET|POST /api/projects` (+archive) · `POST /api/compare/runs (SSE)` · `GET|POST /api/team` + runs (SSE)/stop · `POST /api/research/query (SSE)` + history/detail/stop · `GET /api/git/status|log|diff|branches` · `POST /api/git/init|branches|commit|push|remote` · `/api/git/github/token|user|repos` · `GET|POST|DELETE /api/memory` · `GET|PUT /api/memory/file` · `GET /api/memory/context`. Interactive docs at `/api/docs`.

## Hardware notes (RTX 4060 Ti 8GB)

- GPU: `qwen3:0.6b` is instant; 4B–8B models (e.g. `qwen3:4b`, `qwen3:8b`, `qwen2.5-coder:7b`) are the quality sweet spot on 8GB VRAM. Coder Mode recommends the 7–8B tags and marks `qwen3-coder:30b` as too big.
- `num_ctx` 8192 default; drop to 4096 for 7B+ models if VRAM tightens.
- `keep_alive=10m` avoids reload latency between messages.
- Compare/Team modes serialize local models per provider automatically — parallel local runs would thrash VRAM.

## Troubleshooting

- **"Ollama unavailable"** — install & start Ollama, then Model Center → *Refresh*. Verify with `curl http://localhost:11434/api/version`.
- **Nothing in Model Center** — press *Refresh* (live discovery) or *Pull* the default model.
- **Research says RESEARCH_DISABLED** — Settings → Agent permissions → *Network fetch* is off.
- **Frontend missing** — `python setup.py` builds it (needs Node 22/npm 10).

## License / attribution

Original code & design. Interface takes only broad UX inspiration from modern AI products — no branding, assets or designs were copied.
