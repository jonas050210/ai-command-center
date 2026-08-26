# AI Command Center

**Local-first AI workspace** — chat, Agent Mode with human-gated tools, Coder Mode, Projects, Compare, Team and Research — powered by local Ollama and free-tier OpenRouter, with strict **€0 cost protection** enforced in the backend before any provider request.

Built for: Windows 11 · Intel i7-12700F · RTX 4060 Ti 8GB · 32GB RAM · Python 3.11.9 · Node.js 22 (works on Linux/macOS too).

Version **0.15.0**. Full architecture, file inventory, security model, API reference, database schema and honest limitations live in **[PROJECT.md](./PROJECT.md)** — this README is the quick start.

---

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

`python test_overall.py` runs all four suites: backend tests → frontend type-check → production build → end-to-end system tests.

**Keyboard shortcuts:** `Ctrl+K` command palette · `Ctrl+B` toggle sidebar · `Ctrl+.` toggle inspector · `Ctrl+,` settings · `Ctrl+Alt+N` new chat · `Ctrl+/` shortcuts help (⌘ on macOS).

---

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | local-first binding |
| `OLLAMA_HOST` | `http://localhost:11434` | runtime location |
| `DEFAULT_MODEL` | `qwen3:0.6b` | single source of truth — nothing is hardcoded |
| `OLLAMA_NUM_CTX` | `8192` | tokens; comfortable for 8GB VRAM (4096 = lighter) |
| `OLLAMA_KEEP_ALIVE` | `10m` | keeps the model hot in VRAM |
| `FREE_ONLY` | `true` | hard block on any non-€0.00 model |
| `MAX_SPEND` | `0.00` | lifetime EUR budget |
| `DATA_DIR` | `./data` | SQLite, logs, encrypted key, workspace |
| `LOG_LEVEL` | `INFO` | JSON logs at `data/logs/app.log` |

`FREE_ONLY` / `MAX_SPEND` / default model / num_ctx / custom instructions / agent capability toggles (`network:fetch` on by default, `git:operate` opt-in) can be changed at runtime in the Settings drawer — persisted in SQLite, **enforced only in the backend**.

---

## Windows desktop app (EXE + installer)

```powershell
# one-time extras
pip install -r requirements-desktop.txt        # pyinstaller, pywebview

python desktop/build.py                        # → dist-desktop\AICommandCenter\AICommandCenter.exe
iscc desktop\installer.iss                     # → dist-installer\AICommandCenterSetup-0.15.0.exe
```

The EXE embeds the built frontend and backend; on first start it opens a native window (or your browser), keeps its data beside the EXE when the folder is writable (portable install) or in `%LOCALAPPDATA%\AICommandCenter` otherwise, and migrates its own database. `AICommandCenter.exe --smoke` runs a deterministic health self-check (exit 0 = healthy).

---

## Troubleshooting

- **"Ollama unavailable"** — install & start Ollama, then Model Center → *Refresh*. Verify with `curl http://localhost:11434/api/version`.
- **Nothing in Model Center** — press *Refresh* (live discovery) or *Pull* the default model.
- **Research says RESEARCH_DISABLED** — Settings → Agent permissions → *Network fetch* is off.
- **Frontend missing** — `python setup.py` builds it (needs Node 22/npm 10).

---

*Original code & design. The interface takes only broad UX inspiration from modern AI products — no branding, assets or designs were copied.*
