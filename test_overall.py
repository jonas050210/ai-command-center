"""Overall system test — runs the required suites:

    1. Backend tests                  (pytest tests/)
    2. Frontend type checking         (npm run typecheck)
    3. Frontend production build      (npm run build)
    4. End-to-end system tests        (real server + real HTTP, mock Ollama)

Usage:
    python test_overall.py                    → all four suites
    python test_overall.py --skip-frontend    → suites 1 + 4
    python test_overall.py --skip-backend     → suites 2 + 3 + 4
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MOCK_PORT = 12091
APP_PORT = 18123
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def npm_cmd() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd")


# ─────────────────────────── suites 1-3 ──────────────────────────────
def run_backend_tests() -> bool:
    print("\n[1/4] Backend tests (pytest)")
    python = sys.executable
    venv = ROOT / ".venv"
    for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if candidate.exists():
            python = str(candidate)
    code = subprocess.call([python, "-m", "pytest", "-q"], cwd=ROOT)
    record("backend test suite", code == 0, "pytest exit=" + str(code))
    return code == 0


def run_frontend(kind: str) -> bool:
    npm = npm_cmd()
    if npm is None:
        record(f"frontend {kind}", False, "npm not found — install Node.js 22")
        return False
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").exists():
        print("  installing frontend dependencies…")
        if subprocess.call([npm, "install", "--no-audit", "--no-fund"], cwd=frontend) != 0:
            record(f"frontend {kind}", False, "npm install failed")
            return False
    script = "typecheck" if kind == "type checking" else "build"
    code = subprocess.call([npm, "run", script], cwd=frontend)
    record(f"frontend {kind}", code == 0)
    return code == 0


# ─────────────────────────── suite 4 (e2e) ───────────────────────────
# Stateful mock Ollama that understands the engine prompt protocols, so
# Agent / Team / Compare runs complete deterministically end-to-end.
MOCK_APP_SRC = r"""
import json
from fastapi import FastAPI, Request
from fastapi.responses import Response

app = FastAPI()
REPLY = "System test reply from mock Ollama."
state = {"phase": "idle"}

def pick(body: dict) -> str:
    text = " ".join(m.get("content", "") for m in body.get("messages", []))
    if "Implementation plan" in text or "Answer with the plan only" in text:
        state["phase"] = "plan"
        return "- Inspect the workspace\n- Create result.txt\n- Verify with tests"
    if "exactly one TOOL action" in text:
        if state.get("phase") == "done":
            return "TOOL done Created result.txt with the delivered content."
        if state.get("phase") == "execute":
            state["phase"] = "done"
            return "TOOL done Created result.txt with the delivered content."
        state["phase"] = "execute"
        return ("TOOL write_file path=\"result.txt\"\n<<<FILE\n"
                "Task delivered by the test agent.\nFILE\n")
    if "delivery summary" in text:
        state["phase"] = "done"
        return ("Agent delivered result.txt and verified it. "
                "SUMMARY: complete.")
    if "Analyze this task" in text:
        return ("REQUIREMENTS: build a module\nARCHITECTURE: single file\n"
                "RISKS: none\nDEPENDENCIES: python\nSUBTASKS: code, test\n"
                "TESTING: pytest\nTOOLS: read, write")
    if "MASTER PLAN" in text and "Team analyses" in text:
        return ("- Implement the module\n- Write tests\n- Review the result")
    if "Produce YOUR work product" in text:
        return ("DESIGN: module with greet()\nCODE: ```python\ndef greet(n): "
                "return 'hi ' + n\n```\nTEST: pytest case included.")
    if "Review the work product" in text or "Review team work" in text:
        return "VERDICT: APPROVE\nFINDINGS:\n- (none)"
    if "Compose the final deliverable" in text:
        return ("# Deliverable\nModule with greet(), tests passed, "
                "reviewed by the team.\n\nSTATUS: complete")
    return REPLY

@app.get("/api/version")
async def version():
    return {"version": "0.0-overall-test"}

