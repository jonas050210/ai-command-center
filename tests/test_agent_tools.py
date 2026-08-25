"""Agent mode tests — sandboxed file tools, command runner, engine protocol."""
import pytest

from backend.app.core.errors import BadRequest
from backend.app.tools.files import FileToolbox
from backend.app.tools.runner import CommandRunner


# ── file toolbox ──────────────────────────────────────────────────────
@pytest.fixture
async def toolbox(tmp_path, db):
    root = tmp_path / "ws"
    root.mkdir()
    from backend.app.db.repo import ExecutionsRepo
    return FileToolbox(root, ExecutionsRepo(db))


async def test_write_read_edit_roundtrip(toolbox):
    r = await toolbox.write_file("src/main.py", "print('hi')\n")
    assert r["ok"] and r["bytes"] > 0
    r = await toolbox.read_file("src/main.py")
    assert r["ok"] and "print('hi')" in r["content"]
    r = await toolbox.edit_file("src/main.py", "hi", "hello")
    assert r["ok"] and r["replacements"] == 1
    r = await toolbox.read_file("src/main.py")
    assert "hello" in r["content"]


async def test_path_traversal_blocked(toolbox):
    for evil in ("../../etc/passwd", "/etc/passwd", "..\\..\\windows\\system32\\x",
                 "src/../../../outside"):
        r = await toolbox.read_file(evil)
        assert not r["ok"], evil
        r = await toolbox.write_file(evil, "x")
        assert not r["ok"], evil


async def test_search_and_list(toolbox):
    await toolbox.write_file("README.md", "hello world marker")
    await toolbox.write_file("src/a.py", "marker inside code")
    await toolbox.create_directory("docs")
    r = await toolbox.search_files("marker")
    assert r["ok"] and r["count"] == 2
    r = await toolbox.list_files(".")
    assert r["ok"] and "README.md" in r["tree"] and "src/" in r["tree"]


async def test_delete_file_only_files(toolbox):
    await toolbox.write_file("a.txt", "x")
    assert (await toolbox.delete_file("a.txt"))["ok"]
    assert not (await toolbox.delete_file("missing.txt"))["ok"]


# ── command runner ────────────────────────────────────────────────────
@pytest.fixture
async def runner(tmp_path, db):
    from backend.app.db.repo import ExecutionsRepo
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return CommandRunner(ws, ExecutionsRepo(db))


async def test_runner_allowlist_and_shell_blocked(runner):
    with pytest.raises(BadRequest):
        runner.validate("rm -rf /")
    with pytest.raises(BadRequest):
        runner.validate("bash -c 'echo hi'")
    with pytest.raises(BadRequest):
        runner.validate("python -c 'print(1)'")
    with pytest.raises(BadRequest):
        runner.validate("echo hello; rm -rf /")
    with pytest.raises(BadRequest):
        runner.validate("pytest | tee out.txt")
    with pytest.raises(BadRequest):
        runner.validate("npm install")
    # valid ones
    assert runner.validate("pytest -q")[0].endswith("pytest") or "pytest" in \
        runner.validate("pytest -q")[0]
    assert runner.validate("npm run build")[1] == "run"


async def test_runner_executes_python_script_in_sandbox(runner, tmp_path):
    runner.workspace.mkdir(parents=True, exist_ok=True)
    (runner.workspace / "hello.py").write_text("print('agent-ok')")
    res = await runner.run("python hello.py")
    assert res["ok"] is True
    assert "agent-ok" in res["stdout"]


async def test_runner_timeout_kills(runner):
    runner.timeout = 1.0
    (runner.workspace / "slow.py").write_text("import time\ntime.sleep(30)")
    res = await runner.run("python slow.py", timeout=1.0)
    assert res["timed_out"] is True
    assert "killed" in res["stderr"].lower()


# ── protocol parsing ──────────────────────────────────────────────────
def test_parse_tool_lines():
    from backend.app.agent.engine import parse_tool_lines
    text = (
        "TOOL write_file path=\"src/app.py\"\n"
        "<<<FILE\n"
        "print('x')\n"
        "FILE\n"
        "TOOL run cmd=\"pytest -q\"\n"
    )
    actions = parse_tool_lines(text)
    assert len(actions) == 2
    assert actions[0]["tool"] == "write_file"
    assert actions[0]["path"] == "src/app.py"
    assert actions[0]["content"] == "print('x')"
    assert actions[1]["tool"] == "run"
    assert actions[1]["cmd"] == "pytest -q"


def test_parse_edit_blocks():
    from backend.app.agent.engine import parse_tool_lines
    text = (
        "TOOL edit_file path=\"a.py\"\n"
        "<<<OLD\n"
        "old text\n"
        "OLD\n"
        "<<<NEW\n"
        "new text\n"
        "NEW\n"
        "TOOL done Task finished.\n"
    )
    actions = parse_tool_lines(text)
    assert actions[0]["tool"] == "edit_file"
    assert actions[0]["old"] == "old text"
    assert actions[0]["new"] == "new text"
    assert actions[1]["tool"] == "done"
