# AI Command Center

**Local-first AI operating environment** — premium chat, Model Center, a controlled Agent, Multi-Model Team Mode (flagship), Compare, Research, first-class Projects and Git/GitHub — all with strict **€0 cost protection** enforced server-side before any provider request.

Built for: Windows 11 · Intel i7-12700F · RTX 4060 Ti 8GB · 32GB RAM · Python 3.11+ · Node.js 22 (works on Linux/macOS too).

---

## What works (fully implemented, tested)

| Area | Capabilities |
|---|---|
| **Chat** | Conversations (search, folders-by-pin, rename, delete, archive, pin, favorites), real SSE streaming from Ollama, stop, retry, regenerate, markdown + GFM tables + task lists, syntax-highlighted code with per-block copy, model selector, per-chat system prompt, global custom instructions, token counters (exact/estimated), context-usage meter, clear error states, auto-titles from the local model |
| **Ollama** | running/unavailable/timeout detection, installed-model discovery, context length + capabilities from `/api/show`, real streaming chat, safe pull (live progress) & delete, measured speed tests |
| **Model Center** | live catalog, search, category filters (10), sorting, favorites, recently used, selection, speed test, pull/delete, usage + measured tok/s. Unknown = "Unknown" — never faked |
| **Agent Mode** | real controlled agent: PLAN → EXECUTE → VERIFY → FIX → FINALIZE. Tools: read/write/edit file, search, list, mkdir, delete file, allowlisted commands (`pytest`, `npm run …`, `tsc`, …). Security: workspace sandbox (blocks absolute paths, `..` / `..\` traversal, symlink escapes), no shell (argv-only, metacharacters rejected), command + subcommand allowlists, argument escape checks, hard timeouts, full execution auditing, per-step + token persistence |
| **Team Mode (flagship)** | 2–4 models: TASK → PLANNING (every model analyzes requirements/architecture/risks/dependencies/subtasks/testing/tools) → MASTER PLAN → ROLE ASSIGNMENT (Architect/Developer/QA/Security Reviewer/Researcher/Documentation, auto by capabilities with manual overrides) → EXECUTION → REVIEW → FIX → FINAL REVIEW → DELIVERY. Shared state: task, master plan, decisions, work products, findings, errors on a live board (TODO/IN PROGRESS/REVIEW/DONE with manual override). Per-model tokens + **TEAM TOTAL** + **COST €0.00**. Sequential on one GPU. No chain-of-thought exposed — decisions/actions/findings/status only |
| **Compare Mode** | N models answer one prompt side-by-side with streaming, per-answer token usage, select best answer, combine answers via local model, persisted runs |
| **Research Mode** | real multi-source search (DuckDuckGo, configurable / disable-able), source list with URLs + snippets + fetched excerpts, optional local-model summary/comparison with `[n]` citations, markdown export. Zero fabricated sources: failures are honest |
| **Projects** | first-class objects with sandboxed workspaces, linked chats, files (rescanned from disk), tasks, settings JSON; Agent & Team runs can target a project |
| **Git/GitHub** | real Git (status/branch/log/diff/add/commit) inside sandboxed project workspaces — argv-only, subcommand allowlist, audited. GitHub REST (repos/issues/PRs, issue creation) only when a token exists (env `GITHUB_TOKEN` or encrypted vault credential); otherwise an explicit unauthenticated state — never faked |
| **€0 CostGuard** | `FREE_ONLY=true`, `MAX_SPEND=0.00` by default. Every model call — chat, auto-title, agent, team, compare, research synthesis — is blocked server-side **before any network request** if it costs money. Exact message: *"Paid model blocked. Free-only mode is enabled. No money was spent."* No paid fallback exists anywhere |
| **Security** | Fernet-encrypted credentials (key in `data/secret.key`, 0600 or `AI_CC_SECRET_KEY`), path-containment sandbox (POSIX + Windows escapes), deny-by-default permission policy, command allowlist + subcommand allowlist, shell injection protection, process timeouts, audit log (`executions` + per-mode tables), localhost binding |
| **Observability** | JSON logs (`data/logs/app.log`), session metrics, exact/estimated label integrity, per-model & system-wide token usage |

## Quick start

```bash
# 1. start (verifies Python, installs ONLY missing backend deps, builds the
#    frontend only if missing/stale, creates .env once, checks Ollama)
python start.py

# 2. install Ollama  →  https://ollama.com/download
#    Windows:  winget install Ollama.Ollama
ollama pull qwen3:0.6b        # default model, fully configurable

# 3. open http://127.0.0.1:8000
```

`python start.py --check-only` verifies the environment without starting.
`python test_overall.py` runs all four suites: backend tests → frontend type checking → frontend production build → end-to-end system tests (real server, mock Ollama, all modes).

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | local-first binding |
| `OLLAMA_HOST` | `http://localhost:11434` | runtime location |
| `DEFAULT_MODEL` | `qwen3:0.6b` | single source of truth — nothing hardcoded |
| `OLLAMA_NUM_CTX` | `8192` | tokens; comfortable for 8GB VRAM (4096 = lighter) |
| `OLLAMA_KEEP_ALIVE` | `10m` | keeps the model hot in VRAM |
| `FREE_ONLY` | `true` | hard block on any non-€0.00 model |
| `MAX_SPEND` | `0.00` | lifetime EUR budget |
| `DATA_DIR` / `WORKSPACE_ROOT` | `./data` / `./data/workspace` | SQLite, logs, encrypted key, sandbox (relative paths resolve against the project root, not CWD) |
| `AGENT_MAX_STEPS` | `20` | max agent tool steps (3–100) |
| `AGENT_MAX_FIX_ROUNDS` | `2` | fix rounds after verification failures |
| `AGENT_CMD_TIMEOUT` | `120` | command timeout seconds |
| `TEAM_MAX_ROUNDS` | `2` | review/fix rounds in Team Mode |
| `SEARCH_ENGINE` | `duckduckgo` | `duckduckgo` \| `disabled` |
| `GITHUB_TOKEN` | — | optional; enables GitHub features |

Settings can also be changed at runtime (Settings drawer) — persisted in SQLite, enforced only in the backend.

## Architecture

```
ai-command-center/
├── main.py                 # entry point (uvicorn)
├── start.py                # launcher: env check, missing-only installs, build, start
├── test_overall.py         # 4-suite system test (backend, ts-check, build, e2e)
├── requirements.txt · pytest.ini · .env.example · README.md · ROADMAP
├── backend/app/
│   ├── config.py           # Settings (env/.env) — single source of truth
│   ├── main.py             # app factory + composition root (Services)
│   ├── core/errors.py      # one error shape for every API error
│   ├── db/                 # aiosqlite · migrations (v1, v2) · repositories
│   ├── providers/          # base ABC · ollama · registry   ← provider boundary
│   ├── services/           # model_router · model_runner (guarded metered calls)
│   │                       # cost_guard · chat · compare · models · settings · tokens
│   ├── agent/              # controlled Agent engine (sandboxed tools)
│   ├── team/               # Multi-Model Team engine (flagship)
│   ├── research/           # research engine (real sources, citations, export)
│   ├── gitops/             # Git service + GitHub client (honest auth states)
│   ├── tools/              # sandboxed file tools · allowlisted command runner · audit
│   ├── security/           # crypto vault · permission policy
│   ├── workspace/          # path-containment sandbox
│   ├── routers/            # health · system · settings · costs · providers · models
│   │                       # conversations · chat (SSE) · agent · team · compare
│   │                       # research · projects · git (all SSE/REST, no stubs)
│   └── observability/      # JSON logging · session metrics
├── frontend/               # React 18 · TypeScript (strict) · Vite · Tailwind v4
└── tests/                  # pytest: API, providers, security, tools, agent,
                            # team, compare, research, projects, git (103 tests)
```

**Data flow (any model call):** resolve → **CostGuard** (blocks paid/budget-breakers pre-network) → stream from provider → exact/estimated token accounting → usage ledger (conversation/message, team member, run) → model totals → session metrics. Engines (Agent/Team/Compare/Research) never talk to a provider directly — they go through `ModelRunner`, which is the single guarded path.

## Database (SQLite, migration-managed, additive)

`conversations · messages · models · providers · projects · tasks · teams · team_members · team_events · team_tasks · agent_runs · agent_steps · compare_runs · compare_answers · usage_events (tokens+costs, team-aware) · files · executions · research · settings · credentials · schema_migrations` — existing data is preserved; schema evolves only via new migrations.

## API summary

`/api/health` · `/api/system/status` · `GET|PUT /api/settings` · `/api/costs` · `/api/usage/tokens` · `/api/providers` · `/api/models(...)` · `/api/conversations` · `/api/chat/completions|regenerate(SSE)|stop` · `/api/agent/runs(SSE)|{id}` · `/api/team/runs(SSE)|{id}|board|export` · `/api/compare/runs(SSE)|{id}|select|combine` · `/api/research/runs(SSE)|{id}|export` · `/api/projects` · `/api/git/*` · `/api/github/*`. Interactive docs at `/api/docs`.

## Hardware notes (RTX 4060 Ti 8GB)

- `qwen3:0.6b` is instant; 4B–8B models are the quality sweet spot on 8GB VRAM.
- `num_ctx` 8192 default; drop to 4096 for 7B+ models if VRAM tightens.
- Team Mode runs models sequentially — one GPU serves one model at a time.

## Troubleshooting

- **"Ollama unavailable"** — start Ollama, then Model Center → *Refresh*.
- **Research fails** — the sandbox has no internet or the engine is disabled; set `SEARCH_ENGINE=duckduckgo` and try again. Failures are always explicit.
- **GitHub shows unauthenticated** — that's the honest state until `GITHUB_TOKEN` is set.
- **Frontend missing** — `python start.py` builds it (needs Node 22/npm 10).

## License / attribution

Original code & design. Interface takes only broad UX inspiration from modern AI products — no branding, assets or designs were copied.
