"""Safe self-activation of a new Arena release.

After the controller integrates a verified change into the canonical repository's local
``main``, a detached activator (this module, run as ``python -m agent_arena.control.activation``)
checks that commit out into an isolated release worktree, builds the UI there, points the
launchd login service at the worktree, verifies the new controller is healthy and rolls back
to the previous release automatically when any check fails.

Invariants:

* The live controller's executable files are never modified in place: each release is its own
  ``git worktree`` under ``<repo>/.arena-releases/<sha12>``.
* The canonical checkout is never modified (only ``git worktree add`` from it).
* Credentials, the SQLite ledger, worker guardians and artifacts live in the state directory
  (``--home``) and are untouched; only ``control.plist``, ``control.plist.previous``,
  ``releases.json`` and ``activation.log`` are written there.
* Everything that shells out goes through an injectable ``run`` callable and everything HTTP
  through an injectable ``http`` object, so tests never touch launchctl, npm, git or the network.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

from .engine import atomic_json
from .store import Store

PROBE_SESSION_ID = "activation-probe"
PROBE_SESSION_LABEL = "activation probe"
PROBE_SESSION_SECONDS = 300
KEEP_SPARE_RELEASES = 3
POLL_SECONDS = 0.5
LAUNCH_AGENTS = Path.home() / "Library/LaunchAgents"


class ActivationError(RuntimeError):
    """A release could not be prepared, activated or verified; the message is plain English."""


def _log(home: Path, message: str) -> None:
    """Append a timestamped line to <home>/activation.log (the activator runs detached)."""
    try:
        with (home / "activation.log").open("a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + message + "\n")
    except OSError:
        pass


def _check_sha(sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        raise ActivationError(f"Not a commit hash: {sha!r}")
    return sha


def _text(result) -> str:
    out = (getattr(result, "stderr", "") or "") + (getattr(result, "stdout", "") or "")
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out.strip()[-2000:]


def _run(run, argv, *, timeout, what, env=None, tolerate=None):
    """Run argv through the injectable runner; raise ActivationError on failure."""
    try:
        result = run(
            [str(a) for a in argv],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ActivationError(f"{what} could not run: {exc}") from None
    if result.returncode:
        if tolerate and tolerate(result):
            return result
        raise ActivationError(f"{what} failed (exit {result.returncode}): {_text(result)}")
    return result


# --------------------------------------------------------------------------- releases


def release_dir(repo: Path, sha: str) -> Path:
    return Path(repo) / ".arena-releases" / _check_sha(sha)[:12]


def release_python(repo: Path) -> Path:
    return Path(repo) / ".venv" / "bin" / "python"


def prepare_release(repo: Path, sha: str, *, run=subprocess.run) -> Path:
    """Create (or reuse) the release worktree for sha, build its UI and verify it imports."""
    repo = Path(repo)
    target = release_dir(repo, sha)
    if not (repo / ".git").exists():
        raise ActivationError(f"{repo} is not a git repository")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        _run(
            run,
            ["git", "-C", repo, "worktree", "add", "--detach", target, sha],
            timeout=120,
            what=f"Creating the release worktree for {sha[:12]}",
        )
    if not target.is_dir():
        raise ActivationError(f"Release worktree {target} was not created")
    modules = repo / "web" / "node_modules"
    link = target / "web" / "node_modules"
    if modules.is_dir() and not link.exists() and not link.is_symlink():
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(modules, target_is_directory=True)
    if not (target / "web" / "dist" / "index.html").exists():
        _run(
            run,
            ["npm", "run", "build", "--prefix", target / "web"],
            timeout=900,
            what=f"Building the UI for release {sha[:12]}",
        )
        if not (target / "web" / "dist" / "index.html").exists():
            raise ActivationError(
                f"The UI build for release {sha[:12]} produced no web/dist/index.html"
            )
    python = release_python(repo)
    if not python.exists():
        raise ActivationError(f"No virtual environment interpreter at {python}")
    env = {**os.environ, "PYTHONPATH": str(target / "src")}
    result = _run(
        run,
        [python, "-c", "import agent_arena.control.api as m; print(m.__file__)"],
        timeout=120,
        what=f"Importing the controller from release {sha[:12]}",
        env=env,
    )
    loaded = (result.stdout or "").strip().splitlines()
    if not loaded or not loaded[-1].startswith(str(target)):
        raise ActivationError(
            f"Release {sha[:12]} imported the controller from {loaded[-1] if loaded else '?'}, "
            f"not from {target}"
        )
    return target


def service_label(home: Path) -> str:
    """The launchd label of this state directory's login service."""
    plist = Path(home) / "control.plist"
    if plist.is_file():
        try:
            label = plistlib.loads(plist.read_bytes()).get("Label")
            if label:
                return str(label)
        except (plistlib.InvalidFileException, ValueError):
            pass
    return "local.agent-arena.control." + hashlib.sha256(str(home).encode()).hexdigest()[:12]


