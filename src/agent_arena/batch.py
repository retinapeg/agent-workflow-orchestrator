"""Queues: run a list of small tasks one after another, each benchmarked as its own run.

    agent-arena queue tasks/physics-v3.queue.toml
    agent-arena queue tasks/a.md tasks/b.md --repo PATH

A long overnight job is split into small TASK.md files, each with its own checks, so every step
gets its own verified/failed result, time, tokens and cost in the ledger instead of one opaque
"run". The queue stops at the first step that isn't verified (unless the queue file or
--keep-going says otherwise), because later steps usually build on earlier ones. Touching
~/.agent-arena/STOP ends the queue cleanly between steps.

Queue file (TOML):
    name = "physics-v3"
    description = "..."
    repo = "~/path/to/repo"         # optional; every step runs here
    stop_on_failure = true          # optional, default true
    tasks = ["physics-v3/01-prereg", "physics-v3/02-tasks"]   # relative to the queue file
"""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ArenaError

STOP_FILE = Path("~/.agent-arena/STOP").expanduser()


def _arena_home() -> Path:  # resolved at call time so HOME changes (tests) are respected
    return Path("~/.agent-arena").expanduser()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


def _latest_run(task: Path, since: float) -> Path | None:
    runs = [
        d
        for d in (_arena_home() / "runs").glob(f"*-{task.stem}-*")
        if (d / "result.json").is_file() and d.stat().st_mtime >= since - 1
    ]
    return max(runs, key=lambda d: d.stat().st_mtime) if runs else None


def _git(repo: Path | None, *args: str) -> str:
    if repo is None:
        return ""
    import subprocess

    out = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo.expanduser()), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip()


_VERDICTS = {0: "verified", 1: "FAILED a check", 2: "UNKNOWN check", 3: "harness error"}


def journal_entry(
    index: int,
    total: int,
    task: Path,
    code: int,
    started: str,
    seconds: float,
    run_dir: Path | None,
    repo: Path | None,
) -> str:
    """One timeline entry: what ran, how long, what passed or failed, what the agent said it did,
    which files changed, and where the full evidence lives. Written for a later AI review."""

    import json

    lines = [
        f"## Step {index}/{total}: {task.stem}",
        "",
        f"- started: {started}",
        f"- finished: {_now()} ({seconds / 60:.1f} min)",
        f"- exit code: {code} ({_VERDICTS.get(code, 'other')})",
    ]
    if run_dir is not None:
        data = json.loads((run_dir / "result.json").read_text("utf-8"))
        meta = data.get("metadata", {})
        inv = data.get("invocation", {})
        total_cost = (data.get("cost") or {}).get("total") or {}
        lines += [
            f"- agent: {inv.get('provider')} model={inv.get('model_declared')} "
            f"effort={meta.get('effort')} tier={meta.get('service_tier')} "
            f"access={'FULL' if meta.get('full_access') else 'sandboxed'}",
            f"- cost: ${total_cost.get('cost_usd')} API-equivalent "
            f"({total_cost.get('priced_calls')}/{total_cost.get('total_calls')} calls priced)",
            f"- run record: {run_dir} (transcript.jsonl, report.txt, result.json)",
            "",
            "Checks:",
        ]
        for check in data.get("checks", []):
            lines.append(f"- {check['status']}: {check['name']}: {check.get('detail', '')}")
        warnings = data.get("warnings") or []
        if warnings:
            lines += ["", "Warnings / failure points:"] + [f"- {w}" for w in warnings]
        final = (data.get("agent_final_text") or "").strip()
        if final:
            lines += ["", "Agent's own summary:", "", "```", final[-3000:], "```"]
    diff = _git(repo, "diff", "--stat", "HEAD")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
    if diff or untracked:
        lines += [
            "",
            "Working tree after this step (cumulative vs HEAD):",
            "",
            "```",
            diff or "(no tracked changes)",
            *(f"new: {u}" for u in untracked.splitlines()[:40]),
            "```",
        ]
    return "\n".join(lines) + "\n\n"


_QUEUE_KEYS = {"name", "description", "repo", "stop_on_failure", "tasks"}


@dataclass(frozen=True)
class Queue:
    name: str
    description: str
    repo: Path | None
    stop_on_failure: bool
    tasks: tuple[Path, ...]


def load_queue(path: Path) -> Queue:
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ArenaError(f"{path}: cannot read queue file: {exc}") from exc
    unknown = set(raw) - _QUEUE_KEYS
    if unknown:
        raise ArenaError(f"{path}: unknown queue keys: {sorted(unknown)}")
    names = raw.get("tasks")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ArenaError(f"{path}: tasks must be a non-empty list of task names")
    tasks = []
    for name in names:
        task = (path.parent / (name if name.endswith(".md") else f"{name}.md")).resolve()
        if not task.is_file():
            raise ArenaError(f"{path}: task not found: {name}")
        tasks.append(task)
    repo = raw.get("repo")
    return Queue(
        name=str(raw.get("name") or path.name.removesuffix(".queue.toml")),
        description=str(raw.get("description", "")),
        repo=Path(repo).expanduser() if isinstance(repo, str) and repo.strip() else None,
        stop_on_failure=bool(raw.get("stop_on_failure", True)),
        tasks=tuple(tasks),
    )


