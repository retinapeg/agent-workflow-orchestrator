"""What is running on the owner's Mac: a fresh, read-only look at development work.

``discover`` lists the current user's coding agents (Claude Code, Codex, Aider, Gemini,
OpenCode, Goose, Cursor Agent), development servers and test runs, and reconciles them
with Arena's own records:

* **managed** — work Arena launched: the process or an ancestor matches an attempt's
  recorded identity (pid and start time), it runs inside an attempt workspace or the
  director's scratch folder, or it is a director turn of this controller. Attempts Arena
  records as live with no matching process are listed too ("stuck" becomes visible).
* **external** — agents, servers and tests with a working folder outside Arena.
* **unknown** — agents whose working folder macOS withheld or that is not a repository.

It uses psutil only. For every process it reads the owner and state; only for the current
user's processes does it read names, the first arguments (redacted), working folders, start
times and the listening ports of development servers. It never reads environment
variables, sends signals, attaches to or writes anything, and it looks at Claude Code
session files' metadata (modification times), never their contents. Repositories are found
by path first (enrolled projects, repositories seen before); ``.git`` is looked for only
inside the home folder and outside the macOS privacy folders. All file-system reads run on
a worker thread under a deadline, so a hung volume never holds up a look. The process
table, home folder, user and controller pid are injectable so tests use a fake table.
"""

from __future__ import annotations

import contextlib
import os
import queue
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil

from .engine import ACTIVE
from .worker import redact, redact_args

