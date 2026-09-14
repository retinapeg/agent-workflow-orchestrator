from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_arena.adaptive import (
    AdaptiveController,
    adaptive_status,
    request_stop,
    resolve_adaptive_run,
)
from agent_arena.audit import AuditStore, verify_manifest
from agent_arena.cli import main
from agent_arena.config import load_config
from agent_arena.errors import ArenaError
from agent_arena.integration import integrate_last_green

from .conftest import git


def _plan(
    task_id: str,
    task: str,
    path: str,
    *,
    beat: str | None = None,
) -> dict[str, object]:
    return {
        "action": "task",
        "task_id": task_id,
        "task": task,
        "rationale": "This is the next smallest evidence-backed gap.",
        "acceptance": "The configured coordinator check passes with the intended change.",
        "owned_paths": [path],
        "demo_beat": beat,
        "blocker": None,
        "freeze": "none",
    }


def _done() -> dict[str, object]:
    return {
        "action": "done",
        "task_id": "",
        "task": "",
        "rationale": "The goal and configured acceptance are satisfied.",
        "acceptance": "",
        "owned_paths": [],
        "demo_beat": None,
        "blocker": None,
        "freeze": "none",
    }


def _blocked(reason: str) -> dict[str, object]:
    return {
        "action": "blocked",
        "task_id": "",
        "task": "",
        "rationale": reason,
        "acceptance": "",
        "owned_paths": [],
        "demo_beat": None,
        "blocker": reason,
        "freeze": "none",
    }


def _write_adaptive_scripts(
    arena_fixture: dict[str, Path],
    plans: list[dict[str, object]],
    implementations: list[dict[str, object]],
) -> None:
    config_dir = arena_fixture["config"].parent
    (config_dir / "claude.json").write_text(
        json.dumps({"review": [{"response": plan} for plan in plans]}), encoding="utf-8"
    )
    (config_dir / "codex.json").write_text(
        json.dumps({"implement": implementations}), encoding="utf-8"
    )


