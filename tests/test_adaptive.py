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
from agent_arena.adaptive import _plan as _validate_plan
from agent_arena.audit import AuditStore, rebuild_manifest, verify_manifest
from agent_arena.cli import main
from agent_arena.config import load_config
from agent_arena.errors import ArenaError, DeadlineExceeded, IntegrityError, ProviderError
from agent_arena.evaluator import Evaluator
from agent_arena.gitops import RepositoryManager
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
    }


def _write_adaptive_scripts(
    arena_fixture: dict[str, Path],
    plans: list[dict[str, object]],
    implementations: list[dict[str, object]],
    *,
    mode: str = "engineering",
    scope_reviews: list[dict[str, str]] | None = None,
) -> None:
    config_dir = arena_fixture["config"].parent
    planner = "codex" if mode == "hackathon" else "claude"
    writer = "claude" if mode == "hackathon" else "codex"
    planner_script: dict[str, object] = {"review": [{"response": plan} for plan in plans]}
    if mode == "hackathon":
        reviews = scope_reviews or [
            {"verdict": "approve", "rationale": "The candidate stays within the named beat."}
            for plan in plans
            if plan["action"] == "task"
        ]
        planner_script["judge"] = [{"response": review} for review in reviews]
    (config_dir / f"{planner}.json").write_text(json.dumps(planner_script), encoding="utf-8")
    (config_dir / f"{writer}.json").write_text(
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
        mode="hackathon",
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
    assert (run_dir / "engineers/claude/step-1/candidate.patch").is_file()
    assert not (run_dir / "engineers/codex/step-1/candidate.patch").exists()
    planner_prompt = (run_dir / "providers/planner/step-1/prompt.md").read_text()
    writer_prompt = (run_dir / "providers/writer/step-1/prompt.md").read_text()
    assert "frozen MVP feature contract" in planner_prompt
    assert "explicit user authorization is required" in writer_prompt
    scope_review = json.loads((run_dir / "steps/step-1/scope-review.json").read_text())
    assert scope_review["task_id"] == "make-beat-real"
    assert scope_review["commit"] == status["accepted_tasks"][0]["commit"]
    assert scope_review["demo_beat"] == "Beat 1"
    assert scope_review["verdict"] == "approve"
    assert scope_review["rationale"].strip()
    verify_manifest(run_dir)


def test_hackathon_scope_reviewer_can_reject_claude_candidate(
    arena_fixture: dict[str, Path],
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [
            _plan("scope-creep", "Make Beat 1 work.", "calculator.py", beat="Beat 1"),
            _blocked("The proposed implementation exceeded the frozen MVP contract."),
        ],
        [
            {
                "text": "Implemented the candidate for independent scope review.",
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
        mode="hackathon",
        scope_reviews=[
            {
                "verdict": "reject",
                "rationale": "The diff adds a capability outside the frozen demo beat.",
            }
        ],
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    run_dir = AdaptiveController(config, "hackathon", max_steps=2).run(
        arena_fixture["source"], "Deliver only the contracted MVP."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "blocked"
    assert status["last_green_sha"] == baseline
    assert any("out of MVP scope" in reason for reason in status["rejected_tasks"][0]["reasons"])
    assert (
        json.loads((run_dir / "steps/step-1/scope-review.json").read_text())["verdict"] == "reject"
    )


def test_hackathon_plan_cannot_edit_the_feature_contract(
    arena_fixture: dict[str, Path],
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [_plan("expand-scope", "Add another demo feature.", "DEMO.md", beat="Beat 1")],
        [],
        mode="hackathon",
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    run_dir = AdaptiveController(config, "hackathon", max_steps=1).run(
        arena_fixture["source"], "Deliver only the contracted MVP."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "failed"
    assert status["last_green_sha"] == baseline
    assert "cannot edit the frozen" in status["blocker"]
    assert status["writer"] == "claude"
    assert status["planner"] == "codex"


@pytest.mark.parametrize(
    ("path", "beat"),
    [("?*", "Beat 1"), ("calculator.py", "e")],
)
def test_hackathon_plan_rejects_repo_wide_scope_and_partial_beat(path: str, beat: str) -> None:
    plan = _plan("bad-plan", "Broaden the MVP.", path, beat=beat)
    with pytest.raises(ProviderError):
        _validate_plan(plan, "hackathon", set(), "## Beat 1\nShow the result.\n")


def test_deadline_during_rejection_still_exports_last_green(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [_plan("bad", "Make a candidate that fails acceptance.", "bad.txt")],
        [
            {
                "text": "Returned a bounded candidate for coordinator verification.",
                "operations": [{"op": "write", "path": "bad.txt", "content": "bad\n"}],
            }
        ],
    )
    config = load_config(arena_fixture["config"])
    baseline = git(arena_fixture["source"], "rev-parse", "HEAD")

    def deadline_reset(*args: object, **kwargs: object) -> None:
        raise DeadlineExceeded("the total run deadline was exhausted during cleanup")

    monkeypatch.setattr(RepositoryManager, "reset_engineer_worktree", deadline_reset)
    run_dir = AdaptiveController(config, "engineering", max_steps=1).run(
        arena_fixture["source"], "Make addition correct."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "deadline"
    assert status["last_green_sha"] == baseline
    assert (run_dir / "last-green.bundle").is_file()
    assert (run_dir / "last-green.json").is_file()
    verify_manifest(run_dir)


def test_stop_after_writer_skips_evaluation_and_exports_last_green(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [_plan("candidate", "Attempt the bounded fix.", "calculator.py")],
        [
            {
                "text": "Completed the requested bounded candidate.",
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
    invoke = AdaptiveController._invoke

    def invoke_then_stop(self: AdaptiveController, *args: object, **kwargs: object):
        result = invoke(self, *args, **kwargs)
        if args[4] == "writer":
            assert self.audit is not None
            self.audit.write_text("stop.request", "test\n")
        return result

    monkeypatch.setattr(AdaptiveController, "_invoke", invoke_then_stop)
    run_dir = AdaptiveController(config, "engineering", max_steps=1).run(
        arena_fixture["source"], "Make addition correct."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "stopped"
    assert status["last_green_sha"] == baseline
    assert not (run_dir / "evaluations").exists()
    assert (run_dir / "last-green.bundle").is_file()
    verify_manifest(run_dir)


def test_evaluation_keeps_the_run_deadline_after_provider_phases(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [_plan("fix", "Correct addition.", "calculator.py"), _done()],
        [
            {
                "text": "Corrected the bounded addition implementation.",
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
    evaluate = Evaluator.evaluate

    def evaluate_with_deadline_check(self: Evaluator, *args: object, **kwargs: object):
        assert self.deadline - time.monotonic() > 30
        return evaluate(self, *args, **kwargs)

    monkeypatch.setattr(Evaluator, "evaluate", evaluate_with_deadline_check)
    run_dir = AdaptiveController(config, "engineering", max_steps=2).run(
        arena_fixture["source"], "Make addition correct."
    )

    assert adaptive_status(run_dir)["state"] == "complete"


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


def test_plain_results_ignores_newer_adaptive_runs(
    arena_fixture: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root = load_config(arena_fixture["config"]).run.artifact_root
    ordinary = root / "a-ordinary"
    ordinary.mkdir(parents=True)
    (ordinary / "run.json").write_text('{"run_id":"ordinary"}\n', encoding="utf-8")
    (ordinary / "report.md").write_text("ordinary report\n", encoding="utf-8")
    adaptive = root / "z-adaptive"
    adaptive.mkdir()
    (adaptive / "run.json").write_text(
        '{"run_id":"adaptive","controller":"adaptive"}\n', encoding="utf-8"
    )

    assert main(["results", "--config", str(arena_fixture["config"])]) == 0
    assert capsys.readouterr().out == "ordinary report\n\n"


def test_planner_done_cannot_mark_a_failing_baseline_complete(
    arena_fixture: dict[str, Path],
) -> None:
    _write_adaptive_scripts(arena_fixture, [_done()], [])
    config = load_config(arena_fixture["config"])

    run_dir = AdaptiveController(config, "engineering", max_steps=1).run(
        arena_fixture["source"], "Finish only when the configured checks pass."
    )

    status = adaptive_status(run_dir)
    assert status["state"] == "blocked"
    assert "required checks failed: tests" in status["blocker"]
    with pytest.raises(IntegrityError, match="failed required check"):
        integrate_last_green(
            run_dir,
            arena_fixture["source"],
            "arena/reject-failing-baseline",
            arena_fixture["root"] / "reject-failing-baseline",
        )


def test_adaptive_integration_rechecks_preserved_evaluation(arena_fixture: dict[str, Path]) -> None:
    _write_adaptive_scripts(
        arena_fixture,
        [
            _plan("fix-add", "Correct integer addition.", "calculator.py"),
            _done(),
        ],
        [
            {
                "text": "Corrected the bounded addition implementation.",
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
    run_dir = AdaptiveController(config, "engineering", max_steps=2).run(
        arena_fixture["source"], "Make addition correct."
    )
    evaluation_path = run_dir / "evaluations/codex/step-1/evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["checks"].clear()
    evaluation_path.write_text(json.dumps(evaluation) + "\n", encoding="utf-8")
    rebuild_manifest(run_dir, adaptive_status(run_dir)["run_id"])

    with pytest.raises(IntegrityError, match="omits a passing required check"):
        integrate_last_green(
            run_dir,
            arena_fixture["source"],
            "arena/tampered-evaluation",
            arena_fixture["root"] / "tampered-evaluation",
        )


def test_background_start_returns_while_controller_finishes(
    arena_fixture: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hijack_sentinel = arena_fixture["root"] / "module-hijack.txt"
    fake_package = arena_fixture["source"] / "agent_arena"
    fake_package.mkdir()
    (fake_package / "__init__.py").write_text("", encoding="utf-8")
    (fake_package / "__main__.py").write_text(
        f"from pathlib import Path\nPath({str(hijack_sentinel)!r}).write_text('hijacked')\n",
        encoding="utf-8",
    )
    (arena_fixture["source"] / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n", encoding="utf-8"
    )
    git(arena_fixture["source"], "add", "calculator.py", "agent_arena")
    git(arena_fixture["source"], "commit", "-m", "make baseline green")
    _write_adaptive_scripts(arena_fixture, [_done()], [])
    config = load_config(arena_fixture["config"])
    monkeypatch.chdir(arena_fixture["source"])

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
    assert not hijack_sentinel.exists()
    verify_manifest(run_dir)