def write_plist(home, repo, release, port, origin, label, python) -> Path:
    """Write <home>/control.plist.next running the controller from the release worktree."""
    home = Path(home)
    data = {
        "Label": label,
        "ProgramArguments": [
            "/usr/bin/caffeinate",
            "-i",
            str(python),
            "-m",
            "agent_arena.control",
            "--home",
            str(home),
            "serve",
            "--port",
            str(port),
            "--origin",
            str(origin),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "WorkingDirectory": str(release),
        "StandardOutPath": str(home / "service.log"),
        "StandardErrorPath": str(home / "service.log"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": str(Path(release) / "src"),
        },
    }
    destination = home / "control.plist.next"
    tmp = home / "control.plist.next.tmp"
    tmp.write_bytes(plistlib.dumps(data))
    tmp.chmod(0o600)
    tmp.replace(destination)
    return destination


# --------------------------------------------------------------------------- health


def _server_identity(home: Path):
    try:
        data = json.loads((Path(home) / "server.json").read_text())
    except (OSError, ValueError):
        return None
    try:
        p = psutil.Process(int(data["pid"]))
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        if abs(p.create_time() - float(data["created"])) >= 0.01:
            return None
    except (psutil.Error, KeyError, TypeError, ValueError):
        return None
    return {"pid": int(data["pid"]), "created": float(data["created"])}


def _heartbeat(home: Path):
    with Store(home).transaction() as db:
        row = db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
    return json.loads(row[0]) if row else None


class _ProbeSession:
    """A short-lived session row so the activator can read the authenticated overview."""

    def __init__(self, home: Path):
        self.store = Store(home)
        self.token = secrets.token_urlsafe(32)
        self.hash = hashlib.sha256(self.token.encode()).hexdigest()

    def __enter__(self):
        now = time.time()
        with self.store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (PROBE_SESSION_ID,))
            db.execute(
                "INSERT INTO sessions(hash,expires,csrf,id,label,created,last_seen) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    self.hash,
                    now + PROBE_SESSION_SECONDS,
                    secrets.token_urlsafe(32),
                    PROBE_SESSION_ID,
                    PROBE_SESSION_LABEL,
                    now,
                    now,
                ),
            )
        return self

    def __exit__(self, *exc):
        with self.store.transaction() as db:
            db.execute("DELETE FROM sessions WHERE hash=? OR id=?", (self.hash, PROBE_SESSION_ID))
        return False