def test_engineering_adaptive_run_selects_two_tasks_then_done(
    arena_fixture: dict[str, Path],
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [
            _plan("fix-add", "Correct integer addition.", "calculator.py"),
            _plan("record-limit", "Record the intentionally tiny scope.", "SCOPE.md"),
            _done(),
        ],
        [
            {
                "text": "Corrected the shared addition implementation and retained focused scope.",
                "operations": [
                    {
                        "op": "write",
                        "path": "calculator.py",
                        "content": (
                            "def add(left: int, right: int) -> int:\n    return left + right\n"
                        ),
                    }
                ],
            },
            {
                "text": "Recorded the verified scope without adding another implementation layer.",
                "operations": [
                    {"op": "write", "path": "SCOPE.md", "content": "# Scope\n\nAddition only.\n"}
                ],
            },
        ],
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    run_dir = AdaptiveController(config, "engineering", max_steps=5).run(
        arena_fixture["source"], "Make addition correct and leave the scope explicit."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "complete"
    assert [item["task_id"] for item in status["accepted_tasks"]] == [
        "fix-add",
        "record-limit",
    ]
    assert status["last_green_sha"] != baseline
    assert (run_dir / "last-green.bundle").is_file()
    assert git(arena_fixture["source"], "rev-parse", "HEAD") == baseline
    assert git(arena_fixture["source"], "status", "--porcelain=v1") == ""
    verify_manifest(run_dir)

    integrated = arena_fixture["root"] / "adaptive-integrated"
    receipt = integrate_last_green(
        run_dir, arena_fixture["source"], "arena/adaptive-result", integrated
    )
    assert receipt["last_green_tree"] == git(integrated, "rev-parse", "HEAD^{tree}")
    assert "return left + right" in (integrated / "calculator.py").read_text()
    assert (integrated / "SCOPE.md").is_file()
    assert git(arena_fixture["source"], "rev-parse", "HEAD") == baseline


def test_hackathon_adaptive_run_advances_a_named_demo_beat(
    arena_fixture: dict[str, Path],
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [
            _plan("make-beat-real", "Make the addition beat pass.", "calculator.py", beat="Beat 1"),
            _done(),
        ],
        [
            {
                "text": "Made Beat 1 runnable and kept the demo on the local deterministic path.",
                "operations": [
                    {
                        "op": "write",
                        "path": "calculator.py",
                        "content": (
                            "def add(left: int, right: int) -> int:\n    return left + right\n"
                        ),
                    }
                ],
            }
        ],
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    run_dir = AdaptiveController(config, "hackathon", max_steps=3).run(
        arena_fixture["source"], "Deliver the literal three-minute demo."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "complete"
    assert status["accepted_tasks"][0]["demo_beat"] == "Beat 1"
    assert status["last_green_sha"] != baseline
    verify_manifest(run_dir)


def test_two_rejected_steps_preserve_last_green(arena_fixture: dict[str, Path]) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [
            _plan("bad-one", "Add an unrelated note without fixing behavior.", "bad-one.txt"),
            _plan("bad-two", "Try another note without fixing behavior.", "bad-two.txt"),
            _done(),
        ],
        [
            {
                "text": "Produced a candidate, but the coordinator must decide whether it works.",
                "operations": [{"op": "write", "path": "bad-one.txt", "content": "no fix\n"}],
            },
            {
                "text": "Produced a second candidate for independent coordinator verification.",
                "operations": [{"op": "write", "path": "bad-two.txt", "content": "still no fix\n"}],
            },
        ],
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    run_dir = AdaptiveController(config, "engineering", max_steps=5).run(
        arena_fixture["source"], "Make addition correct."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "blocked"
    assert status["last_green_sha"] == baseline
    assert len(status["rejected_tasks"]) == 2
    assert all(
        "required check failed: tests" in item["reasons"] for item in status["rejected_tasks"]
    )
    assert (run_dir / "engineers/codex/step-1/candidate.patch").is_file()
    assert (run_dir / "engineers/codex/step-2/candidate.patch").is_file()
    verify_manifest(run_dir)


def test_timed_out_writer_is_rejected_and_last_green_is_preserved(
    arena_fixture: dict[str, Path],
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [
            _plan("timed-out", "Attempt the bounded fix.", "calculator.py"),
            _blocked("The bounded writer timed out; new authority is required."),
        ],
        [
            {
                "status": "failed",
                "text": "The bounded writer returned only partial evidence before its deadline.",
                "error": "scripted writer timeout",
            }
        ],
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    run_dir = AdaptiveController(config, "engineering", max_steps=4).run(
        arena_fixture["source"], "Make addition correct."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "blocked"
    assert status["last_green_sha"] == baseline
    assert status["rejected_tasks"][0]["candidate_commit"]
    assert "scripted writer timeout" in status["rejected_tasks"][0]["reasons"]
    verify_manifest(run_dir)


def test_status_and_stop_are_explicit_and_mode_scoped(
    arena_fixture: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    config = load_config(arena_fixture["config"])
    run_id = "adaptive-stop-test"
    audit = AuditStore(config.run.artifact_root / run_id, run_id)
    now = datetime.now(UTC)
    audit.update_run(
        controller="adaptive",
        mode="engineering",
        state="planning",
        started_at=now.isoformat(),
        deadline_at=(now + timedelta(minutes=10)).isoformat(),
        accepted_tasks=[],
        active_providers=[],
        last_green_sha="a" * 40,
        blocker=None,
        next_action="select one bounded task",
        freeze="none",
    )

    assert main(["engineering", "status", str(audit.run_dir), "--config", str(config.path)]) == 0
    assert "Last green" in capsys.readouterr().out
    assert main(["engineering", "stop", str(audit.run_dir), "--config", str(config.path)]) == 0
    assert adaptive_status(audit.run_dir)["stop_requested"] is True
    assert "Stop: requested" in capsys.readouterr().out
    with pytest.raises(ArenaError, match="already terminal"):
        audit.update_run(state="complete")
        request_stop(audit.run_dir)


def test_background_start_returns_while_controller_finishes(
    arena_fixture: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _write_adaptive_scripts(arena_fixture, [_done()], [])
    config = load_config(arena_fixture["config"])

    assert (
        main(
            [
                "engineering",
                "start",
                "Confirm the current accepted repository is sufficient.",
                "--repo",
                str(arena_fixture["source"]),
                "--config",
                str(config.path),
                "--background",
            ]
        )
        == 0
    )
    assert "Run:" in capsys.readouterr().out
    run_dir = resolve_adaptive_run(config, "engineering")
    deadline = time.monotonic() + 10
    while adaptive_status(run_dir)["state"] not in {"complete", "failed"}:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    status = adaptive_status(run_dir)
    assert status["state"] == "complete"
    assert status["launch_log"]
    assert Path(status["launch_log"]).is_file()
    verify_manifest(run_dir)