CACHE_SECONDS = 10
DEADLINE_SECONDS = 3.0  # a look never waits longer than this for the file system
LOCK_SECONDS = 2.0  # how long a caller waits for a look another caller is taking
ANCESTRY = 8
IDENTITY_TOLERANCE = 0.01
WORKING_SECONDS = 120  # a session file this fresh means Claude is working right now
RECENT_SECONDS = 600  # subagent session files this fresh count as recent
STARTING_SECONDS = 30  # a just-claimed attempt has no process yet
ARGS_SHOWN = 6
SCAN_LIMIT = 5000
ACTIVITY_LIMIT = 30
KNOWN_REPOS_LIMIT = 500
# Read for every process; the rest (DETAILS) only for the current user's processes, so no
# other user's command line is ever read.
ATTRS = ["pid", "username", "status"]
DETAILS = ["name", "cmdline", "create_time", "ppid"]
GONE = {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
# Folders macOS guards with privacy consent (a LaunchAgent touching them can stall or be
# refused): Arena never stats inside them unless they hold an enrolled project.
PRIVACY_FOLDERS = ("Desktop", "Documents", "Downloads", "Library")
GROUPS = ("managed", "external", "unknown")
GROUP_NAMES = {
    "managed": "managed by Arena",
    "external": "discovered on your Mac",
    "unknown": "could not be verified",
}
AGENT_KINDS = {"claude", "codex", "agent"}
OTHER_AGENTS = {
    "aider": "Aider",
    "gemini": "Gemini CLI",
    "opencode": "OpenCode",
    "goose": "Goose",
    "cursor-agent": "Cursor Agent",
}
PROVIDERS = {"claude": "Claude", "codex": "Codex"}
NODE_TOOLS = {"npm", "pnpm", "yarn", "bun"}
WRAPPERS = {"env", "nohup", "nice", "caffeinate", "time"}
INTERPRETER = re.compile(r"^(?:node|nodejs|bun|deno|pythonw?[\d.]*|ruby|perl)$")
SCRIPT_SUFFIX = re.compile(r"\.(?:m?js|cjs|ts|py)$")
PHASE_VERBS = {
    "plan": "planning",
    "replan": "working out a fix for",
    "direct": "checking the result of",
    "review": "reviewing",
    "build": "building",
}
LIMITATIONS = [
    "Only your own processes were inspected; other users' processes are not visible to Arena.",
    "Subagents inside a Claude Code session are inferred from its session files, not seen as "
    "processes.",
    "Codex shows no activity times, so a running Codex process proves it is open, not that it "
    "is making progress.",
    "Arena only looked: it did not stop, attach to or change anything.",
]
NO_USER = "Arena could not tell which macOS user it runs as, so it inspected no processes."
PRIVACY_NOTE = (
    "macOS privacy folders are not inspected: work in Desktop, Documents, Downloads, Library, "
    "on other volumes or outside your home folder is listed without its repository."
)
TOO_MANY_SESSIONS = (
    "Some Claude Code session folders hold more than {limit:,} files, so Arena stopped reading "
    "them and their last activity is unknown."
)
PARTIAL_NOTE = (
    "Arena stopped waiting for the file system, so some repositories and activity times are "
    "missing."
)


def system_processes() -> Iterable[Any]:
    return psutil.process_iter(ATTRS)


def clean(value: Any) -> str:
    """A string from psutil that is safe to store and serve: macOS hands non-UTF-8 names and
    arguments over with surrogate escapes, which JSON encoding refuses."""
    return value.encode("utf-8", "replace").decode("utf-8") if isinstance(value, str) else ""


def current_user() -> str | None:
    try:
        return psutil.Process(os.getpid()).username()
    except (psutil.Error, OSError):
        try:
            import pwd

            return pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            return None


# -- reading the process table --------------------------------------------------------------


def _base(arg: str) -> str:
    return os.path.basename(arg.rstrip("/"))


def program(argv: list[str]) -> tuple[str, list[str]]:
    """The program a command line runs, looking through interpreters (``node cli.js``,
    ``python -m uvicorn``), ``npx``/``npm exec`` and environment wrappers."""
    args = list(argv)
    while args and (_base(args[0]) in WRAPPERS or re.match(r"^[A-Za-z_]\w*=", args[0])):
        args = args[1:]
    if not args:
        return "", []
    prog, rest = _base(args[0]), args[1:]
    if INTERPRETER.match(prog):
        for i, arg in enumerate(rest):
            if arg == "-m" and i + 1 < len(rest):
                return rest[i + 1], rest[i + 2 :]
            if arg in {"-c", "-e", "--eval", "-p", "--print"}:
                return prog, rest
            if not arg.startswith("-"):
                return SCRIPT_SUFFIX.sub("", _base(arg)), rest[i + 1 :]
        return prog, rest
    if prog in {"npx", "pnpx", "bunx"} and rest:
        return program(rest)
    if prog in NODE_TOOLS and rest[:1] in (["exec"], ["dlx"]) and len(rest) > 1:
        return program(rest[1:])
    return SCRIPT_SUFFIX.sub("", prog), rest


def _runs(argv: list[str], package: str) -> bool:
    """The executable, or the script an interpreter runs, lives in ``package``."""
    if package in argv[0]:
        return True
    return len(argv) > 1 and bool(INTERPRETER.match(_base(argv[0]))) and package in argv[1]


def classify(argv: list[str], name: str = "") -> tuple[str, str] | None:
    """(kind, tool) for development work, None for anything else. Kinds: claude, codex,
    agent, dev_server, tests, plus arena_worker / arena_controller for Arena itself."""
    argv = [a for a in argv if isinstance(a, str)] or ([name] if name else [])
    if not argv:
        return None
    head = argv[:4]
    if "agent_arena.control.worker" in head:
        return ("arena_worker", "Arena worker")
    if any(a == "agent_arena.control" or a.startswith("agent_arena.control.") for a in head):
        return ("arena_controller", "Arena")
    prog, rest = program(argv)
    if prog == "claude" or name == "claude" or _runs(argv, "claude-code/"):
        return ("claude", "Claude Code")
    if prog == "codex" or name == "codex" or _runs(argv, "@openai/codex"):
        return ("codex", "Codex")
    for key, label in OTHER_AGENTS.items():
        if prog == key or name == key:
            return ("agent", label)
    lead = rest[:1]
    if prog in {"pytest", "py.test", "jest", "vitest"}:
        return ("tests", prog)
    if prog in NODE_TOOLS and (lead == ["test"] or rest[:2] == ["run", "test"]):
        return ("tests", f"{prog} test")
    if prog in {"go", "cargo"} and lead == ["test"]:
        return ("tests", f"{prog} test")
    if prog == "playwright" and lead == ["test"]:
        return ("tests", "playwright test")
    if prog == "vite" and lead not in (["build"], ["optimize"], ["preview"]):
        return ("dev_server", "vite")
    if prog == "next" and lead == ["dev"]:
        return ("dev_server", "next dev")
    if prog == "webpack-dev-server" or (prog in {"webpack", "webpack-cli"} and "serve" in rest[:2]):
        return ("dev_server", "webpack serve")
    if prog in {"uvicorn", "streamlit", "http.server"}:
        return ("dev_server", prog)
    if prog == "flask" and lead == ["run"]:
        return ("dev_server", "flask run")
    if prog.startswith("jupyter"):
        return ("dev_server", "jupyter")
    if prog in NODE_TOOLS:
        script = rest[1:2] if lead == ["run"] else lead
        if script in (["dev"], ["start"]):
            return ("dev_server", f"{prog} run {script[0]}")
    return None


class Table:
    """The current user's processes, read once; working folders and ports on demand. With
    no known user nothing is listed (it fails closed); another user's process is never read
    beyond its owner and state, and zombie or exited processes are skipped."""

    def __init__(self, processes: Iterable[Any], user: str | None):
        self.entries: dict[int, dict[str, Any]] = {}
        self.unreadable = 0
        self.no_user = not user
        if user:
            try:
                for proc in processes:
                    self._read(proc, clean(user))
            except (psutil.Error, OSError):
                self.unreadable += 1
        self.children: dict[int, list[int]] = defaultdict(list)
        for entry in self.entries.values():
            self.children[entry["ppid"]].append(entry["pid"])
            entry["class"] = classify(entry["argv"], entry["name"])
        self._cwd: dict[int, str | None] = {}

    def _read(self, proc: Any, user: str) -> None:
        info = getattr(proc, "info", None) or {}
        pid = info.get("pid")
        if pid is None or clean(info.get("username")) != user:
            return  # someone else's process: its command line is never read
        if info.get("status") in GONE:
            return  # a zombie or a process that already exited is not work
        missing = [key for key in DETAILS if key not in info]
        if missing:
            try:
                info = {**info, **proc.as_dict(attrs=missing)}
            except psutil.NoSuchProcess:
                return  # it ended while Arena looked
            except (psutil.Error, OSError):
                self.unreadable += 1
                return
        argv = info.get("cmdline")
        self.entries[pid] = {
            "pid": pid,
            "ppid": info.get("ppid"),
            "name": clean(info.get("name")),
            "argv": [clean(a) for a in argv if isinstance(a, str)]
            if isinstance(argv, list | tuple)
            else [],
            "started": info.get("create_time"),
            "proc": proc,
        }

    def ancestors(self, pid: int) -> list[dict[str, Any]]:
        out, seen = [], {pid}
        parent = (self.entries.get(pid) or {}).get("ppid")
        while parent and parent not in seen and parent in self.entries and len(out) < ANCESTRY:
            seen.add(parent)
            out.append(self.entries[parent])
            parent = self.entries[parent].get("ppid")
        return out

    def descendants(self, pid: int, depth: int = 4) -> list[int]:
        out, frontier = [], [pid]
        for _ in range(depth):
            frontier = [c for p in frontier for c in self.children.get(p, []) if c != p]
            out += frontier
        return out

    def cwd(self, pid: int) -> str | None:
        """The working folder, or None when macOS withholds it or the process ended."""
        if pid not in self._cwd:
            try:
                value = clean(self.entries[pid]["proc"].cwd())
            except (psutil.Error, OSError, KeyError):
                value = None
            self._cwd[pid] = value or None
        return self._cwd[pid]

    def ports(self, pids: Iterable[int]) -> tuple[list[int], bool]:
        """Listening TCP ports of these processes; False when psutil could not read some."""
        found, complete = set(), True
        for pid in pids:
            proc = (self.entries.get(pid) or {}).get("proc")
            if proc is None:
                continue
            try:
                connections = proc.net_connections(kind="inet")
            except (psutil.Error, OSError):
                complete = False
                continue
            for conn in connections:
                laddr = getattr(conn, "laddr", None)
                if getattr(conn, "status", None) == psutil.CONN_LISTEN and laddr:
                    found.add(int(getattr(laddr, "port", 0) or 0))
        return sorted(p for p in found if p), complete


# -- the file system, under a deadline --------------------------------------------------------

TIMED_OUT = object()


class FileSystem:
    """File-system reads for one look, run on a worker thread under one deadline. A read
    that does not finish in time is abandoned (the thread stays with it until the system
    call returns) and every later read of this look answers TIMED_OUT at once, so a stalled
    privacy prompt or sleeping network volume costs at most the deadline."""

    def __init__(self, seconds: float = DEADLINE_SECONDS, *, enabled: bool = True):
        self.deadline = time.monotonic() + max(0.0, seconds)
        self.expired = not enabled
        self.abandoned = False
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        if self.expired:
            return TIMED_OUT
        left = self.deadline - time.monotonic()
        if left <= 0:
            self.expired = True
            return TIMED_OUT
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True, name="arena-mac-fs")
            self._thread.start()
        box: list[Any] = []
        done = threading.Event()
        self._jobs.put((fn, args, box, done))
        if not done.wait(left):
            self.expired = self.abandoned = True
            return TIMED_OUT
        return box[0]

    def _work(self) -> None:
        while (job := self._jobs.get()) is not None:
            fn, args, box, done = job
            try:
                box.append(fn(*args))
            except Exception:  # an unexpected failure reads as "could not tell"
                box.append(TIMED_OUT)
            done.set()

    def close(self) -> None:
        if self._thread is not None:
            self._jobs.put(None)  # the worker ends after any read it is still stuck in

    @property
    def busy(self) -> bool:
        """Still inside a read this look gave up on."""
        return self.abandoned and self._thread is not None and self._thread.is_alive()