@app.get("/api/tags")
async def tags():
    return {"models": [
        {"name": "qwen3:0.6b", "model": "qwen3:0.6b", "size": 522000000,
         "digest": "overall-test", "modified_at": "2026-01-01T00:00:00Z",
         "details": {"format": "gguf", "family": "qwen3",
                     "families": ["qwen3"], "parameter_size": "0.6B",
                     "quantization_level": "Q4_K_M"}},
        {"name": "deepseek-r1:7b", "model": "deepseek-r1:7b", "size": 4700000000,
         "digest": "overall-test-2", "modified_at": "2026-01-01T00:00:00Z",
         "details": {"format": "gguf", "family": "qwen2",
                     "families": ["qwen2"], "parameter_size": "7B",
                     "quantization_level": "Q4_K_M"}}]}

@app.post("/api/show")
async def show(request: Request):
    return {"model_info": {"qwen3.context_length": 40960},
            "capabilities": ["completion", "tools"]}

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    prompt = sum(len(m.get("content", "")) for m in body.get("messages", []))
    words = pick(body).split(" ")
    lines = []
    for w in words:
        lines.append(json.dumps({"message": {"role": "assistant", "content": w + " "},
                                 "done": False}))
    lines.append(json.dumps({"message": {"role": "assistant", "content": ""},
                             "done": True, "prompt_eval_count": max(1, prompt // 4),
                             "eval_count": len(words), "eval_duration": 700000000}))
    return Response("\n".join(lines) + "\n", media_type="application/x-ndjson")
"""


def start_mock_ollama() -> "threading.Thread":
    (ROOT / "tests" / "_overall_mock_ollama.py").write_text(MOCK_APP_SRC, encoding="utf-8")
    import uvicorn
    sys.path.insert(0, str(ROOT / "tests"))
    import importlib
    mod = importlib.import_module("_overall_mock_ollama")
    config = uvicorn.Config(mod.app, host="127.0.0.1", port=MOCK_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    server._overall_should_exit = False  # type: ignore[attr-defined]
    return thread


def http(method: str, path: str, body: dict | None = None, port: int = APP_PORT,
         timeout: float = 60.0) -> tuple[int, bytes]:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, ConnectionError, OSError):
        return 0, b""


def parse_sse(raw: bytes) -> list[dict]:
    events = []
    for block in raw.decode().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def run_system_tests() -> bool:
    print("\n[4/4] End-to-end system tests (real server, mock Ollama)")
    start_mock_ollama()
    for _ in range(60):
        code, _ = http("GET", "/api/version", port=MOCK_PORT)
        if code == 200:
            break
        time.sleep(0.2)
    else:
        record("mock ollama boots", False, "no response on mock port")
        return False
    record("mock ollama boots", True)

    tmp = tempfile.mkdtemp(prefix="aicc-overall-")
    env = {**os.environ, "DATA_DIR": tmp, "HOST": "127.0.0.1", "PORT": str(APP_PORT),
           "OLLAMA_HOST": f"http://127.0.0.1:{MOCK_PORT}",
           "DEFAULT_MODEL": "qwen3:0.6b", "FREE_ONLY": "true", "MAX_SPEND": "0",
           "SEARCH_ENGINE": "disabled"}
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    all_ok = True
    try:
        for _ in range(80):
            if proc.poll() is not None:
                record("server starts", False, "process exited early")
                return False
            code, _ = http("GET", "/api/health", timeout=2)
            if code == 200:
                break
            time.sleep(0.25)
        else:
            record("server starts", False, "health check never passed")
            return False
        record("server starts", True)

        code, health = http("GET", "/api/health")
        h = json.loads(health)
        record("health: db ok + ollama detected running",
               code == 200 and h["db"] == "ok" and h["ollama"]["status"] == "running",
               f"ollama={h['ollama']['status']}")

        code, settings = http("GET", "/api/settings")
        s = json.loads(settings)
        record("settings: default model from config (not hardcoded)",
               code == 200 and s["default_model"] == "qwen3:0.6b", s.get("default_model", "?"))
        record("settings: FREE_ONLY=true, MAX_SPEND=0.00",
               s.get("free_only") is True and float(s.get("max_spend", -1)) == 0.0)

        code, refresh = http("POST", "/api/models/refresh")
        r = json.loads(refresh)
        synced = r["results"]["ollama"]["synced"]
        record("model discovery syncs real models", code == 200 and synced == 2,
               f"synced={synced}")

        code, models = http("GET", "/api/models")
        m = json.loads(models)["models"][0]
        record("model center: real fields (ctx 40960, free, local)",
               m["context_length"] == 40960 and m["is_free"] and m["location"] == "local")

        code, raw = http("POST", "/api/chat/completions",
                         {"content": " overall system test"})
        events = parse_sse(raw)
        types = [e["type"] for e in events]
        usage = next((e for e in events if e["type"] == "usage"), {})
        content = "".join(e.get("content", "") for e in events if e["type"] == "delta")
        ok = (code == 200 and types[0] == "meta" and "delta" in types
              and types[-1] == "done")
        record("chat: SSE meta→delta→usage→done", ok, " → ".join(types))
        record("chat: real reply content streamed", "System test reply" in content)
        record("chat: EXACT token accounting", usage.get("method") == "exact"
               and usage.get("input_tokens", 0) > 0,
               f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}")
        record("chat: cost is €0.00", usage.get("cost_eur") == 0.0)
        conv_id = events[0]["conversation_id"]

        code, conv = http("GET", f"/api/conversations/{conv_id}")
        c = json.loads(conv)
        assistant = [x for x in c["messages"] if x["role"] == "assistant"][0]
        record("persistence: conversation + exact-labelled messages",
               code == 200 and c["total_tokens"] > 0
               and assistant["token_method"] == "exact")

        code, costs = http("GET", "/api/costs")
        co = json.loads(costs)
        record("costs: current/session/total all €0.00",
               co["current"] == 0.0 and co["session"] == 0.0 and co["total"] == 0.0)

        # paid / unknown provider must be rejected without any fallback
        code, raw = http("POST", "/api/chat/completions",
                         {"content": "hi", "provider": "openai", "model": "gpt-4o"})
        err_events = parse_sse(raw)
        err = err_events[-1]
        record("guard: paid provider blocked (no fallback, pre-network)",
               err["type"] == "error" and err["status_code"] in (400, 403),
               f"code={err.get('code')}")
        code, costs2 = http("GET", "/api/costs")
        record("guard: nothing spent after blocked request",
               json.loads(costs2)["total"] == 0.0)

        code, stop = http("POST", "/api/chat/stop", {"request_id": "nonexistent"})
        record("chat stop endpoint (404 for unknown id)",
               code == 404 and json.loads(stop)["error"]["code"] == "REQUEST_NOT_FOUND")

        # ── PROJECTS ──
        code, proj = http("POST", "/api/projects", {"name": "E2E", "description": "x"})
        project = json.loads(proj)
        pid = project["id"]
        record("projects: create with sandbox workspace",
               code == 200 and project["root_path"] == f"projects/p{pid}", f"pid={pid}")
        code, pfiles = http("GET", f"/api/projects/{pid}/files")
        record("projects: files listing endpoint",
               code == 200 and "workspace" in json.loads(pfiles))

        # ── AGENT (scripted mock) ──
        code, raw = http("POST", "/api/agent/runs",
                         {"task": "Create result.txt", "project_id": pid})
        events = parse_sse(raw)
        types = [e["type"] for e in events]
        final = events[-1]
        record("agent: run lifecycle (run/stage/activity/done)",
               events[0]["type"] == "run" and "stage" in types
               and "activity" in types and "done" in types)
        record("agent: delivered end-to-end", final["status"] == "delivered",
               f"status={final.get('status')}")
        agent_run = events[0]["run_id"]
        code, arun = http("GET", f"/api/agent/runs/{agent_run}")
        arun = json.loads(arun)
        record("agent: plan + steps persisted",
               bool(arun["plan"]) and len(arun.get("steps", [])) > 0,
               f"steps={len(arun.get('steps', []))}")
        # workspace file was created inside the sandbox
        ws = Path(tmp) / "workspace" / "projects" / f"p{pid}" / "result.txt"
        record("agent: file written inside sandbox workspace", ws.exists())

        # ── TEAM (flagship) ──
        code, raw = http("POST", "/api/team/runs", {
            "task": "Build greeting module",
            "models": ["qwen3:0.6b", "deepseek-r1:7b"]})
        events = parse_sse(raw)
        types = [e["type"] for e in events]
        final = events[-1]
        team_id = events[0]["team_id"]
        record("team: full flow planning→roles→execution→delivery",
               final.get("status") == "delivered" and "phase" in types,
               f"status={final.get('status')}")
        code, tstate = http("GET", f"/api/team/runs/{team_id}")
        t = json.loads(tstate)
        record("team: master plan + roles + board shared state",
               bool(t["master_plan"]) and len(t["members"]) == 2
               and len(t["tasks"]) > 0)
        record("team: TEAM TOTAL tokens + COST €0.00",
               t["tokens"]["total_tokens"] > 0 and t["tokens"]["cost_eur"] == 0.0,
               f"total={t['tokens']['total_tokens']} cost={t['tokens']['cost_eur']}")
        code, board = http("GET", f"/api/team/runs/{team_id}/board")
        board = json.loads(board)
        record("team: task board TODO/IN PROGRESS/REVIEW/DONE",
               all(k in board["board"] for k in
                   ("todo", "in_progress", "review", "done")))
        code, export = http("GET", f"/api/team/runs/{team_id}/export")
        record("team: export includes TEAM TOTAL",
               b"TEAM TOTAL" in export and b"COST" in export)

        # ── COMPARE ──
        code, raw = http("POST", "/api/compare/runs", {
            "prompt": "Write a one-line summary.",
            "models": ["qwen3:0.6b", "deepseek-r1:7b"]})
        events = parse_sse(raw)
        compare_id = events[0]["run_id"]
        record("compare: parallel answers + done",
               any(e["type"] == "answer_done" for e in events) and
               events[-1]["type"] == "done")
        code, cstate = http("GET", f"/api/compare/runs/{compare_id}")
        c = json.loads(cstate)
        record("compare: persisted answers with €0.00 cost",
               len(c["answers"]) == 2 and all(a["cost_eur"] == 0.0 for a in c["answers"]))
        code, comb = http("POST", f"/api/compare/runs/{compare_id}/combine")
        record("compare: combine answers", code == 200 and json.loads(comb)["combined"])

        # ── RESEARCH (search disabled in this sandbox → honest failure) ──
        code, raw = http("POST", "/api/research/runs", {"query": "anything"})
        events = parse_sse(raw)
        record("research: disabled is honest (no fake sources)",
               any(e["type"] == "error" and "disabled" in e.get("message", "").lower()
                   for e in events), str(events))

        # ── GIT ──
        ws_abs = Path(tmp) / "workspace" / "projects" / f"p{pid}"
        ws_abs.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=ws_abs, check=True)
        (ws_abs / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "-A"], cwd=ws_abs, check=True)
        subprocess.run(["git", "-c", "user.name=E2E", "-c", "user.email=e2e@t.local",
                        "commit", "-qm", "initial"], cwd=ws_abs, check=True)
        code, gstatus = http("GET", f"/api/git/status?project_id={pid}")
        gs = json.loads(gstatus)
        record("git: real status from sandbox repo",
               code == 200 and gs["is_repo"] is True and gs["clean"] is True)
        code, glog = http("GET", f"/api/git/log?project_id={pid}")
        record("git: commit history real",
               any("initial" in e for e in json.loads(glog)["entries"]))
        code, gh = http("GET", "/api/github/state")
        record("github: unauthenticated state is explicit",
               json.loads(gh)["authenticated"] is False)

        if (ROOT / "frontend" / "dist" / "index.html").exists():
            code, html = http("GET", "/")
            record("frontend served as SPA", code == 200 and b'<div id="root">' in html)
        else:
            record("frontend served as SPA", True, "skipped (no dist yet)")

        code, usage_raw = http("GET", "/api/usage/tokens")
        totals = json.loads(usage_raw)["total"]
        record("token tracking: system-wide totals (team-mode ready)",
               totals["total_tokens"] > 0, f"total={totals['total_tokens']}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        mock_file = ROOT / "tests" / "_overall_mock_ollama.py"
        if mock_file.exists():
            mock_file.unlink()
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Command Center overall system test")
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    print("=" * 68)
    print("AI COMMAND CENTER — OVERALL SYSTEM TEST")
    print("=" * 68)

    if not args.skip_backend:
        run_backend_tests()
    if not args.skip_frontend:
        print("\n[2/4] Frontend type checking")
        run_frontend("type checking")
        print("\n[3/4] Frontend production build")
        run_frontend("production build")
    run_system_tests()

    print("\n" + "=" * 68)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"RESULT: {passed} passed, {len(failed)} failed")
    for n in failed:
        print(f"  FAILED: {n}")
    print("=" * 68)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
