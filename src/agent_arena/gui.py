"""Local control panel for agent_arena: pick a task, repo, model and effort, press Run.

    agent-arena gui            # or: python -m agent_arena.gui

Standard library only, so it runs on any laptop with Python 3.11+. It never reimplements the
harness: every action builds and launches the same CLI command you could type (shown on screen),
so the panel and the command line can't drift apart.

Security: binds to 127.0.0.1 only, rejects requests whose Host header isn't localhost (DNS
rebinding), and requires a custom header on every POST (a cross-site form can't set one).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
import tomllib
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .errors import ArenaError
from .pricing import default_pricing_path
from .run_mode import parse_task

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "tasks"
HOME = Path("~/.agent-arena").expanduser()
RUNS_DIR = HOME / "runs"
JOBS_DIR = HOME / "gui-jobs"
STATE_FILE = HOME / "gui.json"
EFFORTS = ["", "low", "medium", "high", "xhigh", "max"]
FALLBACK_MODELS = {
    "claude": ["claude-sonnet-5", "claude-fable-5-1"],
    "codex": ["gpt-6-sol", "gpt-6-astra"],
}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


# --------------------------------------------------------------------------- data


def list_tasks(tasks_dir: Path = TASKS_DIR) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*.md")):
        try:
            spec = parse_task(path)
        except (ArenaError, OSError) as exc:
            out.append({"name": path.stem, "error": str(exc)})  # shown in the panel
            continue
        first = next((ln.strip() for ln in spec.instructions.splitlines() if ln.strip()), "")
        out.append(
            {
                "name": path.stem,
                "agent": spec.agent,
                "model": spec.model,
                "effort": spec.effort,
                "max_turns": spec.max_turns,
                "timeout_seconds": spec.timeout_seconds,
                "checks": [c.name for c in spec.checks],
                "summary": first[:160],
            }
        )
    return out


def model_choices(pricing: Path | None = None) -> dict[str, list[str]]:
    path = pricing or default_pricing_path()
    names: list[str] = []
    with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
        names = list((tomllib.loads(path.read_text("utf-8")).get("models") or {}).keys())
    claude = sorted({*FALLBACK_MODELS["claude"], *(n for n in names if n.startswith("claude"))})
    codex = sorted({*FALLBACK_MODELS["codex"], *(n for n in names if n.startswith("gpt"))})
    return {"claude": claude, "codex": codex}


def recent_runs(runs_dir: Path = RUNS_DIR, limit: int = 20) -> list[dict[str, Any]]:
    from .pricing import format_cost

    rows = []
    for result in sorted(runs_dir.glob("*/result.json"), reverse=True)[:limit]:
        try:
            data = json.loads(result.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        counts = data.get("check_counts") or {}
        cost = (data.get("cost") or {}).get("total")
        inv = data.get("invocation") or {}
        rows.append(
            {
                "run_id": data.get("run_id"),
                "task": Path(str(data.get("task", ""))).stem,
                "arm": data.get("arm", "harness"),
                "verified": data.get("verified"),
                "checks": f"{counts.get('PASS', 0)}/{sum(counts.values()) if counts else 0}",
                "cost": format_cost(cost) if isinstance(cost, dict) else "null",
                "wall_s": inv.get("wall_time_s"),
                "model": ", ".join(inv.get("models_reported") or []) or inv.get("model_declared"),
                "report": str(result.parent / "report.txt"),
            }
        )
    return rows


def ledger_summary() -> str:
    try:
        from .ledger import summary

        return summary(RUNS_DIR) if RUNS_DIR.is_dir() else "no runs yet"
    except Exception as exc:  # the panel must keep working even if the ledger can't
        return f"ledger unavailable: {exc}"


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def remember_repo(repo: str) -> None:
    state = load_state()
    repos = [repo] + [r for r in state.get("recent_repos", []) if r != repo]
    state["recent_repos"] = repos[:8]
    HOME.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), "utf-8")


# --------------------------------------------------------------------------- commands


def _repo(value: str) -> Path:
    repo = Path(value).expanduser().resolve()
    if not repo.is_dir():
        raise ArenaError(f"repo folder not found: {repo}")
    if repo == Path(repo.anchor) or repo == Path.home().resolve():
        raise ArenaError("pick a project folder, not / or your home folder")
    return repo


def _task(name: str, tasks_dir: Path = TASKS_DIR) -> Path:
    if not _NAME.match(name):
        raise ArenaError(f"invalid task name: {name!r}")
    path = tasks_dir / f"{name}.md"
    if not path.is_file():
        raise ArenaError(f"task not found: {name}")
    return path


def build_command(
    action: str,
    task: str,
    repo: str,
    model: str = "",
    effort: str = "",
    tasks_dir: Path = TASKS_DIR,
    agent: str = "",
) -> list[str]:
    """The exact CLI command for an action. The panel only ever runs what this returns."""

    task_path, repo_path = _task(task, tasks_dir), _repo(repo)
    if action == "run":
        argv = [sys.executable, "-m", "agent_arena", "run", str(task_path)]
        argv += ["--repo", str(repo_path)]
        if agent:
            if agent not in ("claude", "codex"):
                raise ArenaError(f"invalid agent: {agent!r}")
            argv += ["--agent", agent]
        if model:
            if not re.match(r"^[A-Za-z0-9._-]{1,64}$", model):
                raise ArenaError(f"invalid model name: {model!r}")
            argv += ["--model", model]
        if effort:
            if effort not in EFFORTS:
                raise ArenaError(f"invalid effort: {effort!r}")
            argv += ["--effort", effort]
        return argv
    if action == "grade":
        return [
            sys.executable,
            "-m",
            "agent_arena.ledger",
            "grade",
            str(task_path),
            "--repo",
            str(repo_path),
        ]
    raise ArenaError(f"unknown action: {action}")


def display(argv: list[str]) -> str:
    shown = ["agent-arena" if a == sys.executable else a for a in argv]
    if shown[:3] == ["agent-arena", "-m", "agent_arena"]:
        shown = ["agent-arena", *shown[3:]]
    elif shown[:3] == ["agent-arena", "-m", "agent_arena.ledger"]:
        shown = ["python -m agent_arena.ledger", *shown[3:]]
    return " ".join(shlex.quote(a) if a != "python -m agent_arena.ledger" else a for a in shown)


def write_new_task(form: dict[str, Any], tasks_dir: Path = TASKS_DIR) -> Path:
    """Create tasks/<name>.md from the form; refuses to overwrite; validates by parsing it."""

    name = str(form.get("name", "")).strip()
    if not _NAME.match(name):
        raise ArenaError("name: lowercase letters, digits and dashes only (e.g. fix-login-bug)")
    path = tasks_dir / f"{name}.md"
    if path.exists():
        raise ArenaError(f"a task called {name} already exists")
    agent = form.get("agent")
    if agent not in ("claude", "codex"):
        raise ArenaError("agent must be claude or codex")
    model = str(form.get("model", "")).strip()
    tools = [t.strip() for t in str(form.get("allowed_tools", "")).split(",") if t.strip()]
    checks = [c for c in form.get("checks", []) if str(c.get("command", "")).strip()]
    if not checks:
        raise ArenaError("add at least one check, e.g. python3 -m pytest -q")
    instructions = str(form.get("instructions", "")).strip()
    if not instructions:
        raise ArenaError("write the instructions: what you want done")
    lines = ["+++", f"agent = {json.dumps(agent)}", f"model = {json.dumps(model)}"]
    if tools:
        lines.append(f"allowed_tools = {json.dumps(tools)}")
    lines += [
        f"max_turns = {int(form.get('max_turns') or 30)}",
        f"timeout_seconds = {int(form.get('timeout_seconds') or 1800)}",
    ]
    if form.get("effort"):
        lines.append(f"effort = {json.dumps(str(form['effort']))}")
    for check in checks:
        argv = shlex.split(str(check["command"]))
        cname = str(check.get("name") or check["command"]).strip()
        lines += ["", "[[checks]]", f"name = {json.dumps(cname)}", f"argv = {json.dumps(argv)}"]
    lines += ["+++", "", instructions, ""]
    tasks_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(lines), "utf-8")
    try:
        parse_task(tmp)
    except ArenaError as exc:
        tmp.unlink()
        raise ArenaError(str(exc).replace(str(tmp), name)) from exc
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- jobs


@dataclass
class Job:
    id: str
    command: str
    log: Path
    process: subprocess.Popen[bytes]
    started: float = field(default_factory=time.time)

    def status(self) -> dict[str, Any]:
        code = self.process.poll()
        try:
            text = self.log.read_text("utf-8", errors="replace")
        except OSError:
            text = ""
        return {
            "id": self.id,
            "command": self.command,
            "running": code is None,
            "exit_code": code,
            "elapsed_s": round(time.time() - self.started, 1),
            "log_tail": text[-20000:],
        }


class JobManager:
    def __init__(self, jobs_dir: Path = JOBS_DIR) -> None:
        self.jobs_dir = jobs_dir
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, argv: list[str], command: str, cwd: Path = ROOT) -> Job:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        job_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)
        log = self.jobs_dir / f"{job_id}.log"
        handle = log.open("wb")
        handle.write(f"$ {command}\n\n".encode())
        handle.flush()
        env = dict(os.environ)  # works even when agent_arena isn't pip-installed
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")])
        )
        process = subprocess.Popen(  # argv list, never a shell
            argv,
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
        handle.close()
        job = Job(job_id, command, log, process)
        with self.lock:
            self.jobs[job_id] = job
        return job

    def stop(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.process.poll() is not None:
            return False
        try:
            if os.name == "posix":
                os.killpg(job.process.pid, signal.SIGTERM)
            else:
                job.process.terminate()
        except ProcessLookupError:
            return False
        return True

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            jobs = list(self.jobs.values())
        return [{k: v for k, v in j.status().items() if k != "log_tail"} for j in jobs[::-1]]


# --------------------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    jobs: JobManager
    port: int

    def log_message(self, *args: Any) -> None:  # keep the terminal quiet
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in {"127.0.0.1", "localhost"}

    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, default=str).encode())

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
        if self.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._json(
                {
                    "tasks": list_tasks(),
                    "models": model_choices(),
                    "efforts": EFFORTS,
                    "recent_repos": load_state().get("recent_repos", []),
                    "runs": recent_runs(),
                    "jobs": self.jobs.list(),
                    "pricing_file": str(default_pricing_path()),
                    "pricing_found": default_pricing_path().is_file(),
                }
            )
        if self.path == "/api/ledger":
            return self._json({"summary": ledger_summary()})
        match = re.fullmatch(r"/api/job/([\w-]+)", self.path)
        if match and match.group(1) in self.jobs.jobs:
            return self._json(self.jobs.jobs[match.group(1)].status())
        match = re.fullmatch(r"/api/report/([\w-]+)", self.path)
        if match:
            report = RUNS_DIR / match.group(1) / "report.txt"
            if report.is_file() and report.resolve().parent.parent == RUNS_DIR.resolve():
                return self._send(200, report.read_bytes(), "text/plain; charset=utf-8")
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._host_ok() or self.headers.get("X-Arena") != "1":
            return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path in ("/api/preview", "/api/run"):
                argv = build_command(
                    body.get("action", "run"),
                    body.get("task", ""),
                    body.get("repo", ""),
                    body.get("model", ""),
                    body.get("effort", ""),
                    agent=body.get("agent", ""),
                )
                command = display(argv)
                if self.path == "/api/preview":
                    return self._json({"command": command})
                remember_repo(str(_repo(body["repo"])))
                job = self.jobs.start(argv, command)
                return self._json({"job": job.id, "command": command})
            if self.path == "/api/task":
                path = write_new_task(body)
                return self._json({"created": path.stem})
            match = re.fullmatch(r"/api/stop/([\w-]+)", self.path)
            if match:
                return self._json({"stopped": self.jobs.stop(match.group(1))})
        except ArenaError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (ValueError, KeyError) as exc:
            return self._json({"error": f"bad request: {exc}"}, HTTPStatus.BAD_REQUEST)
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def make_server(port: int = 8787, jobs: JobManager | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"jobs": jobs or JobManager(), "port": port})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def _is_our_panel(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as resp:
            data = json.loads(resp.read())
            return isinstance(data, dict) and {"tasks", "efforts", "pricing_file"} <= data.keys()
    except Exception:  # nothing there, or something that isn't us
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-arena gui")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    for port in range(args.port, args.port + 20):
        if _is_our_panel(port):
            # Already open (e.g. the launcher was double-clicked twice): just show it.
            url = f"http://127.0.0.1:{port}/"
            print(f"agent-arena control panel is already running: {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        try:
            server = make_server(port)
            break
        except OSError:
            continue  # port taken by something else; try the next one
    else:
        print(f"no free port between {args.port} and {args.port + 19}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"agent-arena control panel: {url}  (Ctrl+C to quit; running jobs keep going)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


PAGE = (Path(__file__).with_name("gui.html")).read_text("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