def has_git(folder: str) -> bool | None:
    """Whether ``folder`` holds ``.git``; None when the folder cannot be read."""
    try:
        return (Path(folder) / ".git").exists()
    except (OSError, ValueError):
        return None


def modified(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except (OSError, ValueError):
        return None


def session_files(home: Path, folder: str, now: float) -> tuple[float | None, int | None, str]:
    """(newest session-file time, subagent files changed in the last 10 minutes, status)
    from file metadata under ~/.claude and ~/.claude-second; status is "ok", "none" (no
    session folder) or "too_many" (more than SCAN_LIMIT entries: times unknown)."""
    slugs = {re.sub(r"[/.]", "-", folder), re.sub(r"[^A-Za-z0-9]", "-", folder)}
    newest, recent, exists, scanned = None, 0, False, 0

    def seen(stat_time: float, subagent: bool):
        nonlocal newest, recent
        newest = stat_time if newest is None else max(newest, stat_time)
        if subagent and now - stat_time <= RECENT_SECONDS:
            recent += 1

    for root in (".claude", ".claude-second"):
        for slug in slugs:
            base = home / root / "projects" / slug
            try:
                if not base.is_dir():
                    continue
                exists = True
                with os.scandir(base) as items:
                    for item in items:
                        scanned += 1
                        if scanned > SCAN_LIMIT:
                            return None, None, "too_many"
                        if item.is_file() and item.name.endswith(".jsonl"):
                            seen(item.stat().st_mtime, item.name.startswith("agent-"))
                        elif item.is_dir():
                            folder_path = (
                                base / item.name
                                if item.name == "subagents"
                                else base / item.name / "subagents"
                            )
                            if not folder_path.is_dir():
                                continue
                            with os.scandir(folder_path) as nested:
                                for sub in nested:
                                    scanned += 1
                                    if scanned > SCAN_LIMIT:
                                        return None, None, "too_many"
                                    if sub.is_file() and sub.name.endswith(".jsonl"):
                                        seen(sub.stat().st_mtime, True)
            except (OSError, ValueError):
                continue
    return newest, recent, "ok" if exists else "none"


# -- Arena's own records ----------------------------------------------------------------------


class Arena:
    """The attempts, tasks, goals and projects discovery reconciles processes with."""

    def __init__(self, store):
        self.store = store
        self.home = Path(store.home)
        with contextlib.closing(store.connect()) as db:
            marks = ",".join("?" * len(ACTIVE))
            self.live = [
                store.unpack(r)
                for r in db.execute(
                    f"SELECT * FROM attempts WHERE state IN ({marks})", sorted(ACTIVE)
                )
            ]
            self.projects = {p["id"]: p for p in store.rows("projects", db)}
        self._attempts = {a["id"]: a for a in self.live}
        self._tasks: dict[str, dict[str, Any] | None] = {}
        # Enrolled repositories by path, compared as text: never a disk access (a project
        # may live on a volume that is asleep or in a privacy folder).
        self.by_repo: dict[str, dict[str, Any]] = {}
        for project in sorted(self.projects.values(), key=lambda p: bool(p.get("archived"))):
            if isinstance(project.get("repo"), str) and project["repo"]:
                self.by_repo.setdefault(os.path.normpath(project["repo"]), project)
        self.roots = list(self.by_repo)

    def attempt(self, ident: str) -> dict[str, Any] | None:
        if ident not in self._attempts:
            try:
                self._attempts[ident] = self.store.get("attempts", ident)
            except (KeyError, ValueError):
                self._attempts[ident] = None
        return self._attempts[ident]

    def task(self, ident: str | None) -> dict[str, Any] | None:
        if not ident:
            return None
        if ident not in self._tasks:
            try:
                self._tasks[ident] = self.store.get("tasks", ident)
            except (KeyError, ValueError):
                self._tasks[ident] = None
        return self._tasks[ident]

    def goal(self, ident: str | None) -> dict[str, Any] | None:
        try:
            return self.store.get("goals", ident) if ident else None
        except (KeyError, ValueError):
            return None

    def identity_match(self, entry: dict[str, Any]) -> str | None:
        started = entry.get("started")
        if not isinstance(started, int | float):
            return None
        for attempt in self.live:
            ident = attempt.get("identity") if isinstance(attempt.get("identity"), dict) else {}
            created = ident.get("created")
            if (
                ident.get("pid") == entry["pid"]
                and isinstance(created, int | float)
                and abs(created - started) < IDENTITY_TOLERANCE
            ):
                return attempt["id"]
        return None

    def worker_attempt(self, entry: dict[str, Any]) -> str | None:
        """The attempt an Arena worker of THIS state folder runs (its command line)."""
        argv = entry["argv"]
        if "agent_arena.control.worker" not in argv:
            return None
        rest = argv[argv.index("agent_arena.control.worker") + 1 :]
        if (
            len(rest) >= 2
            and rest[1].startswith("attempt_")
            and os.path.normpath(rest[0]) == os.path.normpath(str(self.home))
        ):
            return rest[1]
        return None

    def inside(self, folder: str | None, *parts: str) -> bool:
        if not folder:
            return False
        with contextlib.suppress(ValueError):
            Path(folder).relative_to(self.home.joinpath(*parts))
            return True
        return False

    def workspace_attempt(self, folder: str | None) -> str | None:
        if not self.inside(folder, "workspaces"):
            return None
        name = Path(folder or "").relative_to(self.home / "workspaces").parts[:1]
        return name[0] if name and name[0].startswith("attempt_") else None

    def integration_task(self, folder: str | None) -> str | None:
        """The task whose combined change the controller is checking in this folder
        (``projects/<id>/integrate-<task>-<work>``, see Engine.integrate)."""
        if not self.inside(folder, "projects"):
            return None
        parts = Path(folder or "").relative_to(self.home / "projects").parts
        match = re.match(r"^integrate-(task_[0-9a-f]+)-", parts[1]) if len(parts) > 1 else None
        return match.group(1) if match else None


# -- describing -------------------------------------------------------------------------------


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def tilde(path: str | Path | None, home: Path) -> str | None:
    if not path:
        return None
    value = Path(path)
    if value == home:
        return "~"
    with contextlib.suppress(ValueError):
        return "~/" + str(value.relative_to(home))
    return str(value)


def command_line(argv: list[str], home: Path) -> str:
    """The first arguments, home-relative and redacted (worker.redact_args: values of
    secret-looking flags and assignments, credentials in URLs and known token formats).
    Shown only by the live endpoint, never to the director or in stored chat cards."""
    shown = redact_args(argv[:ARGS_SHOWN])
    if str(home) != "/":
        shown = [re.sub(re.escape(str(home)) + r"(?=/|$)", "~", arg) for arg in shown]
    text = redact(" ".join(shown))  # secrets split over two arguments ("token" "abc123…")
    return text if len(text) <= 200 else text[:199] + "…"


def _ports_phrase(ports: list[int]) -> str:
    if not ports:
        return ""
    if len(ports) == 1:
        return f" on port {ports[0]}"
    return " on ports " + ", ".join(str(p) for p in ports[:-1]) + f" and {ports[-1]}"


def _activity(kind: str, label: str, **fields: Any) -> dict[str, Any]:
    out = {
        "kind": kind,
        "label": label,
        "repo": None,
        "project": None,
        "folder": None,
        "pid": None,
        "started": None,
        "last_activity": None,
        "headless": None,
        "ports": [],
        "evidence": [],
        "arena": None,
        "subagents_recent": None,
    }
    out.update(fields)
    return out


class Discoverer:
    """One read of the process table, reconciled with Arena's records."""

    def __init__(
        self,
        store,
        table: Table,
        *,
        home: Path,
        controller_pid: int,
        now: float,
        fs: FileSystem | None = None,
        known_repos: set[str] | None = None,
    ):
        self.store, self.table, self.home = store, table, home
        self.controller, self.now = controller_pid, now
        self.arena = Arena(store)
        self.fs = fs or FileSystem()
        # Repositories found by earlier looks (Discovery keeps them): matched by path.
        self.known = known_repos if known_repos is not None else set()
        self.home_text = os.path.normpath(str(home))
        self.withheld = 0
        self.ports_unread = 0
        self.unchecked: dict[str, str] = {}  # folder → "private" | "timeout"
        self.sessions: dict[str, tuple[float | None, int | None, str]] = {}
        self.subagents_counted: set[str] = set()
        self._repos: dict[str, str | None] = {}

    # repositories ------------------------------------------------------------------------
    def repo_of(self, folder: str | None) -> str | None:
        """The repository a folder belongs to. First by path, with no disk access: the
        enrolled project or known repository that contains it. Otherwise the nearest folder
        holding ``.git``, looked for only inside the home folder and outside the macOS
        privacy folders (the home folder itself is never one); a folder Arena does not
        inspect, or whose check ran out of time, is recorded in ``unchecked``."""
        if not folder:
            return None
        if folder not in self._repos:
            self._repos[folder] = self._find_repo(os.path.normpath(folder), folder)
        return self._repos[folder]

    def _root_of(self, path: str) -> str | None:
        found = None
        for root in (*self.arena.roots, *self.known):
            if (path == root or path.startswith(root.rstrip(os.sep) + os.sep)) and (
                found is None or len(root) > len(found)
            ):
                found = root
        return found

    def inspectable(self, path: str) -> bool:
        inside = path.startswith(self.home_text.rstrip(os.sep) + os.sep)
        top = path[len(self.home_text) :].lstrip(os.sep).split(os.sep, 1)[0] if inside else ""
        return inside and top not in PRIVACY_FOLDERS

    def _find_repo(self, path: str, folder: str) -> str | None:
        root = self._root_of(path)
        if root or path == self.home_text:
            return root
        if not self.inspectable(path):
            self.unchecked[folder] = "private"
            return None
        candidate = path
        for _ in range(40):
            if candidate == self.home_text or os.path.dirname(candidate) == candidate:
                return None
            found = self.fs.call(has_git, candidate)
            if found is TIMED_OUT:
                self.unchecked[folder] = "timeout"
                return None
            if found is None:
                return None  # unreadable: stop looking rather than guess
            if found:
                if len(self.known) < KNOWN_REPOS_LIMIT:
                    self.known.add(candidate)
                return candidate
            candidate = os.path.dirname(candidate)
        return None

    def project_of(self, repo: str | None) -> dict[str, Any] | None:
        return self.arena.by_repo.get(os.path.normpath(repo)) if repo else None

    def folder_of(self, entry: dict[str, Any]) -> str | None:
        """Where the process works: Codex's ``--cd`` when given, else its working folder."""
        argv = entry["argv"]
        if (entry.get("class") or ("",))[0] == "codex":
            for flag in ("--cd", "-C"):
                if flag in argv[:-1]:
                    value = argv[argv.index(flag) + 1]
                    if value.startswith("/"):
                        return value
        return self.table.cwd(entry["pid"])

    # Claude Code session evidence ----------------------------------------------------------
    def claude_sessions(self, folder: str) -> tuple[float | None, int | None, str]:
        """session_files() for a folder, read once per look however many sessions run in
        it; status "timeout" when the file system did not answer in time."""
        if folder not in self.sessions:
            found = self.fs.call(session_files, self.home, folder, self.now)
            self.sessions[folder] = (None, None, "timeout") if found is TIMED_OUT else found
        return self.sessions[folder]

    # building activities ---------------------------------------------------------------------
    def place(self, folder: str | None) -> tuple[str | None, dict[str, Any] | None, str | None]:
        """(repo, project, where): ``where`` names the place in a sentence: the project, the
        repository's folder name, the folder's own name when Arena did not check it, or "a
        folder outside any project (~/scratch)" (always home-relative)."""
        repo = self.repo_of(folder)
        project = self.project_of(repo)
        if project or repo:
            return repo, project, project["name"] if project else Path(repo).name
        if not folder:
            return None, None, None
        if folder in self.unchecked:
            return None, None, os.path.basename(os.path.normpath(folder)) or folder
        shown = tilde(folder, self.home) or ""
        note = f" ({shown})" if shown == "~" or shown.startswith("~/") else ""
        return None, None, "a folder outside any project" + note

    def base_evidence(self, entry: dict[str, Any]) -> list[str]:
        out = [
            f"Process {entry['name'] or 'unknown'} (pid {entry['pid']})"
            + (
                f", started {ago(self.now - entry['started'])}"
                if isinstance(entry.get("started"), int | float)
                else ""
            )
        ]
        command = command_line(entry["argv"], self.home)
        if command:
            out.append("Command: " + command)
        return out

    def managed_attempt(self, entry: dict[str, Any]) -> tuple[str, str] | None:
        chain = [entry, *self.table.ancestors(entry["pid"])]
        for item in chain:
            matched = self.arena.identity_match(item)
            if matched:
                return matched, "Matches Arena's record of this attempt (process id and start time)"
        for item in chain:
            matched = self.arena.worker_attempt(item)
            if matched:
                return matched, "Started by an Arena worker for this attempt"
        matched = self.arena.workspace_attempt(self.table.cwd(entry["pid"]))
        if matched:
            return matched, "Runs inside the workspace of this Arena attempt"
        return None

    def director_turn(self, entry: dict[str, Any]) -> bool:
        if any(a["pid"] == self.controller for a in self.table.ancestors(entry["pid"])):
            return True
        return self.arena.inside(self.table.cwd(entry["pid"]), "chat-scratch")

    def run(self) -> dict[str, Any]:
        managed: dict[str, dict[str, Any]] = {}
        integrating: dict[str, list[dict[str, Any]]] = defaultdict(list)
        director: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for entry in sorted(self.table.entries.values(), key=lambda e: e["pid"]):
            cls = entry.get("class")
            if not cls or cls[0] == "arena_controller" or entry["pid"] == self.controller:
                continue
            matched = self.managed_attempt(entry)
            if matched:
                group = managed.setdefault(matched[0], {"entries": [], "how": []})
                group["entries"].append(entry)
                if matched[1] not in group["how"]:
                    group["how"].append(matched[1])
            elif cls[0] == "arena_worker":
                candidates.append({**entry, "class": ("agent", "Arena worker of another Arena")})
            elif task_id := self.arena.integration_task(self.table.cwd(entry["pid"])):
                integrating[task_id].append(entry)
            elif self.director_turn(entry):
                director.append(entry)
            else:
                candidates.append(entry)
        activities = {"managed": [], "external": [], "unknown": []}
        activities["managed"] += self.managed_activities(managed)
        activities["managed"] += self.integration_activities(integrating)
        activities["managed"] += self.director_activities(director)
        for activity, group in self.external_activities(candidates):
            activities[group].append(activity)
        order = {"claude": 0, "codex": 1, "agent": 2, "tests": 3, "dev_server": 4}
        activities["managed"].sort(
            key=lambda a: (a["arena"] is None, a["project"] or "", a["started"] or 0)
        )
        for name in ("external", "unknown"):
            activities[name].sort(
                key=lambda a: (order.get(a["kind"], 9), a.get("where") or "~", a["pid"])
            )
        return {
            "generated": self.now,
            "groups": activities,
            "summary": self.summary(activities),
            "limitations": self.limitations(),
            # partial: the file system did not answer in time (see limitations); stale: an
            # earlier look served while a fresh one is still running (Discovery.get).
            "partial": self.fs.expired,
            "stale": False,
        }

    # managed ---------------------------------------------------------------------------------
    def transcript_time(self, attempt: dict[str, Any]) -> float | None:
        folder = attempt.get("artifact_dir")
        if not folder:
            return None
        found = self.fs.call(modified, os.path.join(folder, "transcript.jsonl"))
        return None if found is TIMED_OUT else found

    def arena_fields(self, attempt: dict[str, Any] | None, attempt_id: str) -> dict[str, Any]:
        task = self.arena.task((attempt or {}).get("task_id"))
        project = self.arena.projects.get((task or {}).get("project_id")) if task else None
        from .story import is_plan_task, plain_title
        from .titles import goal_title

        title = plain_title((task or {}).get("title"), "a build step")
        if task and is_plan_task(task):  # the director's plan is named after its build
            goal = self.arena.goal(task.get("goal_id"))
            title = (plain_title(goal_title(goal)) if goal else "") or title
        phase = (attempt or {}).get("phase") or (task or {}).get("phase") or "build"
        kind = (
            "arena_director"
            if phase in {"plan", "replan", "direct"} and (task or {}).get("director")
            else "arena_build"
        )
        return {
            "kind": kind,
            "title": title,
            "phase": phase,
            "task": task,
            "project": project,
            "arena": {
                "goal_id": (task or {}).get("goal_id"),
                "task_id": (task or {}).get("id"),
                "attempt_id": attempt_id,
            },
        }

    def managed_activities(self, managed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for attempt_id, group in managed.items():
            attempt = self.arena.attempt(attempt_id)
            facts = self.arena_fields(attempt, attempt_id)
            entries = group["entries"]
            anchor = next(
                (e for e in entries if e["class"][0] == "arena_worker"),
                next((e for e in entries if e["class"][0] in AGENT_KINDS), entries[0]),
            )
            live = (attempt or {}).get("state") in ACTIVE
            verb = PHASE_VERBS.get(facts["phase"], "working on")
            if live and attempt and attempt.get("state") == "verifying":
                verb = "checking"
            prefix = "Arena director" if facts["kind"] == "arena_director" else "Arena"
            label = (
                f"{prefix} is {verb} “{facts['title']}”"
                if live
                else "A process is still running in the folder of a finished Arena build"
            )
            evidence = list(group["how"])
            for entry in entries:
                kind, tool = entry["class"]
                if kind in AGENT_KINDS:
                    evidence.append(f"Agent process: {tool} (pid {entry['pid']})")
                elif kind == "tests":
                    evidence.append(f"Running the project's checks: {tool} (pid {entry['pid']})")
                elif kind == "dev_server":
                    evidence.append(f"Development server: {tool} (pid {entry['pid']})")
            servers = [e["pid"] for e in entries if e["class"][0] == "dev_server"]
            ports, complete = self.table.ports(servers) if servers else ([], True)
            self.ports_unread += 0 if complete else 1
            project = facts["project"]
            out.append(
                _activity(
                    facts["kind"],
                    label,
                    repo=tilde(project["repo"], self.home) if project else None,
                    project=project["name"] if project else None,
                    folder=tilde((attempt or {}).get("workspace"), self.home),
                    pid=anchor["pid"],
                    started=anchor.get("started"),
                    last_activity=self.transcript_time(attempt) if attempt else None,
                    headless=True,
                    ports=ports,
                    evidence=evidence[:6],
                    arena=facts["arena"],
                    live=live,
                )
            )
        for attempt in self.arena.live:
            if attempt["id"] in managed or self.table.no_user:
                continue  # with no process inspected, "no process found" would be a guess
            facts = self.arena_fields(attempt, attempt["id"])
            starting = (
                attempt.get("state") == "leased"
                and self.now - (attempt.get("created") or 0) < STARTING_SECONDS
            )
            ident = attempt.get("identity") if isinstance(attempt.get("identity"), dict) else {}
            project = facts["project"]
            out.append(
                _activity(
                    facts["kind"],
                    "Arena is starting an agent for this build"
                    if starting
                    else "Arena records this as running, but no agent process was found",
                    repo=tilde(project["repo"], self.home) if project else None,
                    project=project["name"] if project else None,
                    folder=tilde(attempt.get("workspace"), self.home),
                    started=attempt.get("created"),
                    last_activity=self.transcript_time(attempt),
                    headless=True,
                    evidence=[
                        f"Arena's record: attempt {attempt['id']} is {attempt.get('state')}",
                        f"No running process has its recorded id {ident['pid']}"
                        if ident.get("pid")
                        else "No process identity was recorded for it yet",
                    ],
                    arena=facts["arena"],
                    live=True,
                    missing=not starting,
                    starting=starting,
                )
            )
        return out

    def integration_activities(
        self, integrating: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        out = []
        for task_id, entries in integrating.items():
            task = self.arena.task(task_id)
            facts = self.arena_fields({"task_id": task_id, "phase": "build"}, "")
            project = facts["project"]
            first = entries[0]
            out.append(
                _activity(
                    "arena_build",
                    f"Arena is checking the combined change for “{facts['title']}”",
                    repo=tilde(project["repo"], self.home) if project else None,
                    project=project["name"] if project else None,
                    folder=tilde(self.table.cwd(first["pid"]), self.home),
                    pid=first["pid"],
                    started=first.get("started"),
                    headless=True,
                    evidence=[
                        "Runs in Arena's integration folder for this build",
                        *(
                            f"Running the project's checks: {e['class'][1]} (pid {e['pid']})"
                            for e in entries
                        ),
                    ][:6],
                    arena={
                        "goal_id": (task or {}).get("goal_id"),
                        "task_id": task_id,
                        "attempt_id": None,
                    },
                    checking=True,
                )
            )
        return out

    def director_activities(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        tops = [
            e
            for e in entries
            if e["class"][0] in AGENT_KINDS
            and not any(a in entries for a in self.table.ancestors(e["pid"]))
        ]
        for entry in tops:
            folder = self.folder_of(entry)
            repo, project, _ = self.place(folder)
            if self.arena.inside(folder, "chat-scratch"):
                repo, project = None, None
            out.append(
                _activity(
                    "arena_director",
                    "Arena's director is answering a chat message",
                    repo=tilde(repo, self.home),
                    project=project["name"] if project else None,
                    folder=tilde(folder, self.home),
                    pid=entry["pid"],
                    started=entry.get("started"),
                    headless=True,
                    evidence=[
                        "Started by the Arena controller (a director chat turn)",
                        *self.base_evidence(entry),
                    ],
                )
            )
        return out

    # external and unknown ----------------------------------------------------------------------
    def external_activities(self, entries: list[dict[str, Any]]):
        listed = {e["pid"]: e for e in entries}
        folded: dict[int, list[dict[str, Any]]] = defaultdict(list)
        tops = []
        for entry in entries:
            kind = entry["class"][0]
            parent = next(
                (
                    a
                    for a in self.table.ancestors(entry["pid"])
                    if a["pid"] in listed and listed[a["pid"]]["class"][0] == kind
                ),
                None,
            )
            if parent is not None:
                top = parent
                for a in self.table.ancestors(parent["pid"]):
                    if a["pid"] in listed and listed[a["pid"]]["class"][0] == kind:
                        top = a
                folded[top["pid"]].append(entry)
            else:
                tops.append(entry)
        for entry in tops:
            yield self.external(entry, folded.get(entry["pid"], []))

    def external(self, entry: dict[str, Any], children: list[dict[str, Any]]):
        kind, tool = entry["class"]
        folder = self.folder_of(entry)
        if folder is None:
            self.withheld += 1
        repo, project, where = self.place(folder)
        unchecked = self.unchecked.get(folder or "")
        evidence = self.base_evidence(entry)
        evidence.append(
            f"Working folder: {tilde(folder, self.home)}"
            if folder
            else "macOS withheld its working folder"
        )
        if repo:
            evidence.append(
                f"Repository: {tilde(repo, self.home)}"
                + (f" (enrolled in Arena as {project['name']})" if project else "")
            )
        elif unchecked:
            evidence.append(
                "Arena does not look for repositories in macOS privacy folders"
                if unchecked == "private"
                else "The file system did not answer in time, so the repository is unknown"
            )
        starter = next(
            (
                a
                for a in self.table.ancestors(entry["pid"])
                if (a.get("class") or ("",))[0] in AGENT_KINDS
            ),
            None,
        )
        if starter and starter["class"][0] != kind:
            evidence.append(f"Started from {starter['class'][1]} (pid {starter['pid']})")
        if children:
            evidence.append(
                f"{len(children)} related child process{'es' if len(children) != 1 else ''}"
            )
        fields: dict[str, Any] = {
            "repo": tilde(repo, self.home),
            "project": project["name"] if project else None,
            "folder": tilde(folder, self.home),
            "pid": entry["pid"],
            "started": entry.get("started"),
            "where": where,
            "unchecked": unchecked,
        }
        argv = entry["argv"]
        if kind == "claude":
            fields["headless"] = "-p" in argv or "--print" in argv
            if folder:
                newest, recent, status = self.claude_sessions(folder)
                # Session files belong to the folder, not to one process: the first session
                # in a folder carries its recent subagents, any others none.
                first = folder not in self.subagents_counted
                self.subagents_counted.add(folder)
                fields["last_activity"] = newest
                fields["subagents_recent"] = recent if first or recent is None else 0
                fields["sessions_unknown"] = status in {"too_many", "timeout"}
                evidence.append(
                    f"This folder holds more than {SCAN_LIMIT:,} Claude Code session files, so "
                    "Arena stopped reading them and its last activity is unknown"
                    if status == "too_many"
                    else "The file system did not answer in time, so its last activity is unknown"
                    if status == "timeout"
                    else "Claude Code session files for this folder last changed "
                    + ago(self.now - newest)
                    if newest
                    else "No Claude Code session files were found for this folder"
                )
                if recent and first:
                    evidence.append(
                        f"{recent} subagent session file{'s' if recent != 1 else ''} changed in "
                        "the last 10 minutes (inferred from session files)"
                    )
        elif kind == "codex":
            fields["headless"] = "exec" in argv[1:4] or "e" in argv[1:2]
        elif kind == "agent":
            fields["headless"] = False
        if kind == "dev_server":
            pids = [entry["pid"], *(c["pid"] for c in children)]
            pids += [d for p in list(pids) for d in self.table.descendants(p)]
            ports, complete = self.table.ports(dict.fromkeys(pids))
            if not complete:
                self.ports_unread += 1
            fields["ports"] = ports
            if ports:
                evidence.append(
                    f"Listening on port{'s' if len(ports) != 1 else ''} "
                    + ", ".join(str(p) for p in ports)
                )
        if starter and kind in {"tests", "dev_server"}:
            fields["started_by"] = starter["class"][0]
        label = {
            "claude": "Claude Code session",
            "codex": "Codex session",
            "agent": f"{tool} session",
            "dev_server": f"Development server ({tool})",
            "tests": f"Test run ({tool})",
        }[kind]
        unknown = folder is None or (kind in AGENT_KINDS and repo is None)
        activity = _activity(kind, label, evidence=evidence[:6], **fields)
        return activity, "unknown" if unknown else "external"

    # what the owner reads ------------------------------------------------------------------------
    def sentence(self, a: dict[str, Any], group: str) -> str | None:
        kind, where = a["kind"], a.get("where")
        if group == "unknown":
            label = a["label"] if kind in AGENT_KINDS else a["label"][0].lower() + a["label"][1:]
            article = "An" if label[:1].lower() in "aeiou" else "A"
            if a["folder"] is None:
                return f"{article} {label} is running, but macOS did not reveal its working folder."
            if a.get("unchecked") == "private":
                return (
                    f"{article} {label} is open in {where}, a folder Arena does not inspect, so "
                    "its repository is unknown."
                )
            if a.get("unchecked") == "timeout":
                return f"{article} {label} is open in {where}; Arena ran out of time checking it."
            return f"{article} {label} is open in {where}."  # "a folder outside any project (~)"
        if kind == "claude":
            if a.get("sessions_unknown"):
                return (
                    f"A Claude Code session is open in {where}; Arena could not tell when it was "
                    "last active."
                )
            # "Working" only on fresh evidence; an older session file says it is open.
            if a["last_activity"] and self.now - a["last_activity"] <= WORKING_SECONDS:
                return (
                    f"Claude is working in {where} (active {ago(self.now - a['last_activity'])})."
                )
            if a["last_activity"]:
                return (
                    f"A Claude Code session is open in {where} "
                    f"(last active {ago(self.now - a['last_activity'])})."
                )
            return f"A Claude Code session is open in {where}; Arena found no session activity."
        started = (
            f" (started {ago(self.now - a['started'])})"
            if isinstance(a.get("started"), int | float)
            else ""
        )
        if kind == "codex":
            return f"A Codex session is open in {where}{started}."
        if kind == "agent":
            return f"{a['label'][: -len(' session')]} is running in {where}{started}."
        starter = PROVIDERS.get(a.get("started_by") or "", "")
        at = f"for {where}" if a["repo"] else f"in {where}"
        if kind == "tests":
            if starter:
                return f"{starter} is running tests {at}."
            return f"Tests are running {at} ({a['label'][10:-1]})."
        if kind == "dev_server":
            ports = _ports_phrase(a["ports"])
            if starter:
                return f"{starter} started a development server {at}{ports}."
            return f"A development server is running {at}{ports}."
        return None

    def summary(self, groups: dict[str, list[dict[str, Any]]]) -> list[str]:
        out = []
        building: dict[str, int] = {}
        for a in groups["managed"]:
            project = a["project"] or "a project"
            if a.get("checking"):
                out.append(f"Arena is checking the combined change for {project}.")
            elif a.get("missing"):
                out.append(
                    f"Arena records a build of {project} as running, but no agent process "
                    "was found."
                )
            elif a.get("starting"):
                out.append(f"Arena is starting an agent for {project}.")
            elif a["arena"] and a.get("live"):
                building[project] = building.get(project, 0) + 1
            elif a["arena"]:
                out.append(
                    f"A process is still running in the folder of a finished Arena build of "
                    f"{project}."
                )
            else:
                out.append("Arena's director is answering a chat message.")
        out[:0] = [
            f"Arena is building {project}: {n} agent{'s' if n != 1 else ''}."
            for project, n in building.items()
        ]
        for group in ("external", "unknown"):
            for a in groups[group]:
                text = self.sentence(a, group)
                if text:
                    out.append(text)
        if not out:
            out.append("No coding agents, development servers or test runs were found.")
        return out

    def limitations(self) -> list[str]:
        out = list(LIMITATIONS)
        if self.table.no_user:
            out[0] = NO_USER
        if "private" in self.unchecked.values():
            out.insert(1, PRIVACY_NOTE)
        if any(status == "too_many" for _, _, status in self.sessions.values()):
            out.insert(1, TOO_MANY_SESSIONS.format(limit=SCAN_LIMIT))
        if self.withheld:
            n = self.withheld
            out.insert(
                1,
                f"macOS withheld the working folder of {n} process{'es' if n != 1 else ''}, so "
                + ("their repositories are unknown." if n != 1 else "its repository is unknown."),
            )
        if self.ports_unread:
            n = self.ports_unread
            out.insert(1, f"Listening ports could not be read for {n} development server(s).")
        if self.table.unreadable:
            out.insert(1, "Some processes ended or could not be read while Arena looked.")
        if self.fs.expired:
            out.insert(1, PARTIAL_NOTE)
        return out


PRIVATE = (
    "where",
    "started_by",
    "live",
    "missing",
    "starting",
    "checking",
    "unchecked",
    "sessions_unknown",
)


def discover(
    store,
    *,
    processes: Iterable[Any] | None = None,
    home: Path | str | None = None,
    user: str | None = None,
    controller_pid: int | None = None,
    now: float | None = None,
    deadline: float = DEADLINE_SECONDS,
    fs: FileSystem | None = None,
    known_repos: set[str] | None = None,
) -> dict[str, Any]:
    """GET /api/v1/mac-activity (docs/ROUND3_CONTRACT.md A3): a fresh, read-only look. Its
    file-system reads stop at ``deadline`` seconds (or ``fs``'s own deadline); what they did
    not reach is left out and the limitations say so."""
    fs = fs or FileSystem(deadline)
    try:
        table = Table(
            system_processes() if processes is None else processes,
            current_user() if user is None else user,
        )
        result = Discoverer(
            store,
            table,
            home=Path(home or Path.home()),
            controller_pid=os.getpid() if controller_pid is None else controller_pid,
            now=time.time() if now is None else now,
            fs=fs,
            known_repos=known_repos,
        ).run()
    finally:
        fs.close()
    for group in GROUPS:
        for activity in result["groups"][group]:
            for key in PRIVATE:
                activity.pop(key, None)
    return result


class Discovery:
    """``discover`` behind a 10-second cache and a lock: a burst of requests (or the
    director and the phone together) inspects the process table once. No caller waits
    longer than ``deadline`` seconds in all: one that cannot take the lock within
    ``lock_seconds`` (another look is running) gets the previous look marked stale, or None
    when there is none yet."""

    def __init__(
        self,
        store,
        *,
        processes: Callable[[], Iterable[Any]] | None = None,
        home: Path | str | None = None,
        user: str | None = None,
        controller_pid: int | None = None,
        clock: Callable[[], float] = time.time,
        seconds: float = CACHE_SECONDS,
        deadline: float = DEADLINE_SECONDS,
        lock_seconds: float = LOCK_SECONDS,
    ):
        self.store, self.processes, self.home = store, processes, home
        self.user, self.controller_pid = user, controller_pid
        self.clock, self.seconds = clock, seconds
        self.deadline, self.lock_seconds = deadline, min(lock_seconds, deadline)
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._stamp = 0.0
        self._fs: FileSystem | None = None
        self.known_repos: set[str] = set()

    def get(self) -> dict[str, Any] | None:
        started = time.monotonic()
        if not self._lock.acquire(timeout=self.lock_seconds):
            return self.stale()
        try:
            now = self.clock()
            if self._value is not None and now - self._stamp < self.seconds:
                return self._value
            # A read the previous look gave up on may still be stuck (a sleeping network
            # volume): this look then skips the file system instead of queueing behind it.
            fs = FileSystem(
                self.deadline - (time.monotonic() - started),
                enabled=not (self._fs is not None and self._fs.busy),
            )
            self._fs = fs
            self._value = discover(
                self.store,
                processes=self.processes() if self.processes else None,
                home=self.home,
                user=self.user,
                controller_pid=self.controller_pid,
                now=now,
                fs=fs,
                known_repos=self.known_repos,
            )
            self._stamp = now
            return self._value
        finally:
            self._lock.release()

    def stale(self) -> dict[str, Any] | None:
        value, stamp = self._value, self._stamp
        if value is None:
            return None
        note = (
            "A fresh look at the Mac is still running, so this is Arena's previous look "
            f"({ago(self.clock() - stamp)})."
        )
        return {**value, "stale": True, "limitations": [note, *value["limitations"]]}


# -- what the director and chat cards see -------------------------------------------------------

# The folder note a summary sentence may end with: "… outside any project (~/scratch)."
_FOLDER_NOTE = re.compile(r" \((?:~|/)[^()]*\)")
# A chat card's activity: what the phone shows, never process ids, folders or commands.
CARD_KEYS = (
    "kind",
    "label",
    "repo",
    "project",
    "started",
    "last_activity",
    "headless",
    "ports",
    "arena",
)


def pathless(text: str) -> str:
    """A sentence without its folder note: the director and stored cards carry no paths."""
    return _FOLDER_NOTE.sub("", text)


def repo_name(repo: str | None) -> str | None:
    """A repository as the director and chat cards name it: home-relative, or only its
    folder name when it lies outside the home folder (never an absolute path)."""
    if not repo:
        return None
    if repo == "~" or repo.startswith("~/"):
        return repo
    return os.path.basename(repo.rstrip("/")) or None


def compact(result: dict[str, Any] | None, limit: int = ACTIVITY_LIMIT) -> dict[str, Any] | None:
    """The director's view: sentences and at most ``limit`` activities: kind, label,
    project, repository, times, ports and recent subagents. No process ids, command lines,
    folders or evidence."""
    if not result:
        return None
    now = result.get("generated") or time.time()
    items, total = [], 0
    for group in GROUPS:
        for a in result["groups"].get(group, []):
            total += 1
            if len(items) >= limit:
                continue
            item = {
                "group": GROUP_NAMES[group],
                "kind": a["kind"],
                "label": a["label"],
                "project": a["project"],
                "repo": repo_name(a.get("repo")),
                "started": ago(now - a["started"])
                if isinstance(a.get("started"), int | float)
                else None,
                "last_activity": ago(now - a["last_activity"])
                if isinstance(a.get("last_activity"), int | float)
                else None,
                "ports": a.get("ports") or [],
            }
            if a.get("subagents_recent"):
                item["subagents_recent"] = a["subagents_recent"]
            items.append(item)
    return {
        "summary": [pathless(s) for s in result.get("summary", [])[:limit]],
        "activities": items,
        "omitted": max(0, total - limit),
        "limitations": [pathless(s) for s in result.get("limitations", [])],
    }


def capped(result: dict[str, Any] | None, limit: int = ACTIVITY_LIMIT) -> dict[str, Any] | None:
    """A chat message's card, stored with the message: the discover() shape with at most
    ``limit`` activities (managed first), each reduced to CARD_KEYS, and sentences without
    folder notes, so a long conversation stays light to poll and keeps no command lines,
    process ids or paths."""
    if not result:
        return None
    groups, room = {}, limit
    for group in GROUPS:
        items = result["groups"].get(group, [])[: max(room, 0)]
        groups[group] = [
            {
                **{key: a.get(key) for key in CARD_KEYS},
                "repo": repo_name(a.get("repo")),
                "ports": a.get("ports") or [],
            }
            for a in items
        ]
        room -= len(items)
    return {
        **result,
        "groups": groups,
        "summary": [pathless(s) for s in result.get("summary", [])[:limit]],
        "limitations": [pathless(s) for s in result.get("limitations", [])],
    }