def health(
    origin,
    home,
    *,
    timeout=90,
    http=httpx,
    sleep=time.sleep,
    clock=time.monotonic,
    previous_pid=None,
) -> dict:
    """Verify the controller behind origin is up, authenticates and serves the existing history.

    Returns a dict of the checks that passed; raises ActivationError naming the check that
    did not pass within the timeout.
    """
    home = Path(home)
    origin = str(origin).rstrip("/")
    deadline = clock() + timeout
    passed = {}

    def until(name, check):
        last = None
        while True:
            try:
                value = check()
            except Exception as exc:  # transport errors while the service restarts
                value, last = None, f"{type(exc).__name__}: {exc}"[:300]
            if value is not None and value is not False:
                passed[name] = value
                return value
            if clock() >= deadline:
                done = ", ".join(passed) or "none"
                raise ActivationError(
                    f"Health check '{name}' did not pass within {timeout}s"
                    + (f" (last: {last})" if last else "")
                    + f"; passed: {done}"
                )
            sleep(POLL_SECONDS)

    def get(path, **kw):
        return http.get(origin + path, timeout=10, follow_redirects=False, **kw)

    def process():
        ident = _server_identity(home)
        if not ident or (previous_pid is not None and ident["pid"] == previous_pid):
            return None
        return ident["pid"]

    def ui():
        r = get("/")
        ok = r.status_code == 200 and "text/html" in r.headers.get("content-type", "")
        if not ok:
            raise ActivationError(f"GET / answered {r.status_code}")
        return True

    def scheduler():
        r = get("/api/v1/health")
        body = r.json() if r.status_code in (200, 503) else {}
        if r.status_code != 200 or not body.get("scheduler_ready"):
            raise ActivationError(f"GET /api/v1/health answered {r.status_code} {body}")
        return True

    def auth():
        r = get("/api/v1/overview")
        if r.status_code != 401:
            raise ActivationError(
                f"GET /api/v1/overview without a session answered {r.status_code}"
            )
        return True

    def history(probe):
        def check():
            r = get("/api/v1/overview", cookies={"arena_session": probe.token})
            if r.status_code != 200:
                raise ActivationError(
                    f"GET /api/v1/overview with a session answered {r.status_code}"
                )
            body = r.json()
            if not isinstance(body.get("projects"), list) or not isinstance(
                body.get("tasks"), list
            ):
                raise ActivationError("Overview lacks the projects/tasks arrays")
            return {"projects": len(body["projects"]), "tasks": len(body["tasks"])}

        return check

    def heartbeat():
        seen = []

        def check():
            value = _heartbeat(home)
            if value is not None and (not seen or value != seen[-1]):
                seen.append(value)
            # Two advances after the first observation prove the scheduler thread is alive.
            return seen[-1] if len(seen) >= 3 else None

        return check

    until("process", process)
    until("ui", ui)
    until("scheduler", scheduler)
    until("auth", auth)
    with _ProbeSession(home) as probe:
        until("history", history(probe))
    until("heartbeat", heartbeat())
    return passed


# --------------------------------------------------------------------------- launchd


def _bootout_tolerable(result) -> bool:
    text = _text(result).lower()
    return result.returncode == 3 or any(
        s in text for s in ("no such process", "not loaded", "could not find", "not find")
    )


def _bootout(run, label):
    _run(
        run,
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
        timeout=60,
        what="Unloading the login service",
        tolerate=_bootout_tolerable,
    )


def _bootstrap(run, plist):
    _run(
        run,
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
        timeout=60,
        what="Loading the login service",
    )


def _wait_for_exit(ident, *, sleep, clock, timeout=30):
    if not ident:
        return
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            p = psutil.Process(ident["pid"])
            if (
                p.status() == psutil.STATUS_ZOMBIE
                or abs(p.create_time() - ident["created"]) >= 0.01
            ):
                return
        except psutil.Error:
            return
        sleep(POLL_SECONDS)


def ensure_launch_agent(home: Path, label: str, launch_agents: Path = LAUNCH_AGENTS) -> Path:
    """The ~/Library/LaunchAgents/<label>.plist symlink must point at <home>/control.plist."""
    destination = Path(home) / "control.plist"
    link = Path(launch_agents) / (label + ".plist")
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or link.resolve() != destination.resolve():
            raise ActivationError(f"An unrelated login service occupies {link}; nothing replaced")
        return link
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(destination)
    return link


# --------------------------------------------------------------------------- bookkeeping


def read_releases(home: Path) -> dict:
    path = Path(home) / "releases.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    return {
        "current": data.get("current"),
        "previous": data.get("previous"),
        "history": list(data.get("history") or []),
    }


def _record(home: Path, releases: dict, sha: str, result: str, reason=None) -> dict:
    entry = {"sha": sha, "time": time.time(), "result": result, "reason": reason}
    releases["history"] = (releases.get("history") or [])[-199:] + [entry]
    atomic_json(Path(home) / "releases.json", releases)
    return releases


