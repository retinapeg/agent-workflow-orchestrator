from __future__ import annotations

import contextlib
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from agent_arena.control import activation
from agent_arena.control.activation import (
    ActivationError,
    activate,
    prune_releases,
    release_dir,
    write_plist,
)
from agent_arena.control.store import Store, encode

SHA = "0123456789abcdef0123456789abcdef01234567"
LABEL = "local.agent-arena.control.test00000000"


class Response:
    def __init__(self, status, body=None, html=False):
        self.status_code = status
        self.body = body if body is not None else {}
        self.headers = {"content-type": "text/html" if html else "application/json"}

    def json(self):
        return self.body


class Harness:
    """Fake launchd/git/npm/HTTP around a real state directory and SQLite ledger."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.store = Store(self.home)
        self.repo = tmp_path / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").write_text("")
        (self.repo / "web/node_modules").mkdir(parents=True)
        self.launch_agents = tmp_path / "LaunchAgents"
        self.release = release_dir(self.repo, SHA)
        self.port = 8791
        self.origin = "https://mac.example.ts.net"
        self.calls = []
        self.live = str(self.repo)  # WorkingDirectory of the plist launchd last loaded
        self.failing = set()  # (check, working_directory) pairs that fail
        self.probe_seen = []
        self.now = 1000.0
        (self.home / "control.plist").write_bytes(
            plistlib.dumps(
                {
                    "Label": LABEL,
                    "ProgramArguments": ["/usr/bin/caffeinate", "-i", "python"],
                    "WorkingDirectory": str(self.repo),
                    "EnvironmentVariables": {"PATH": "/usr/bin"},
                }
            )
        )
        self.children = []  # one fake controller process per launchctl bootstrap, like launchd
        # Stale identity: psutil cannot match create_time 0, so no previous process is known.
        (self.home / "server.json").write_text(json.dumps({"pid": os.getpid(), "created": 0.0}))

    def close(self):
        for child in self.children:
            child.kill()
            child.wait()

    # -- fakes ---------------------------------------------------------------------
    def run(self, argv, **kw):
        if argv[:2] == ["launchctl", "print"]:
            # The label is never listed in this harness: teardown is instantaneous.
            return SimpleNamespace(returncode=113, stdout="", stderr="not found")
        self.calls.append(argv)
        stdout = ""
        if argv[:2] == ["git", "-C"] and argv[3:5] == ["worktree", "add"]:
            target = Path(argv[6])
            (target / "src/agent_arena/control").mkdir(parents=True)
            (target / "src/agent_arena/control/api.py").write_text("")
            (target / "web").mkdir()
        elif argv[0] == "npm":
            dist = Path(argv[4]) / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text("<html></html>")
        elif argv[1] == "-c":
            assert kw["env"]["PYTHONPATH"] == str(self.release / "src")
            stdout = str(self.release / "src/agent_arena/control/api.py") + "\n"
        elif argv[:2] == ["launchctl", "bootout"]:
            self.close()
        elif argv[:2] == ["launchctl", "bootstrap"]:
            plist = plistlib.loads(Path(argv[3]).read_bytes())
            self.live = plist["WorkingDirectory"]
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            self.children.append(child)
            created = psutil.Process(child.pid).create_time()
            (self.home / "server.json").write_text(
                json.dumps({"pid": child.pid, "created": created, "port": self.port})
            )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    def fails(self, check):
        return (check, self.live) in self.failing

    def get(self, url, timeout=None, follow_redirects=None, cookies=None):
        assert url.startswith(self.origin + "/")
        path = url[len(self.origin) :]
        if path == "/":
            return Response(500 if self.fails("ui") else 200, html=True)
        if path == "/api/v1/health":
            if self.fails("scheduler"):
                return Response(503, {"scheduler_ready": False, "heartbeat_age_seconds": 99})
            return Response(200, {"scheduler_ready": True, "heartbeat_age_seconds": 0.2})
        if path == "/api/v1/overview":
            token = (cookies or {}).get("arena_session")
            if not token:
                return Response(401, {"detail": "Pair this device on the host first"})
            with contextlib.closing(self.store.connect()) as db:
                row = db.execute(
                    "SELECT id FROM sessions WHERE hash=? AND expires>?",
                    (hashlib.sha256(token.encode()).hexdigest(), time.time()),
                ).fetchone()
            self.probe_seen.append(row["id"] if row else None)
            if not row:
                return Response(401)
            if self.fails("history"):
                return Response(500, {"detail": "boom"})
            return Response(200, {"projects": [{"id": "p1"}], "tasks": [], "events": []})
        raise AssertionError(url)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        # The scheduler thread of whichever controller is live keeps the heartbeat moving.
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)", (encode(self.now),))

    # -- helpers --------------------------------------------------------------------
    def activate(self, sha=SHA):
        return activate(
            self.home,
            self.repo,
            sha,
            port=self.port,
            origin=self.origin,
            run=self.run,
            http=self,
            sleep=self.sleep,
            clock=self.clock,
            launch_agents=self.launch_agents,
            timeout=30,
        )

    def launchctl(self):
        return [c for c in self.calls if c[0] == "launchctl"]

    def events(self, prefix="activation."):
        with contextlib.closing(self.store.connect()) as db:
            rows = db.execute("SELECT kind, data FROM events ORDER BY id").fetchall()
        return [(r["kind"], json.loads(r["data"])) for r in rows if r["kind"].startswith(prefix)]

    def releases(self):
        return json.loads((self.home / "releases.json").read_text())


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    yield h
    h.close()


def test_plist_targets_release_and_keeps_label(harness):
    h = harness
    path = write_plist(
        h.home, h.repo, h.release, h.port, h.origin, activation.service_label(h.home), "/py"
    )
    assert path == h.home / "control.plist.next"
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == LABEL
    assert data["WorkingDirectory"] == str(h.release)
    assert data["EnvironmentVariables"]["PYTHONPATH"] == str(h.release / "src")
    assert "PATH" in data["EnvironmentVariables"]
    assert data["ProgramArguments"] == [
        "/usr/bin/caffeinate",
        "-i",
        "/py",
        "-m",
        "agent_arena.control",
        "--home",
        str(h.home),
        "serve",
        "--port",
        "8791",
        "--origin",
        h.origin,
    ]
    assert data["KeepAlive"] is True and data["RunAtLoad"] is True
    assert data["ThrottleInterval"] == 10
    assert data["StandardOutPath"] == data["StandardErrorPath"] == str(h.home / "service.log")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    # Without a control.plist the label is derived from the state directory, as install does.
    (h.home / "control.plist").unlink()
    expected = "local.agent-arena.control." + hashlib.sha256(str(h.home).encode()).hexdigest()[:12]
    assert activation.service_label(h.home) == expected


def test_successful_activation_swaps_service_in_order(harness):
    h = harness
    result = h.activate()
    assert result["result"] == "activated" and result["rolled_back"] is False
    assert result["health"]["history"] == {"projects": 1, "tasks": 0}
    assert set(result["health"]) == {"process", "ui", "scheduler", "auth", "history", "heartbeat"}
    # Worktree, UI build and import check happen before launchd is touched.
    kinds = [
        "worktree" if c[0] == "git" else c[0] if c[0] != "launchctl" else c[1] for c in h.calls
    ]
    assert kinds[:3] == ["worktree", "npm", str(h.repo / ".venv/bin/python")]
    assert h.calls[0][3:] == ["worktree", "add", "--detach", str(h.release), SHA]
    assert kinds[3:5] == ["bootout", "bootstrap"]
    uid = os.getuid()
    assert h.launchctl() == [
        ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(h.home / "control.plist")],
    ]
    # Canonical checkout untouched; node_modules shared with the release.
    assert (h.release / "web/node_modules").is_symlink()
    assert (h.release / "web/node_modules").resolve() == (h.repo / "web/node_modules").resolve()
    live = plistlib.loads((h.home / "control.plist").read_bytes())
    assert live["WorkingDirectory"] == str(h.release) and live["Label"] == LABEL
    previous = plistlib.loads((h.home / "control.plist.previous").read_bytes())
    assert previous["WorkingDirectory"] == str(h.repo)
    assert not (h.home / "control.plist.next").exists()
    link = h.launch_agents / (LABEL + ".plist")
    assert link.is_symlink() and link.resolve() == (h.home / "control.plist").resolve()
    releases = h.releases()
    assert releases["current"] == SHA and releases["previous"] is None
    assert [e["result"] for e in releases["history"]] == ["activated"]
    assert [k for k, _ in h.events()] == ["activation.started", "activation.succeeded"]
    assert h.events()[-1][1]["sha"] == SHA
    # Probe session cleaned up.
    with contextlib.closing(h.store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_health_failure_rolls_back_to_previous_release(harness):
    h = harness
    h.failing.add(("scheduler", str(h.release)))
    result = h.activate()
    assert result["result"] == "rolled_back" and result["rolled_back"] is True
    assert "scheduler" in result["reason"]
    uid = os.getuid()
    assert h.launchctl() == [
        ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(h.home / "control.plist")],
        ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(h.home / "control.plist")],
    ]
    live = plistlib.loads((h.home / "control.plist").read_bytes())
    assert live["WorkingDirectory"] == str(h.repo)
    assert h.live == str(h.repo)
    releases = h.releases()
    assert releases["current"] is None  # never moved to the failed release
    entry = releases["history"][-1]
    assert entry["sha"] == SHA and entry["result"] == "rolled_back"
    assert "scheduler" in entry["reason"] and "passed: process, ui" in entry["reason"]
    kinds = [k for k, _ in h.events()]
    assert kinds == ["activation.started", "activation.failed", "activation.rolled_back"]
    assert h.events()[-1][1] == {"schema": 1, "sha": SHA, "reason": entry["reason"]}
    assert result["health"]["heartbeat"] is not None


def test_probe_session_exists_during_check_and_is_removed_after_failure(harness):
    h = harness
    h.failing.add(("history", str(h.release)))
    result = h.activate()
    assert result["rolled_back"] is True and "history" in result["reason"]
    # The overview probe saw the session row on the failed release and again on the restored one.
    assert h.probe_seen and all(seen == "activation-probe" for seen in h.probe_seen)
    assert len(h.probe_seen) >= 2
    with contextlib.closing(h.store.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_rollback_failure_raises_and_records(harness):
    h = harness
    h.failing.add(("ui", str(h.release)))
    h.failing.add(("ui", str(h.repo)))
    with pytest.raises(ActivationError, match="rollback did not verify"):
        h.activate()
    entry = h.releases()["history"][-1]
    assert entry["result"] == "failed" and "rollback failed" in entry["reason"]
    assert [k for k, _ in h.events()] == ["activation.started", "activation.failed"]


def test_prepare_failure_never_touches_launchd(harness):
    h = harness
    original = h.run

    def run(argv, **kw):
        if argv[0] == "npm":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="tsc: type error")
        return original(argv, **kw)

    h.run = run
    with pytest.raises(ActivationError, match="Building the UI.*type error"):
        h.activate()
    assert h.launchctl() == []
    assert not (h.home / "control.plist.previous").exists()
    assert h.releases()["history"][-1]["result"] == "failed"
    assert [k for k, _ in h.events()] == ["activation.started", "activation.failed"]


def test_prune_keeps_current_previous_and_three_newest(harness):
    h = harness
    root = h.repo / ".arena-releases"
    current, previous = "c" * 40, "b" * 40
    names = ["old1" * 3, "old2" * 3, "old3" * 3, "new1" * 3, "new2" * 3, "new3" * 3]
    order = [current[:12], previous[:12], *names]
    for n, name in enumerate(order):
        path = root / name
        path.mkdir(parents=True)
        os.utime(path, (1000 + n, 1000 + n))
    removed = prune_releases(h.repo, {current, previous}, run=h.run)
    assert sorted(removed) == sorted(n for n in names if n.startswith("old"))
    removes = [c for c in h.calls if c[0] == "git" and c[3:5] == ["worktree", "remove"]]
    assert all(c[6].startswith(str(root)) for c in removes)
    assert not any(Path(c[6]).name in {current[:12], previous[:12]} for c in removes)
    assert [c[3:] for c in h.calls][-1] == ["worktree", "prune"]
    # With the same protection a second pass removes nothing further.
    h.calls.clear()
    for name in removed:
        (root / name).rmdir()
    assert prune_releases(h.repo, {current, previous}, run=h.run) == []
    assert h.calls == []


def test_release_dir_rejects_non_hashes(harness):
    with pytest.raises(ActivationError, match="Not a commit hash"):
        release_dir(harness.repo, "main; rm -rf /")
    assert release_dir(harness.repo, SHA) == harness.repo / ".arena-releases" / SHA[:12]


def test_cli_status_and_origin_defaults(harness, capsys):
    h = harness
    (h.home / "connection.json").write_text(json.dumps({"origin": h.origin, "port": 8791}))
    assert activation.default_origin(h.home, 8791) == h.origin
    (h.home / "connection.json").unlink()
    assert activation.default_origin(h.home, 8791) == "http://127.0.0.1:8791"
    assert activation.main(["--home", str(h.home), "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"current": None, "previous": None, "history": []}


def test_swap_waits_for_launchd_teardown_and_retries_bootstrap(tmp_path):
    from types import SimpleNamespace

    from agent_arena.control.activation import _swap

    home = tmp_path / "home"
    home.mkdir()
    source = home / "control.plist.next"
    source.write_text("<plist/>")
    calls = []
    state = {"prints": 0, "bootstraps": 0}

    def run(argv, **kwargs):
        calls.append(argv[:2])
        if argv[1] == "bootout":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "print":
            state["prints"] += 1  # Still listed for the first two polls, then gone.
            return SimpleNamespace(
                returncode=0 if state["prints"] <= 2 else 113, stdout="", stderr=""
            )
        if argv[1] == "bootstrap":
            state["bootstraps"] += 1
            if state["bootstraps"] < 3:
                return SimpleNamespace(
                    returncode=5, stdout="", stderr="Bootstrap failed: 5: Input/output error"
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(argv)

    clock = {"t": 0.0}
    _swap(
        run,
        home,
        "label.test",
        source,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        clock=lambda: clock["t"],
    )
    assert state["prints"] == 3 and state["bootstraps"] == 3
    assert (home / "control.plist").read_text() == "<plist/>"
    assert [c[1] for c in calls] == [
        "bootout",
        "print",
        "print",
        "print",
        "bootstrap",
        "bootstrap",
        "bootstrap",
    ]


def test_activation_plist_uses_absolute_state_directory(tmp_path, monkeypatch):
    from agent_arena.control.activation import write_plist

    monkeypatch.chdir(tmp_path)
    (tmp_path / "home").mkdir()
    release = tmp_path / "rel"
    release.mkdir()
    plist = write_plist(
        Path("home"), tmp_path, release, 8791, "http://127.0.0.1:8791", "label.test", "/py"
    )
    data = plistlib.loads(plist.read_bytes())
    home_arg = data["ProgramArguments"][data["ProgramArguments"].index("--home") + 1]
    assert Path(home_arg).is_absolute() and Path(home_arg) == (tmp_path / "home").resolve()
    assert data["StandardOutPath"].startswith(str(tmp_path.resolve()))
