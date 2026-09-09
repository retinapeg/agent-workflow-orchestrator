from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import agent_arena.orchestrator as orchestrator_module
from agent_arena.config import EngineerConfig, load_config
from agent_arena.orchestrator import ArenaOrchestrator


def test_main_thread_interrupt_cancels_parallel_cli_processes_and_marks_run_cancelled(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = arena_fixture["root"] / "cancelled-provider-must-not-finish"
    child = (
        f"import pathlib,time; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('escaped')"
    )
    config = load_config(arena_fixture["config"])
    config = replace(
        config,
        engineers=tuple(
            EngineerConfig(
                engineer_id=engineer.engineer_id,
                kind="generic_cli",
                model=None,
                timeout_seconds=20,
                options={"command": [sys.executable, "-c", child]},
            )
            for engineer in config.engineers
        ),
    )
    arena = ArenaOrchestrator(config, "hackathon")

    def interrupt_when_a_child_is_owned(futures: Any) -> Iterator[Any]:
        deadline = time.monotonic() + 2
        while arena.runner.active_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert arena.runner.active_count() > 0
        raise KeyboardInterrupt
        yield from ()

    monkeypatch.setattr(orchestrator_module, "as_completed", interrupt_when_a_child_is_owned)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        arena.run(arena_fixture["source"], arena_fixture["task"])
    assert time.monotonic() - started < 3
    assert arena.runner.active_count() == 0
    time.sleep(0.3)
    assert not marker.exists()
    runs = [path for path in config.run.artifact_root.iterdir() if path.is_dir()]
    assert len(runs) == 1
    run_data = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    assert run_data["state"] == "cancelled"
    assert (runs[0] / "manifest.json").is_file()
