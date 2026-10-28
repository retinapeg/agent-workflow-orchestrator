"""Disk hygiene for attempt workspaces on the Mac.

Every attempt clones its project's staging repository into ``<home>/workspaces/<attempt
id>`` and nothing removed them, so a busy Mac accumulates gigabytes. A workspace matters
only while its attempt runs and, for a build, until that build has been reviewed and
integrated. What matters afterwards (transcript, prompt, diffs, check output, result) lives
in ``<home>/attempts/<attempt id>/`` and is never touched here.

A workspace is prunable when all of these hold:

* it is a real directory named ``attempt_*`` directly under ``<home>/workspaces``; symbolic
  links and every other entry are reported, never followed or removed;
* its attempt is in the ledger and not live (not in ``engine.ACTIVE``) and its task is
  completed, cancelled or blocked (``PRUNABLE_TASK_STATES``);
* a retry would not read it: retrying a blocked review or director check fetches the build
  attempt's workspace again;
* it has been idle for ``days``: the latest of the attempt's start and end, its task's last
  change and the directory's own modification time.

``survey`` only reads. ``prune`` removes only with ``apply=True``: ``git worktree remove``
for a workspace registered as a worktree of an enrolled repository (then ``git worktree
prune``), otherwise ``shutil.rmtree``. ``UsageCache`` serves the totals to the phone and
walks the tree at most once per ``USAGE_TTL_SECONDS``.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .engine import ACTIVE, git
from .store import Store

PREFIX = "attempt_"
DEFAULT_DAYS = 7.0
DAY = 86400.0
# Finished for good. The scheduler also treats "interrupted", "needs_auth" and
# "needs_permission" as terminal, but those wait for the owner (an interrupted stop may not
# even be confirmed), so their workspaces stay.
PRUNABLE_TASK_STATES = frozenset({"completed", "cancelled", "blocked"})
# Retrying a blocked task in one of these phases fetches its build attempt's workspace.
RETRY_READS_BUILD = frozenset({"review", "direct"})
USAGE_TTL_SECONDS = 300.0
LOG = "maintenance.log"
_GIT_ERRORS = (ValueError, OSError, subprocess.TimeoutExpired)

# Why a workspace is kept, for the summary line of the dry run.
KEPT = {
    "live": "live",
    "unfinished": "whose task is not finished",
    "retry": "that a retry of a blocked review would read",
    "recent": "idle for less than {days}",
}


def workspace_root(home: Path) -> Path:
    return Path(home) / "workspaces"


def human(n: int | float) -> str:
    """Binary units, like the phone's byte readouts."""
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{round(n / (1 << 10))} KB"
    return f"{int(n)} B"


def days_text(days: float) -> str:
    return f"{days:g} day" + ("" if days == 1 else "s")


def _allocated(st: os.stat_result) -> int:
    blocks = getattr(st, "st_blocks", None)
    return int(blocks) * 512 if blocks is not None else int(st.st_size)