def _event(home: Path, kind: str, **data):
    try:
        store = Store(home)
        with store.transaction() as db:
            store.event(db, kind, **data)
    except Exception as exc:  # the ledger must not block an activation decision
        _log(home, f"could not record {kind}: {exc}")


def prune_releases(repo: Path, keep: set[str], *, run=subprocess.run, spare=KEEP_SPARE_RELEASES):
    """Remove old release worktrees, keeping those in `keep` plus the `spare` newest others."""
    root = Path(repo) / ".arena-releases"
    if not root.is_dir():
        return []
    protected = {k[:12] for k in keep if k}
    others = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name not in protected),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for path in others[spare:]:
        try:
            _run(
                run,
                ["git", "-C", repo, "worktree", "remove", "--force", path],
                timeout=120,
                what=f"Removing old release {path.name}",
            )
        except ActivationError:
            shutil.rmtree(path, ignore_errors=True)
        removed.append(path.name)
    if removed:
        with contextlib.suppress(ActivationError):
            _run(run, ["git", "-C", repo, "worktree", "prune"], timeout=60, what="worktree prune")
    return removed


def default_origin(home: Path, port: int) -> str:
    path = Path(home) / "connection.json"
    try:
        origin = json.loads(path.read_text()).get("origin")
    except (OSError, ValueError, AttributeError):
        origin = None
    return origin or f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------- procedure


def _swap(run, home: Path, label: str, source: Path, *, sleep, clock):
    """Unload the service, install `source` as control.plist, load it again."""
    old = _server_identity(home)
    _bootout(run, label)
    _wait_for_exit(old, sleep=sleep, clock=clock)
    control = Path(home) / "control.plist"
    if source != control:
        shutil.copy2(source, control)
    _bootstrap(run, control)
    return old


def rollback(
    home,
    *,
    port,
    origin=None,
    run=subprocess.run,
    http=httpx,
    sleep=time.sleep,
    clock=time.monotonic,
    reason="manual rollback",
    timeout=90,
    sha=None,
    record=True,
) -> dict:
    """Restore control.plist.previous as the live service and verify it.

    With record=True (a manual rollback after a successful activation) releases.json moves
    `previous` into `current`; activate() passes record=False because `current` never moved.
    """
    home = Path(home)
    previous = home / "control.plist.previous"
    if not previous.is_file():
        raise ActivationError(f"No previous service plist at {previous}; nothing to roll back to")
    origin = origin or default_origin(home, port)
    label = service_label(home)
    releases = read_releases(home)
    _log(home, f"rolling back: {reason}")
    old = _swap(run, home, label, previous, sleep=sleep, clock=clock)
    checks = health(
        origin,
        home,
        timeout=timeout,
        http=http,
        sleep=sleep,
        clock=clock,
        previous_pid=old["pid"] if old else None,
    )
    if record:
        abandoned = releases["current"]
        releases["current"], releases["previous"] = releases["previous"], None
        _record(home, releases, sha or abandoned or "", "rolled_back", reason)
    _event(home, "activation.rolled_back", sha=sha or releases.get("current"), reason=reason)
    _log(home, "rollback verified healthy")
    return {"rolled_back": True, "reason": reason, "health": checks, "current": releases["current"]}


