from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent_arena import gui
from agent_arena.errors import ArenaError
from agent_arena.run_mode import parse_task

GOOD = (
    '+++\nagent = "claude"\nmodel = "claude-sonnet-5"\nallowed_tools = ["Read"]\n'
    "max_turns = 3\ntimeout_seconds = 60\n"
    '[[checks]]\nname = "ok"\nargv = ["true"]\n+++\n\nSay hello.\n'
)


def _tasks(tmp_path: Path) -> Path:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "hello.md").write_text(GOOD)
    (tasks / "broken.md").write_text("no front matter")
    return tasks


def test_list_tasks_reports_valid_and_broken(tmp_path: Path) -> None:
    listed = {t["name"]: t for t in gui.list_tasks(_tasks(tmp_path))}
    assert listed["hello"]["model"] == "claude-sonnet-5" and listed["hello"]["checks"] == ["ok"]
    assert listed["hello"]["summary"] == "Say hello."
    assert "error" in listed["broken"]


def test_build_command_validates_everything(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    argv = gui.build_command("run", "hello", str(repo), "gpt-6-sol", "high", tasks)
    assert argv[:4] == [sys.executable, "-m", "agent_arena", "run"]
    assert argv[-4:] == ["--model", "gpt-6-sol", "--effort", "high"]
    assert gui.display(argv).startswith("agent-arena run ")
    switched = gui.build_command("run", "hello", str(repo), "gpt-6-sol", "", tasks, agent="codex")
    assert switched[-4:] == ["--agent", "codex", "--model", "gpt-6-sol"]
    with pytest.raises(ArenaError):
        gui.build_command("run", "hello", str(repo), "", "", tasks, agent="gemini")
    grade = gui.build_command("grade", "hello", str(repo), tasks_dir=tasks)
    assert grade[1:4] == ["-m", "agent_arena.ledger", "grade"]
    bad = [
        ("run", "../etc", str(repo), "", ""),
        ("run", "missing", str(repo), "", ""),
        ("run", "hello", str(Path.home()), "", ""),
        ("run", "hello", str(tmp_path / "nope"), "", ""),
        ("run", "hello", str(repo), "x; rm -rf /", ""),
        ("run", "hello", str(repo), "", "turbo"),
        ("delete", "hello", str(repo), "", ""),
    ]
    for args in bad:
        with pytest.raises(ArenaError):
            gui.build_command(*args, tasks_dir=tasks)


def test_new_task_is_valid_and_never_overwrites(tmp_path: Path) -> None:
    tasks = _tasks(tmp_path)
    form = {
        "name": "fix-bug",
        "agent": "claude",
        "model": "claude-sonnet-5",
        "allowed_tools": "Read, Edit, Bash(python3 -m pytest:*)",
        "max_turns": 20,
        "timeout_seconds": 600,
        "effort": "high",
        "checks": [{"command": "python3 -m pytest -q"}, {"command": ""}],
        "instructions": "Fix the failing test in parser.py.",
    }
    path = gui.write_new_task(form, tasks)
    spec = parse_task(path)
    assert spec.checks[0].argv == ("python3", "-m", "pytest", "-q") and len(spec.checks) == 1
    assert spec.effort == "high" and "Bash(python3 -m pytest:*)" in spec.allowed_tools
    with pytest.raises(ArenaError, match="already exists"):
        gui.write_new_task(form, tasks)
    with pytest.raises(ArenaError, match="at least one check"):
        gui.write_new_task({**form, "name": "other", "checks": []}, tasks)
    with pytest.raises(ArenaError):
        gui.write_new_task({**form, "name": "Bad Name"}, tasks)
    assert not list(tasks.glob("*.tmp"))


def test_job_runs_and_captures_output(tmp_path: Path) -> None:
    jobs = gui.JobManager(tmp_path / "jobs")
    job = jobs.start([sys.executable, "-c", "print('hi from job')"], "demo", cwd=tmp_path)
    for _ in range(100):
        if job.process.poll() is not None:
            break
        time.sleep(0.05)
    status = job.status()
    assert status["exit_code"] == 0 and "hi from job" in status["log_tail"]
    assert status["log_tail"].startswith("$ demo")


def _serve() -> tuple[str, object]:
    server = gui.make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def _req(url: str, data: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_http_guards(tmp_path: Path) -> None:
    base, server = _serve()
    try:
        status, state = _req(base + "/api/state")
        assert status == 200 and "tasks" in state and "models" in state
        assert _req(base + "/api/state", headers={"Host": "evil.example"})[0] == 403
        assert _req(base + "/api/preview", {"task": "live-hello", "repo": str(tmp_path)})[0] == 403
        repo = tmp_path / "r"
        repo.mkdir()
        status, out = _req(
            base + "/api/preview", {"task": "live-hello", "repo": str(repo)}, {"X-Arena": "1"}
        )
        assert status == 200 and out["command"].startswith("agent-arena run ")
        status, out = _req(
            base + "/api/preview", {"task": "live-hello", "repo": "/"}, {"X-Arena": "1"}
        )
        assert status == 400 and "project folder" in out["error"]
    finally:
        server.shutdown()  # type: ignore[attr-defined]


def test_second_launch_reuses_running_panel_and_skips_busy_ports(capsys) -> None:  # type: ignore[no-untyped-def]
    import socket

    base, server = _serve()
    port = int(base.rsplit(":", 1)[1])
    try:
        assert gui.main(["--port", str(port), "--no-browser"]) == 0
        assert "already running" in capsys.readouterr().out
    finally:
        server.shutdown()  # type: ignore[attr-defined]
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    busy = blocker.getsockname()[1]
    try:
        assert not gui._is_our_panel(busy)
        srv = None
        for p in range(busy, busy + 20):
            try:
                srv = gui.make_server(p)
                break
            except OSError:
                continue
        assert srv is not None and srv.server_address[1] != busy
        srv.server_close()
    finally:
        blocker.close()
