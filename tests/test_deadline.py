from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent_arena.config import load_config
from agent_arena.errors import DeadlineExceeded
from agent_arena.orchestrator import ArenaOrchestrator


def test_whole_run_deadline_stops_later_evaluation_and_winner_export(
    arena_fixture: dict[str, Path],
) -> None:
    config = load_config(arena_fixture["config"])
    slow_check = replace(
        config.checks[0],
        command=(sys.executable, "-c", "import time; time.sleep(5)"),
        timeout_seconds=20,
    )
    config = replace(config, run=replace(config.run, max_run_seconds=5), checks=(slow_check,))

    with pytest.raises(DeadlineExceeded, match="deadline"):
        ArenaOrchestrator(config, "hackathon").run(arena_fixture["source"], arena_fixture["task"])

    runs = [path for path in config.run.artifact_root.iterdir() if path.is_dir()]
    assert len(runs) == 1
    run_data = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    assert run_data["state"] == "failed"
    assert "deadline" in run_data["error"]
    assert not (runs[0] / "decision.json").exists()
    assert not (runs[0] / "winner.patch").exists()
    terminating_processes = list(runs[0].glob("evaluations/*/initial/tests/process.json"))
    assert terminating_processes
    process = json.loads(terminating_processes[0].read_text(encoding="utf-8"))
    assert process["timed_out"] is True
    assert (terminating_processes[0].parent / "stdout.log").is_file()
    ephemeral = runs[0] / "private/worktrees/ephemeral"
    assert not ephemeral.exists() or not any(ephemeral.iterdir())