def disk_bytes(path: Path | str) -> int:
    """Bytes allocated on disk under ``path`` (``st_blocks``), the directory itself included.
    Symbolic links count as links and are never followed; a hard-linked file counts once."""
    try:
        top = os.lstat(path)
    except OSError:
        return 0
    total = _allocated(top)
    if not stat.S_ISDIR(top.st_mode):
        return total
    seen: set[tuple[int, int]] = set()
    stack = [os.fspath(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    directory = stat.S_ISDIR(st.st_mode)
                    if st.st_nlink > 1 and not directory:
                        if (st.st_dev, st.st_ino) in seen:
                            continue
                        seen.add((st.st_dev, st.st_ino))
                    total += _allocated(st)
                    if directory:
                        stack.append(entry.path)
        except OSError:
            continue
    return total


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _idle(seconds: float) -> str:
    if seconds >= DAY:
        return f"{seconds / DAY:.1f} days"
    return f"{seconds / 3600:.1f} hours"


def judge(
    attempt: dict[str, Any] | None,
    task: dict[str, Any] | None,
    mtime: float,
    days: float,
    now: float,
) -> dict[str, Any]:
    """The verdict for one workspace directory: ``prune`` or ``keep``, with why and when it
    was last active. Unknown attempts and tasks are always kept."""
    if attempt is None:
        return dict(
            verdict="keep",
            why="unknown_attempt",
            reason="not in the ledger (unknown attempt)",
            last_activity=mtime,
        )
    out: dict[str, Any] = dict(task_id=attempt.get("task_id"), attempt_state=attempt["state"])
    if task is None:
        return dict(
            out,
            verdict="keep",
            why="unknown_task",
            reason="its task is not in the ledger",
            last_activity=mtime,
        )
    last = max(
        _number(attempt.get("created")),
        _number(attempt.get("ended")),
        _number(task.get("updated")),
        mtime,
    )
    idle = now - last
    out.update(task_state=task["state"], last_activity=last, idle_days=round(idle / DAY, 2))
    if attempt["state"] in ACTIVE:
        return dict(out, verdict="keep", why="live", reason=f"live attempt ({attempt['state']})")
    if task["state"] not in PRUNABLE_TASK_STATES:
        return dict(out, verdict="keep", why="unfinished", reason=f"task is {task['state']}")
    if (
        task["state"] == "blocked"
        and task.get("phase") in RETRY_READS_BUILD
        and task.get("build_attempt") == attempt["id"]
        and _number(task.get("repairs")) < _number(task.get("max_repairs"))
    ):
        return dict(
            out,
            verdict="keep",
            why="retry",
            reason="a retry of this blocked task reviews this build again",
        )
    if idle < days * DAY:
        return dict(
            out,
            verdict="keep",
            why="recent",
            reason=f"idle {_idle(max(idle, 0))}, less than {days_text(days)}",
        )
    return dict(out, verdict="prune", why="prunable", reason=f"{task['state']}, idle {_idle(idle)}")


def survey(store: Store, days: float = DEFAULT_DAYS, now: float | None = None) -> dict[str, Any]:
    """Classify every entry under ``<home>/workspaces``. Changes nothing.

    ``workspaces`` lists every real ``attempt_*`` directory with its verdict; ``skipped``
    lists the other entries (symbolic links, other names), which are never touched."""
    now = time.time() if now is None else now
    base = workspace_root(store.home)
    report: dict[str, Any] = dict(
        root=str(base), days=days, refused=None, workspaces=[], skipped=[]
    )
    if base.is_symlink():
        report["refused"] = (
            f"{base} is a symbolic link; nothing outside the control home is followed or removed"
        )
        return _totals(report)
    if not base.is_dir():
        return _totals(report)
    with contextlib.closing(store.connect()) as db:
        db.execute("BEGIN")
        attempts = {a["id"]: a for a in store.rows("attempts", db)}
        tasks = {t["id"]: t for t in store.rows("tasks", db)}
        db.commit()
    with os.scandir(base) as listing:
        entries = sorted(listing, key=lambda e: e.name)
    for entry in entries:
        name = entry.name
        if entry.is_symlink():
            report["skipped"].append(
                dict(name=name, why="symlink", reason="symbolic link; not followed")
            )
            continue
        if not name.startswith(PREFIX) or not entry.is_dir(follow_symlinks=False):
            if not name.startswith("."):  # .DS_Store and friends are not worth a line
                report["skipped"].append(
                    dict(name=name, why="other", reason="not an attempt workspace")
                )
            continue
        try:
            mtime = entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        attempt = attempts.get(name)
        task = tasks.get(attempt["task_id"]) if attempt else None
        report["workspaces"].append(
            dict(
                name=name,
                path=entry.path,
                bytes=disk_bytes(entry.path),
                **judge(attempt, task, mtime, days, now),
            )
        )
    return _totals(report)


def _totals(report: dict[str, Any]) -> dict[str, Any]:
    items = report["workspaces"]
    plan = [w for w in items if w["verdict"] == "prune"]
    report.update(
        count=len(items),
        bytes=sum(w["bytes"] for w in items),
        prunable_count=len(plan),
        prunable_bytes=sum(w["bytes"] for w in plan),
    )
    return report


def usage(store: Store, days: float = DEFAULT_DAYS, now: float | None = None) -> dict[str, Any]:
    """Totals for the Device dialog (no paths)."""
    report = survey(store, days, now)
    times = [w["last_activity"] for w in report["workspaces"] if w.get("last_activity")]
    return dict(
        count=report["count"],
        bytes=report["bytes"],
        prunable_count=report["prunable_count"],
        prunable_bytes=report["prunable_bytes"],
        oldest=min(times) if times else None,
        days=days,
        measured=time.time(),
    )


class UsageCache:
    """``usage()`` computed at most once per ``ttl`` seconds; concurrent callers share one walk."""

    def __init__(
        self,
        store: Store,
        ttl: float = USAGE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.ttl = ttl
        self.clock = clock
        self.walks = 0
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._at = 0.0

    def get(self) -> dict[str, Any]:
        with self._lock:
            if self._value is None or self.clock() - self._at >= self.ttl:
                self._value = usage(self.store)
                self._at = self.clock()
                self.walks += 1
            return dict(self._value)


def registered_worktrees(store: Store) -> dict[str, Path]:
    """Real path of every worktree registered in an enrolled project's source or staging
    repository, mapped to that repository (``git worktree list --porcelain``)."""
    owners: dict[str, Path] = {}
    for project in store.rows("projects"):
        for key in ("repo", "staging"):
            repo = project.get(key)
            if not repo or not Path(repo).is_dir():
                continue
            try:
                listing = git(repo, "worktree", "list", "--porcelain")
            except _GIT_ERRORS:
                continue
            for line in listing.splitlines():
                if line.startswith("worktree "):
                    owners.setdefault(os.path.realpath(line[len("worktree ") :]), Path(repo))
    return owners


def _rmtree(path: Path) -> None:
    """``shutil.rmtree``; retried once after making every directory writable (a check may
    leave a read-only directory behind). Never follows symbolic links."""
    try:
        shutil.rmtree(path)
    except PermissionError:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
        for parent, dirs, _files in os.walk(path):
            for name in dirs:
                child = os.path.join(parent, name)
                if not os.path.islink(child):
                    with contextlib.suppress(OSError):
                        os.chmod(child, 0o700)
        shutil.rmtree(path)


def _log(home: Path, message: str) -> None:
    """Append a timestamped line to ``<home>/maintenance.log`` (the activation.log format)."""
    try:
        with (Path(home) / LOG).open("a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + message + "\n")
    except OSError:
        pass


def _recheck(store: Store, item: dict[str, Any], days: float, now: float) -> dict[str, Any]:
    """The verdict again from fresh ledger rows, right before removing (a retry or a new
    attempt may have changed things since the survey)."""
    try:
        attempt = store.get("attempts", item["name"])
    except KeyError:
        attempt = None
    try:
        task = store.get("tasks", attempt["task_id"]) if attempt else None
    except KeyError:
        task = None
    mtime = os.lstat(item["path"]).st_mtime
    return judge(attempt, task, mtime, days, now)


def prune(
    store: Store, days: float = DEFAULT_DAYS, apply: bool = False, now: float | None = None
) -> dict[str, Any]:
    """Survey, then (only with ``apply``) remove the prunable workspaces. Returns the report:
    the survey plus ``applied``, ``removed``, ``removed_count``, ``removed_bytes``, ``errors``."""
    now = time.time() if now is None else now
    report = survey(store, days, now)
    report.update(applied=bool(apply), removed=[], removed_count=0, removed_bytes=0, errors=[])
    plan = [w for w in report["workspaces"] if w["verdict"] == "prune"]
    owners = registered_worktrees(store) if plan else {}
    for item in plan:
        owner = owners.get(os.path.realpath(item["path"]))
        item["method"] = "git worktree remove" if owner else "delete"
        if owner:
            item["repository"] = str(owner)
    if not apply or report["refused"]:
        return report
    base = os.path.realpath(report["root"])
    touched: set[Path] = set()
    for item in plan:
        path = Path(item["path"])
        try:
            if path.is_symlink() or not path.is_dir():
                raise OSError("no longer a plain directory; left alone")
            if os.path.dirname(os.path.realpath(path)) != base:
                raise OSError("resolves outside the workspaces directory; left alone")
            fresh = _recheck(store, item, days, now)
            if fresh["verdict"] != "prune":
                item.update(fresh, reason=fresh["reason"] + " (changed since the survey)")
                continue
            if item.get("repository"):
                repo = Path(item["repository"])
                touched.add(repo)
                try:
                    git(repo, "worktree", "remove", "--force", str(path))
                except _GIT_ERRORS as exc:
                    item["method"] = "delete (git worktree remove failed: " + str(exc)[:200] + ")"
            if path.exists():
                _rmtree(path)
        except OSError as exc:
            report["errors"].append(dict(name=item["name"], error=str(exc)[:500]))
            continue
        report["removed"].append(item["name"])
        report["removed_bytes"] += item["bytes"]
    for repo in sorted(touched):
        with contextlib.suppress(*_GIT_ERRORS):
            git(repo, "worktree", "prune")
    report["removed_count"] = len(report["removed"])
    _record(store, report)
    return _totals(report)


def _record(store: Store, report: dict[str, Any]) -> None:
    count, size, errors = report["removed_count"], report["removed_bytes"], report["errors"]
    message = (
        f"Removed {count} finished build workspace{'' if count == 1 else 's'} "
        f"({human(size)}) idle for at least {days_text(report['days'])}"
        + (f"; {len(errors)} could not be removed" if errors else "")
    )
    if count or errors:
        with store.transaction() as db:
            store.event(
                db,
                "workspaces.pruned",
                count=count,
                bytes=size,
                days=report["days"],
                errors=len(errors),
                reason=message,
            )
    _log(
        store.home,
        "prune-workspaces: "
        + message
        + (" [" + ", ".join(report["removed"]) + "]" if report["removed"] else "")
        + "".join(f"; {e['name']}: {e['error']}" for e in errors),
    )


def render(report: dict[str, Any]) -> str:
    """The plain-text summary printed by ``prune-workspaces``."""
    if report["refused"]:
        return "Refused: " + report["refused"]
    days = days_text(report["days"])
    count = report["count"]
    lines = [
        f"{count} workspace{'' if count == 1 else 's'} in {report['root']} "
        f"using {human(report['bytes'])}."
    ]
    plan = [w for w in report["workspaces"] if w.get("method") or w["verdict"] == "prune"]
    removed = set(report.get("removed", []))
    if not plan:
        lines.append(f"Nothing to remove: no finished build's workspace has been idle for {days}.")
    elif report["applied"]:
        lines.append(
            f"Removed {report['removed_count']} of {len(plan)} prunable "
            f"(finished builds idle for at least {days}), {human(report['removed_bytes'])}:"
        )
    else:
        lines.append(
            f"Would remove {len(plan)} (finished builds idle for at least {days}), "
            f"{human(sum(w['bytes'] for w in plan))}:"
        )
    for w in plan:
        lines.append(
            f"  {w['name']}  {human(w['bytes']):>9}  {w.get('task_state', ''):<9}  "
            f"idle {w.get('idle_days', 0):.1f} days  {w.get('method', 'delete')}"
            + (f" ({w['repository']})" if w.get("repository") else "")
            + (f"  [kept: {w['reason']}]" if w["verdict"] != "prune" else "")
            + (
                "  [not removed]"
                if report["applied"] and w["verdict"] == "prune" and w["name"] not in removed
                else ""
            )
        )
    kept = Counter(
        w["why"] for w in report["workspaces"] if w["verdict"] == "keep" and w["why"] in KEPT
    )
    if kept:
        lines.append(
            "Keeping "
            + ", ".join(f"{n} {KEPT[why].format(days=days)}" for why, n in sorted(kept.items()))
            + "."
        )
    odd = [w for w in report["workspaces"] if w["why"] in {"unknown_attempt", "unknown_task"}]
    if odd or report["skipped"]:
        lines.append("Left alone:")
        for w in odd + report["skipped"]:
            lines.append(f"  {w['name']}  {w['reason']}")
    for e in report.get("errors", []):
        lines.append(f"Could not remove {e['name']}: {e['error']}")
    if report["applied"] and report["removed_count"]:
        lines.append("Attempt evidence (transcripts, diffs, results) in attempts/ is untouched.")
    elif plan and not report["applied"]:
        lines.append("Dry run: nothing was removed. Add --apply to remove them.")
    return "\n".join(lines)