def activate(
    home,
    repo,
    sha,
    *,
    port,
    origin=None,
    run=subprocess.run,
    http=httpx,
    sleep=time.sleep,
    clock=time.monotonic,
    launch_agents=LAUNCH_AGENTS,
    timeout=90,
) -> dict:
    """Prepare, swap in and verify release `sha`; roll back automatically on any failure.

    Returns {"sha", "result": "activated", "health", ...} on success and
    {"sha", "result": "rolled_back", "rolled_back": True, "reason", ...} when the new release
    failed verification but the previous one was restored and verified. Raises ActivationError
    when the release could not be prepared or the rollback itself did not verify.
    """
    home, repo = Path(home), Path(repo)
    sha = _check_sha(sha)
    origin = origin or default_origin(home, port)
    control = home / "control.plist"
    if not control.is_file():
        raise ActivationError(f"No login service plist at {control}; run install-service first")
    releases = read_releases(home)
    label = service_label(home)
    _log(home, f"activation of {sha} started (origin {origin}, port {port})")
    _event(home, "activation.started", sha=sha)
    try:
        release = prepare_release(repo, sha, run=run)
        nxt = write_plist(home, repo, release, port, origin, label, release_python(repo))
        ensure_launch_agent(home, label, launch_agents)
    except ActivationError as exc:
        _record(home, releases, sha, "failed", str(exc))
        _event(home, "activation.failed", sha=sha, reason=str(exc))
        _log(home, f"activation of {sha} failed before the swap: {exc}")
        raise
    previous = home / "control.plist.previous"
    shutil.copy2(control, previous)
    previous.chmod(0o600)
    try:
        old = _swap(run, home, label, nxt, sleep=sleep, clock=clock)
        nxt.unlink(missing_ok=True)
        checks = health(
            origin,
            home,
            timeout=timeout,
            http=http,
            sleep=sleep,
            clock=clock,
            previous_pid=old["pid"] if old else None,
        )
    except ActivationError as exc:
        reason = str(exc)
        _log(home, f"activation of {sha} failed after the swap: {reason}")
        _event(home, "activation.failed", sha=sha, reason=reason)
        try:
            restored = rollback(
                home,
                port=port,
                origin=origin,
                run=run,
                http=http,
                sleep=sleep,
                clock=clock,
                reason=reason,
                timeout=timeout,
                sha=sha,
                record=False,
            )
        except ActivationError as inner:
            _record(home, releases, sha, "failed", f"{reason}; rollback failed: {inner}")
            raise ActivationError(
                f"Release {sha[:12]} failed ({reason}) and the rollback did not verify: {inner}"
            ) from None
        _record(home, releases, sha, "rolled_back", reason)
        return {
            "sha": sha,
            "result": "rolled_back",
            "rolled_back": True,
            "reason": reason,
            "current": releases["current"],
            "health": restored["health"],
        }
    releases["previous"], releases["current"] = releases["current"], sha
    _record(home, releases, sha, "activated")
    _event(home, "activation.succeeded", sha=sha)
    removed = prune_releases(repo, {sha, releases["previous"] or ""}, run=run)
    _log(home, f"activation of {sha} verified healthy; pruned {removed or 'nothing'}")
    return {
        "sha": sha,
        "result": "activated",
        "rolled_back": False,
        "current": sha,
        "previous": releases["previous"],
        "release": str(release),
        "health": checks,
        "pruned": removed,
    }


# --------------------------------------------------------------------------- CLI


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent_arena.control.activation",
        description="Activate, roll back or inspect Arena releases for one state directory",
    )
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--repo", type=Path, help="canonical repository (required to activate)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("activate", help="check out, build, swap in and verify a commit")
    p.add_argument("sha")
    p.add_argument("--port", type=int)
    p.add_argument("--origin")
    p.add_argument("--timeout", type=float, default=90)
    p = sub.add_parser("rollback", help="restore the previous release and verify it")
    p.add_argument("--port", type=int)
    p.add_argument("--origin")
    p.add_argument("--timeout", type=float, default=90)
    sub.add_parser("status", help="print releases.json")
    args = parser.parse_args(argv)
    home = args.home.expanduser()
    if args.command == "status":
        print(json.dumps(read_releases(home), indent=2))
        return 0
    connection_path = home / "connection.json"
    try:
        connection = json.loads(connection_path.read_text())
    except (OSError, ValueError):
        connection = {}
    port = args.port or connection.get("port") or 8790
    origin = args.origin or default_origin(home, port)
    os.umask(0o077)
    try:
        if args.command == "activate":
            if not args.repo:
                parser.error("--repo is required to activate")
            result = activate(
                home,
                args.repo.expanduser(),
                args.sha,
                port=port,
                origin=origin,
                timeout=args.timeout,
            )
        else:
            result = rollback(home, port=port, origin=origin, timeout=args.timeout)
    except ActivationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if result.get("rolled_back") else 0


if __name__ == "__main__":
    raise SystemExit(main())
