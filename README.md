# AI Command Center

**Local-first AI workspace** — premium chat + Ollama runtime + OpenRouter (free-tier) providers, Agent Mode with human-gated tools, Projects, Compare, Team and Research Mode — with strict **€0 cost protection** enforced in the backend before any provider request is ever made.

Built for: Windows 11 · Intel i7-12700F · RTX 4060 Ti 8GB · 32GB RAM · Python 3.11.9 · Node.js 22 (works on Linux/macOS too).

---

## What works today (Phases 0–6, fully implemented, tests passing)

| Area | Capabilities |
|---|---|
| **Chat** | New conversation, history, search, rename, delete, archive, pin, favorites, **real SSE streaming**, stop, retry, regenerate, copy, markdown + GFM tables, syntax-highlighted code with per-block copy, model selector, per-chat system prompt, global custom instructions, token counters (exact/estimated), context-usage meter, clear error states, automatic titles. Context compaction keeps long chats inside the model's `num_ctx` honestly (a note marks what was compacted) |
| **Ollama + OpenRouter** | Ollama: detection, installed-model discovery, context length + capabilities from `/api/show`, streaming chat, safe pull & delete, measured speed tests, **tool calling**. OpenRouter: live catalog, `:free` models, key stored Fernet-encrypted, both providers stay strictly separate — no silent switching |
| **Model Center** | Live catalog (name, provider, local/cloud, availability, capabilities, context length, size, parameters, quantization, measured tok/s, token usage, cost, status), 10 categories, search, filters, sorting, favorites, recently used, speed testing, pull with live progress, delete. Unknown values are shown as **Unknown** — nothing is faked |
| **Agent Mode** | Tool-calling runs over SSE with **human approval for every write/exec action** (exact diff preview, 10-minute validity), sandboxed file tools (`fs_list/read/write/edit`), allow-listed shell (`shell_run`: python/pytest/git/node/ruff/…, no chaining, dangerous-arg scanner), circuit breaker, cooperative stop, full audit log (`executions` + per-run steps/approvals). Denials are audited, never hidden |
| **Projects** | Project workspaces with slug-deduped, path-proven directories; agent runs can be scoped to a project sandbox; archive-only (no destructive delete) |
| **Compare Mode** | One prompt to 2–4 models streamed side by side; local models run one-at-a-time per provider (VRAM safety, honestly shown as *queued*), clouds parallel; CostGuard blocks only the offending slot |
| **Team Mode** | Planner → executor → reviewer pipelines of 2–4 models. Executor turns are **real agent runs** (same gateway, approvals, audit); verdict parsing (`VERDICT: ACCEPTED/CHANGES_REQUESTED`) with exactly one revision max; sequential execution respects VRAM |
| **Research Mode** | Web-grounded answers with numbered citations: DuckDuckGo search → **SSRF-guarded** fetch (private/loopback/link-local blocked, redirect chain re-validated, hard size caps, page text honestly marked truncated) → answer pass with `[n]` citations. Sources that fail are dropped *and said so*; if nothing could be read, the run fails instead of hallucinating. Runs persist to history. The same web layer powers the agent's `web_search`/`web_fetch` tools (READ-tier, `network:fetch` capability) |
| **Token tracking** | Input / output / total — always labeled **exact** or **estimated**. Per message, conversation, model card and session |
| **€0 CostGuard** | `FREE_ONLY=true`, `MAX_SPEND=0.00` by default. Paid requests are **blocked server-side before any network call**: *"Paid model blocked. Free-only mode is enabled. No money was spent."* Tested against request-body bypass attempts |
| **Security** | Fernet-encrypted credentials, workspace path-containment sandbox (blocks `../`, `..\\`, UNC), deny-by-default capability policy (`filesystem:read/write`, `command:execute`, `network:fetch` on · `git:operate` reserved), tool execution audit log, security headers, host/origin guard, API-token middleware, rate limiting, JSON logs with rotation + secret redaction |

**Honestly marked NOT IMPLEMENTED** (HTTP 501 at its API boundary, badge in the UI): **Git/GitHub integration** (Phase 7).

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

