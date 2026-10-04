from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from agent_arena import batch
from agent_arena.errors import ArenaError
from agent_arena.run_mode import parse_task, resolve_repo

FAKE_CLAUDE = """#!{python}
import json, sys, pathlib
sys.stdin.read()
step = pathlib.Path("step.txt")
step.write_text((step.read_text() if step.exists() else "") + "x")
u = {{"input_tokens": 2, "cache_read_input_tokens": 10,
     "cache_creation_input_tokens": 5, "output_tokens": 3}}
events = [
    {{"type": "assistant", "message": {{"id": "m1", "model": "claude-sonnet-5", "usage": u}}}},
    {{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
     "usage": u, "result": "ok"}},
]
for e in events:
    print(json.dumps(e))
"""


def _task(path: Path, check: str, repo: str | None = None) -> Path:
    pin = f'repo = "{repo}"\n' if repo else ""
    path.write_text(
        f'+++\nagent = "claude"\nmodel = "claude-sonnet-5"\nallowed_tools = ["Read"]\n{pin}'
        f"max_turns = 2\ntimeout_seconds = 30\n[[checks]]\nargv = {json.dumps(check.split())}\n"
        "+++\nDo a step.\n"
    )
    return path


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(batch, "STOP_FILE", home / ".agent-arena" / "STOP")
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    return {"repo": repo, "tasks": tasks, "home": home}


def test_queue_runs_each_step_as_its_own_run_and_stops_on_failure(
    env: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    t = env["tasks"]
    _task(t / "a.md", "test -f step.txt")
    _task(t / "b.md", "false")  # this step fails
    _task(t / "c.md", "true")
    (t / "q.queue.toml").write_text(
        f'name = "demo"\nrepo = "{env["repo"]}"\ntasks = ["a", "b", "c"]\n'
    )
    code = batch.main([str(t / "q.queue.toml")])
    out = capsys.readouterr().out
    assert code == 1
    assert "STEP 1/3: a" in out and "STEP 2/3: b" in out and "STEP 3/3" not in out
    assert "stopping the queue here" in out and "not run" in out
    runs = list((env["home"] / ".agent-arena" / "runs").glob("*/result.json"))
    assert len(runs) == 2  # one benchmarked run per step that ran
    (journal,) = list((env["home"] / ".agent-arena" / "journals").glob("demo-*.md"))
    text = journal.read_text()
    assert "## Step 1/3: a" in text and "## Step 2/3: b" in text
    assert "- PASS: " in text and "- FAIL: " in text and "run record:" in text
    assert "Agent's own summary" in text and "new: step.txt" in text
    assert "Queue finished" in text and "1/3 steps verified" in text


def test_keep_going_and_stop_file(env: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    t = env["tasks"]
    for name, check in [("a", "false"), ("b", "true")]:
        _task(t / f"{name}.md", check)
    code = batch.main(
        [str(t / "a.md"), str(t / "b.md"), "--repo", str(env["repo"]), "--keep-going"]
    )
    assert code == 1 and "STEP 2/2: b" in capsys.readouterr().out
    batch.STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    batch.STOP_FILE.write_text("")
    batch.main([str(t / "b.md"), "--repo", str(env["repo"])])
    assert "STOP file found" in capsys.readouterr().out


def test_pinned_repo_is_enforced(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    spec = parse_task(_task(tmp_path / "p.md", "true", str(pinned)))
    assert resolve_repo(spec, None) == pinned
    assert resolve_repo(spec, pinned) == pinned
    with pytest.raises(ArenaError, match="pinned"):
        resolve_repo(spec, other)
    free = parse_task(_task(tmp_path / "f.md", "true"))
    with pytest.raises(ArenaError, match="no repo"):
        resolve_repo(free, None)


def test_bad_queue_files(tmp_path: Path) -> None:
    (tmp_path / "x.queue.toml").write_text('tasks = ["missing"]\n')
    with pytest.raises(ArenaError, match="task not found"):
        batch.load_queue(tmp_path / "x.queue.toml")
    (tmp_path / "y.queue.toml").write_text("tasks = []\nsurprise = 1\n")
    with pytest.raises(ArenaError):
        batch.load_queue(tmp_path / "y.queue.toml")


def test_shipped_queue_and_tasks_parse() -> None:
    root = Path(__file__).resolve().parents[1] / "tasks"
    queue = batch.load_queue(root / "physics-v3.queue.toml")
    assert len(queue.tasks) == 6 and queue.stop_on_failure
    for task in queue.tasks:
        spec = parse_task(task)
        assert spec.repo is not None and spec.agent == "codex" and spec.effort == "medium"
        assert spec.checks[0].name == "V1/V2 frozen files unchanged"