def main(argv: list[str]) -> int:
    from .ledger import after_run
    from .run_mode import main as run_main

    parser = argparse.ArgumentParser(prog="agent-arena queue")
    parser.add_argument(
        "targets", nargs="+", type=Path, help="a .queue.toml file or task .md files"
    )
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--agent", choices=["claude", "codex"])
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--service-tier", choices=["standard", "fast", "ultrafast"])
    parser.add_argument("--keep-going", action="store_true", help="don't stop at a failed step")
    args = parser.parse_args(argv)

    try:
        if len(args.targets) == 1 and args.targets[0].name.endswith(".queue.toml"):
            queue = load_queue(args.targets[0])
        else:
            missing = [t for t in args.targets if not t.is_file()]
            if missing:
                raise ArenaError(f"task not found: {missing[0]}")
            queue = Queue("ad-hoc", "", None, True, tuple(t.resolve() for t in args.targets))
    except ArenaError as exc:
        print(f"agent-arena queue: {exc}", file=sys.stderr)
        return 3
    repo = args.repo or queue.repo
    stop_on_failure = queue.stop_on_failure and not args.keep_going
    extra: list[str] = []
    for flag in ("agent", "model", "effort", "service_tier"):
        if getattr(args, flag):
            extra += [f"--{flag.replace('_', '-')}", getattr(args, flag)]

    total = len(queue.tasks)
    journals = _arena_home() / "journals"
    journals.mkdir(parents=True, exist_ok=True)
    journal = journals / f"{queue.name}-{time.strftime('%Y%m%dT%H%M%S')}.md"
    journal.write_text(
        f"# Build journal: {queue.name}\n\n"
        f"- repo: {repo}\n- started: {_now()}\n- steps: {total}\n"
        f"- branch: {_git(repo, 'branch', '--show-current')}  "
        f"HEAD: {_git(repo, 'rev-parse', '--short', 'HEAD')}\n"
        f"- overrides: {' '.join(extra) or 'none'}\n\n"
        "A timeline of the build: one entry per step with timing, checks, failure points, the "
        "agent's own summary and the files changed. The repo's docs/BUILD_LOG.md, "
        "docs/DECISIONS.md and docs/ERROR_LOG.md hold the agent's side of the story.\n\n",
        "utf-8",
    )
    print(f"journal: {journal}", flush=True)
    print(f"QUEUE {queue.name}: {total} steps" + (f" in {repo}" if repo else ""), flush=True)
    results: list[tuple[str, int, float]] = []
    for index, task in enumerate(queue.tasks, start=1):
        if STOP_FILE.exists():
            print(f"\nSTOP file found ({STOP_FILE}); ending the queue before step {index}.")
            break
        print(f"\n{'=' * 72}\nSTEP {index}/{total}: {task.stem}\n{'=' * 72}", flush=True)
        run_args = [str(task), *(["--repo", str(repo)] if repo else []), *extra]
        started = time.monotonic()
        started_wall, started_at = time.time(), _now()
        code = run_main(run_args)
        after_run(run_args)
        results.append((task.stem, code, time.monotonic() - started))
        try:
            with journal.open("a", encoding="utf-8") as handle:
                handle.write(
                    journal_entry(
                        index,
                        total,
                        task,
                        code,
                        started_at,
                        time.monotonic() - started,
                        _latest_run(task, started_wall),
                        repo,
                    )
                )
        except (OSError, ValueError) as exc:  # the journal must never stop the queue
            print(f"journal: entry not written: {exc}", file=sys.stderr)
        sys.stdout.flush()
        if code != 0 and stop_on_failure:
            print(f"\nStep {index} was not verified (exit {code}); stopping the queue here.")
            break

    labels = {0: "verified", 1: "FAIL", 2: "UNKNOWN", 3: "harness error"}
    print(f"\n{'=' * 72}\nQUEUE SUMMARY: {queue.name}")
    for name, code, seconds in results:
        print(f"  {labels.get(code, f'exit {code}'):<14} {seconds:7.0f}s  {name}")
    skipped = [t.stem for t in queue.tasks[len(results) :]]
    for name in skipped:
        print(f"  {'not run':<14} {'':>8}  {name}")
    verified = sum(1 for _, code, _ in results if code == 0)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(f"## Queue finished {_now()}: {verified}/{total} steps verified\n")
    print(f"journal: {journal}")
    print(f"  => {verified}/{total} steps verified; each step is its own row in the ledger")
    return 0 if verified == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
