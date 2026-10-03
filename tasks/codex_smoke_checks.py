"""Deterministic checks for tasks/codex-smoke.md. Run with cwd = agent_reliability_lab.

Exit 0 = PASS, 1 = FAIL, 2 = UNKNOWN (evidence missing). Last stdout line is the reason.
Stdlib only; reads files, never writes.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

LABEL = "codex-smoke"
V1, V2 = Path("configs/cross_review_v1.json"), Path("configs/cross_review_v2_codex.json")
WANT_MODELS = {"gpt-6-sol", "claude-sonnet-5"}
STATE_FACTS = {
    "commits pushed": r"push",
    "reviewer pinned": r"claude-sonnet-5",
    "medium effort": r"medium",
    "codex model declared": r"declared",
    "tool loop only live with claude_cli": r"claude_cli",
}


def done(code: int, reason: str) -> None:
    print(reason)
    raise SystemExit(code)


def run_dir() -> Path:
    runs = sorted(Path("results/runs").glob(f"*-{LABEL}"))
    if not runs:
        done(2, f"no results/runs/*-{LABEL} directory")
    return runs[-1]


def episodes() -> list[dict]:
    path = run_dir() / "episodes.jsonl"
    if not path.is_file():
        done(2, f"{path} missing")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        done(2, f"{path} is empty")
    return rows


def calls() -> list[dict]:
    return [c for e in episodes() for c in (e.get("calls") or [])]


def check_log(path: str) -> None:
    log = Path(path)
    if not log.is_file():
        done(2, f"{path} not found")
    text = log.read_text(errors="replace")
    if "BATCH STOPPED" in text:
        done(1, "log contains BATCH STOPPED")
    done(0, f"no BATCH STOPPED in {log.stat().st_size} bytes")


def check_config() -> None:
    if not V2.is_file():
        done(2, f"{V2} missing")
    v1, v2 = json.loads(V1.read_text()), json.loads(V2.read_text())
    reviewer = (v2.get("reviewer") or {}).get("model")
    if reviewer != "claude-sonnet-5":
        done(1, f"reviewer model is {reviewer!r}")
    changed = sorted(k for k in set(v1) | set(v2) if v1.get(k) != v2.get(k))
    if changed != ["coder", "repetitions", "reviewer"]:
        done(1, f"keys changed vs v1: {changed}")
    reviewer_diff = sorted(
        k
        for k in set(v1["reviewer"]) | set(v2["reviewer"])
        if v1["reviewer"].get(k) != v2["reviewer"].get(k)
    )
    if reviewer_diff != ["model"]:
        done(1, f"reviewer fields changed vs v1: {reviewer_diff}")
    done(0, "reviewer=claude-sonnet-5; changed vs v1: coder, repetitions, reviewer.model")


def check_state() -> None:
    state = Path("STATE.md")
    if not state.is_file():
        done(1, "STATE.md missing")
    text = state.read_text(errors="replace")
    missing = [name for name, pattern in STATE_FACTS.items() if not re.search(pattern, text, re.I)]
    if missing:
        done(1, f"STATE.md lacks: {', '.join(missing)}")
    done(0, "all five facts present")


def check_episodes_complete() -> None:
    rows = episodes()
    meta = json.loads((run_dir() / "run.json").read_text())
    expected = len(meta.get("tasks") or {}) * int(meta["config"].get("repetitions", 1))
    bad = [f"{e['episode_id']}={e.get('status')}" for e in rows if e.get("status") != "complete"]
    if bad:
        done(1, f"not complete: {', '.join(bad)}")
    if len(rows) != expected:
        done(1, f"{len(rows)} episodes, expected {expected}")
    done(0, f"{len(rows)}/{expected} episodes complete")


def check_results_present() -> None:
    rows = episodes()
    bad = [
        e["episode_id"]
        for e in rows
        if not isinstance(e.get("visible"), dict)
        or "passed" not in e["visible"]
        or not isinstance(e.get("hidden"), dict)
        or "passed" not in e["hidden"]
    ]
    if bad:
        done(1, f"missing visible/hidden in {', '.join(bad)}")
    hidden = sum(1 for e in rows if e["hidden"]["passed"])
    done(0, f"present in all {len(rows)}; hidden passed {hidden}/{len(rows)}")


def check_coder_ok() -> None:
    coder = [c for c in calls() if c.get("role") == "coder"]
    if not coder:
        done(2, "no coder calls recorded")
    bad = [c.get("failure") for c in coder if c.get("failure") is not None]
    if bad:
        done(1, f"{len(bad)}/{len(coder)} coder calls failed, first: {str(bad[0])[:120]}")
    done(0, f"{len(coder)} coder calls, failure=null")


def check_usage_present() -> None:
    all_calls = calls()
    bad = [c.get("role") for c in all_calls if not c.get("usage")]
    if bad:
        done(1, f"{len(bad)}/{len(all_calls)} calls without usage")
    done(0, f"usage on all {len(all_calls)} calls")


def check_models_used() -> None:
    seen = {m for c in calls() for m in (c.get("models_used") or [])}
    if seen != WANT_MODELS:
        done(1, f"models_used = {sorted(seen)}")
    done(0, f"models_used = {sorted(seen)}")


def check_git_clean() -> None:
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        done(2, f"git status failed: {exc}")
    allowed = [
        re.compile(rf"^results/runs/[^/]+-{LABEL}/$"),
        re.compile(r"^STATE\.md$"),
        re.compile(r"^configs/cross_review_v2_codex\.json$"),
        re.compile(r"^\.claude/$"),
    ]
    extra = [
        line
        for line in out.splitlines()
        if line and not any(p.match(line[3:].strip('"')) for p in allowed)
    ]
    if extra:
        done(1, f"unexpected changes: {'; '.join(extra[:5])}")
    done(0, f"{len(out.splitlines())} expected entries only")


COMMANDS = {
    "config": check_config,
    "state": check_state,
    "episodes-complete": check_episodes_complete,
    "results-present": check_results_present,
    "coder-ok": check_coder_ok,
    "usage-present": check_usage_present,
    "models-used": check_models_used,
    "git-clean": check_git_clean,
}

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "log":
        check_log(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]]()
    else:
        done(2, f"usage: {Path(sys.argv[0]).name} log PATH | {' | '.join(COMMANDS)}")
