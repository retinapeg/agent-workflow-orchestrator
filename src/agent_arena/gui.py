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
import shutil
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
                "repo": str(spec.repo) if spec.repo else None,
                "kind": "task",
            }
        )
    return out


def list_queues(tasks_dir: Path = TASKS_DIR) -> list[dict[str, Any]]:
    from .batch import load_queue

    out: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*.queue.toml")):
        name = "queue:" + path.name.removesuffix(".queue.toml")
        try:
            queue = load_queue(path)
        except ArenaError as exc:
            out.append({"name": name, "kind": "queue", "error": str(exc)})
            continue
        steps = []
        for task in queue.tasks:
            try:
                spec = parse_task(task)
                steps.append(f"{task.stem} ({spec.agent} · {spec.model})")
            except ArenaError as exc:
                steps.append(f"{task.stem}: INVALID ({exc})")
        out.append(
            {
                "name": name,
                "kind": "queue",
                "summary": queue.description,
                "steps": steps,
                "repo": str(queue.repo) if queue.repo else None,
                "stop_on_failure": queue.stop_on_failure,
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


_STEP = re.compile(r"^STEP (\d+)/(\d+): (\S+)", re.M)
_QUEUE = re.compile(r"^QUEUE (\S+): (\d+) steps(?: in (.+))?$", re.M)


def parse_queue_log(text: str) -> dict[str, Any] | None:
    """Turn a queue's console log into a board: one card per step with its live status."""

    head = _QUEUE.search(text)
    if not head:
        return None
    total = int(head.group(2))
    starts = list(_STEP.finditer(text))
    finished = "QUEUE SUMMARY" in text
    steps: list[dict[str, Any]] = []
    for i, match in enumerate(starts):
        chunk = text[match.end() : starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        verdict = re.search(r"=> (\d+) PASS, (\d+) FAIL, (\d+) UNKNOWN; verified=(\w+)", chunk)
        cost = re.search(r"^  total:\s+(\S+)", chunk, re.M)
        wall = re.search(r"wall=([\d.]+)s", chunk)
        if verdict:
            status = "verified" if verdict.group(4) == "True" else "failed"
        elif "agent_arena run:" in chunk and "error" in chunk.lower():
            status = "failed"
        else:
            status = "stopped" if finished else "running"
        steps.append(
            {
                "name": match.group(3),
                "status": status,
                "checks": f"{verdict.group(1)}/{sum(int(verdict.group(k)) for k in (1, 2, 3))}"
                if verdict
                else None,
                "cost": cost.group(1) if cost else None,
                "wall_s": float(wall.group(1)) if wall else None,
            }
        )
    for n in range(len(steps), total):
        steps.append(
            {
                "name": f"step {n + 1}",
                "status": "not run" if finished else "queued",
                "checks": None,
                "cost": None,
                "wall_s": None,
            }
        )
    return {"queue": head.group(1), "repo": head.group(3), "finished": finished, "steps": steps}


def queue_board(home: Path = HOME) -> list[dict[str, Any]]:
    boards = []
    logs = list(home.glob("overnight-*.log")) + list((home / "gui-jobs").glob("*.log"))
    for log in sorted(logs, key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
        try:
            board = parse_queue_log(log.read_text("utf-8", errors="replace"))
        except OSError:
            continue
        if board:
            board["log"] = log.name
            board["updated_s_ago"] = round(time.time() - log.stat().st_mtime)
            boards.append(board)
    return boards


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
    tier: str = "",
) -> list[str]:
    """The exact CLI command for an action. The panel only ever runs what this returns."""

    if task.startswith("queue:"):
        if action != "run":
            raise ArenaError("grading works on single tasks, not queues")
        qname = task.removeprefix("queue:")
        if not _NAME.match(qname) or not (tasks_dir / f"{qname}.queue.toml").is_file():
            raise ArenaError(f"queue not found: {qname}")
        from .batch import load_queue

        queue_path = tasks_dir / f"{qname}.queue.toml"
        pinned = load_queue(queue_path).repo
        argv = [sys.executable, "-m", "agent_arena", "queue", str(queue_path)]
    else:
        task_path = _task(task, tasks_dir)
        pinned = parse_task(task_path).repo
        argv = [sys.executable, "-m", "agent_arena", "run", str(task_path)]
    if repo.strip():
        repo_path = _repo(repo)
        if pinned is not None and pinned.resolve() != repo_path:
            raise ArenaError(f"this task is pinned to {pinned}; clear the repo box or use that")
    elif pinned is not None:
        repo_path = _repo(str(pinned))
    else:
        raise ArenaError("pick a repo: the folder the agent will edit")
    if action == "run":
        argv += ["--repo", str(repo_path)]
        if agent:
            if agent not in ("claude", "codex"):
                raise ArenaError(f"invalid agent: {agent!r}")
            argv += ["--agent", agent]
        if model:
            if not re.match(r"^[A-Za-z0-9._-]{1,64}$", model):
                raise ArenaError(f"invalid model name: {model!r}")
            argv += ["--model", model]
        if tier:
            if tier not in ("standard", "fast", "ultrafast"):
                raise ArenaError(f"invalid speed tier: {tier!r}")
            argv += ["--service-tier", tier]
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
            argv[4],
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
                    "tasks": list_tasks() + list_queues(),
                    "models": model_choices(),
                    "efforts": EFFORTS,
                    "recent_repos": load_state().get("recent_repos", []),
                    "runs": recent_runs(),
                    "jobs": self.jobs.list(),
                    "pricing_file": str(default_pricing_path()),
                    "pricing_found": default_pricing_path().is_file(),
                }
            )
        if self.path == "/api/board":
            return self._json({"queues": queue_board()})
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
                    tier=body.get("tier", ""),
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


_MAC_APP_BROWSERS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc")
_LINUX_APP_BROWSERS = ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")


def window_command(url: str, platform: str = sys.platform) -> list[str] | None:
    """Command that opens the panel as its own desktop window (no tabs or address bar), using a
    Chromium browser's app mode. None means fall back to a normal browser tab."""

    if platform == "darwin":
        for app in _MAC_APP_BROWSERS:
            if Path(f"/Applications/{app}.app").exists():
                return ["open", "-na", app, "--args", f"--app={url}", "--window-size=1280,900"]
        return None
    if platform.startswith("linux"):
        for exe in _LINUX_APP_BROWSERS:
            found = shutil.which(exe)
            if found:
                return [found, f"--app={url}", "--window-size=1280,900"]
    return None


def open_panel(url: str, browser_tab: bool = False) -> None:
    command = None if browser_tab else window_command(url)
    if command:
        with contextlib.suppress(OSError):
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-arena gui")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true", help="don't open anything")
    parser.add_argument(
        "--tab", action="store_true", help="open in a normal browser tab instead of a window"
    )
    args = parser.parse_args(argv)
    for port in range(args.port, args.port + 20):
        if _is_our_panel(port):
            # Already open (e.g. the launcher was double-clicked twice): just show it.
            url = f"http://127.0.0.1:{port}/"
            print(f"agent-arena control panel is already running: {url}")
            if not args.no_browser:
                open_panel(url, args.tab)
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
        threading.Timer(0.5, open_panel, args=(url, args.tab)).start()
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


PAGE = (Path(__file__).with_name("gui.html")).read_text("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
