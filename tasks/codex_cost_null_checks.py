"""Deterministic checks for tasks/codex-cost-null.md. Run with cwd = agent_reliability_lab.

Exit 0 = PASS, 1 = FAIL, 2 = UNKNOWN (evidence missing). Last stdout line is the reason.
Stdlib plus the lab's own code (imported from ./src); reads files, never writes.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

# A tracked run whose coder and reviewer calls all report a cost (claude_cli on both sides).
BASE_RUN = Path("results/runs/20260927T192610Z-26ffea4d")
SUMMARIZE = "src/agent_reliability/analysis/summarize.py"


def done(code: int, reason: str) -> None:
    print(reason)
    raise SystemExit(code)


def _git_status() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        done(2, f"git status failed: {exc}")
    return [line for line in out.splitlines() if line]


def _cost_sum(calls: list[dict], roles: set[str]) -> float:
    return round(sum(c["list_cost_usd"] for c in calls if c["role"] in roles), 6)


def check_metrics() -> None:
    """Coder calls without a reported cost must give a null coder cost, not 0.0."""

    sys.path.insert(0, "src")
    try:
        from agent_reliability.analysis.summarize import compute_metrics, render_summary
        from agent_reliability.traces.store import load_episodes

        base = load_episodes(BASE_RUN)
        meta = json.loads((BASE_RUN / "run.json").read_text())
    except Exception as exc:  # the lab or its tracked run is not importable/readable
        done(2, f"cannot load the lab or {BASE_RUN}: {type(exc).__name__}: {exc}")
    calls = [c for e in base for c in e["calls"]]
    coder = [c for c in calls if c["role"] == "coder"]
    if len(coder) < 2 or any(c.get("list_cost_usd") is None for c in calls):
        done(2, f"{BASE_RUN} no longer has a reported cost on every call")
    oversight_want = _cost_sum(calls, {"reviewer", "reviser"})

    def metrics(mutate) -> tuple[dict, str]:  # type: ignore[no-untyped-def]
        episodes = copy.deepcopy(base)
        seen = 0
        for episode in episodes:
            for call in episode["calls"]:
                if call["role"] == "coder":
                    mutate(call, seen)
                    seen += 1
        try:
            m = compute_metrics(episodes)
            return m, render_summary(meta, m)
        except Exception as exc:
            done(1, f"compute_metrics/render_summary raised {type(exc).__name__}: {exc}")
        raise AssertionError

    def drop_all(call: dict, _: int) -> None:
        call["list_cost_usd"] = None

    def drop_first(call: dict, index: int) -> None:
        if index == 0:
            call["list_cost_usd"] = None

    m, text = metrics(lambda call, index: None)
    got = m["overhead"]["coder_list_cost_usd"]
    if got != _cost_sum(calls, {"coder"}):
        done(1, f"all costs reported: coder_list_cost_usd is {got!r}, not the sum")

    for label, mutate in (
        ("no coder cost reported", drop_all),
        ("one coder cost missing", drop_first),
    ):
        m, text = metrics(mutate)
        got = m["overhead"]["coder_list_cost_usd"]
        if got is not None:
            done(1, f"{label}: overhead.coder_list_cost_usd is {got!r}, expected null")
        role = m["cost_by_role"]["coder"]["list_cost_usd"]
        if role is not None:
            done(1, f"{label}: cost_by_role.coder.list_cost_usd is {role!r}, expected null")
        if m["overhead"]["oversight_list_cost_usd"] != oversight_want:
            done(
                1,
                f"{label}: oversight cost changed to {m['overhead']['oversight_list_cost_usd']!r}",
            )
        line = next((ln for ln in text.splitlines() if "list-price" in ln), "")
        if re.search(r"coder \$\d", line):
            done(1, f"{label}: summary still prints a dollar figure for the coder: {line[:120]}")
    done(0, "null coder cost when any coder call lacks one; reported costs still sum")


def check_lab_test() -> None:
    """The fix must come with a lab test that mentions the null cost."""

    changed = [line[3:].strip('"') for line in _git_status()]
    tests = [p for p in changed if re.fullmatch(r"tests/[^/]+\.py", p)]
    if not tests:
        done(1, "no new or modified file under tests/")
    hits = [
        p
        for p in tests
        if "list_cost_usd" in (text := Path(p).read_text(errors="replace")) and "None" in text
    ]
    if not hits:
        done(1, f"changed tests ({', '.join(tests)}) do not assert a None list_cost_usd")
    done(0, f"null-cost test in {', '.join(hits)}")


def check_git_clean() -> None:
    allowed = [
        re.compile(rf"^ M {re.escape(SUMMARIZE)}$"),
        re.compile(r"^( M|\?\?) tests/[^/]+\.py$"),
        # left by earlier harness runs; untracked before this task
        re.compile(r"^\?\? STATE\.md$"),
        re.compile(r"^\?\? \.claude/"),
        re.compile(r"^\?\? configs/cross_review_v2_codex\.json$"),
        re.compile(r"^\?\? results/runs/[^/]+-codex-smoke/"),
    ]
    lines = _git_status()
    extra = [line for line in lines if not any(p.match(line) for p in allowed)]
    if extra:
        done(1, f"unexpected changes: {'; '.join(extra[:5])}")
    if not any(line.endswith(SUMMARIZE) for line in lines):
        done(1, f"{SUMMARIZE} is unchanged")
    done(0, f"{len(lines)} expected entries only")


COMMANDS = {"metrics": check_metrics, "lab-test": check_lab_test, "git-clean": check_git_clean}

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in COMMANDS:
        COMMANDS[sys.argv[1]]()
    else:
        done(2, f"usage: {Path(sys.argv[0]).name} {' | '.join(COMMANDS)}")