FREE_ONLY / MAX_SPEND / default model / num_ctx / custom instructions / agent capability toggles (`network:fetch` on by default since Research Mode shipped; `git:operate` reserved off) can be changed at runtime (Settings drawer) — persisted in SQLite, **enforced only in the backend**.

## Architecture

```
ai-command-center/
├── main.py                 # entry point (uvicorn)
├── start.py                # cross-platform launcher (uses .venv if present)
├── setup.py                # idempotent first-run setup
├── test_overall.py         # 4-suite system test (backend, ts-check, build, e2e)
├── requirements.txt · pytest.ini · .env.example · ROADMAP
├── backend/app/
│   ├── config.py           # Settings (env/.env) + APP_VERSION — single source of truth
│   ├── main.py             # app factory + composition root (Services)
│   ├── core/errors.py      # one error shape for every API error
│   ├── db/                 # aiosqlite · migrations (V1–V4) · repositories
│   ├── providers/          # base ABC · ollama · openrouter · registry
│   ├── services/           # model_router · cost_guard · chat · models · settings
│   │                       # tokens · credentials · context · projects · compare
│   ├── agent/              # tool-calling engine (REAL) — max-steps, circuit breaker
│   ├── team/               # planner/executor/reviewer pipelines (REAL)
│   ├── research/           # web search/fetch layer (SSRF-guarded) + grounded Q&A (REAL)
│   ├── gitops/             # Phase 7 boundary (HTTP 501, no fakes)
│   ├── tools/              # registry · builtin (fs/shell/web) · gateway executor · audit
│   ├── security/           # crypto vault · permission policy · guards · rate limit
│   ├── workspace/          # path-containment sandbox
│   ├── observability/      # JSON logging (rotation+redaction) · session metrics
│   └── routers/            # health·system·settings·costs·providers·models·conversations
│                           # chat·agent·projects·compare·team·research (SSE) · future(501)
├── frontend/               # React 18 · TypeScript (strict) · Vite · Tailwind v4
│   └── src/                # store · api (REST + SSE) · views (chat/agent/compare/team/
│                           # research/projects/models) · shared agent UI components
└── tests/                  # 197 pytest tests (unit + API + SSE flows, all faked nets)
```

**Data flow (any LLM call):** request → ModelRouter (explicit provider › synced catalog › default provider; *no cross-provider fallback*) → **CostGuard** (blocks paid/budget-breakers pre-network) → provider stream → exact token accounting → usage ledger → session metrics.

**Tool flow (agent):** model tool_call → ToolExecutor gateway (1. capability check → 2. arg validation → 3. human approval for write/exec → 4. sandboxed execution → 5. audit row — *always, including denials*) → result back to the model.

## Database (SQLite, migration-managed)

`conversations · messages · models · providers · settings · credentials · usage_events · executions · projects · research · teams · team_members · agent_runs · agent_steps · approvals · team_runs · schema_migrations`

## API summary

`GET /api/health` · `GET /api/system/status` · `GET|PUT /api/settings` · `GET /api/costs` · `GET /api/usage/tokens` · `GET /api/providers` (+key endpoints) · `GET /api/models` (+filters) · `POST /api/models/refresh|test|pull(SSE)` · favorites/delete · `GET|POST|PATCH|DELETE /api/conversations[/{id}]` · `POST /api/chat/completions|regenerate|stop (SSE)` · `POST /api/agent/runs (SSE)` + stop/history/approvals/capabilities/tools/executions · `GET|POST /api/projects` (+archive) · `POST /api/compare/runs (SSE)` · `GET|POST /api/team` + runs (SSE)/stop · `POST /api/research/query (SSE)` + history/detail/stop · `/api/git → 501`. Interactive docs at `/api/docs`.

## Hardware notes (RTX 4060 Ti 8GB)

- GPU: `qwen3:0.6b` is instant; 4B–8B models (e.g. `qwen3:4b`, `llama3.1:8b`) are the quality sweet spot on 8GB VRAM.
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
