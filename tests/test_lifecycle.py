from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from agent_arena.audit import AuditStore, rebuild_manifest, sha256_file, verify_manifest
from agent_arena.cli import _status_payload
from agent_arena.config import load_config
from agent_arena.errors import ArenaError, IntegrityError, RepositoryError
from agent_arena.integration import integrate_winner
from agent_arena.models import Phase
from agent_arena.orchestrator import ArenaOrchestrator

from .conftest import git


def test_full_hackathon_loop_and_safe_integration(arena_fixture: dict[str, Path]) -> None:
    config = load_config(arena_fixture["config"])
    source = arena_fixture["source"]
    original_head = git(source, "rev-parse", "HEAD")
    run_dir = ArenaOrchestrator(config, "hackathon").run(source, arena_fixture["task"])

    run = json.loads((run_dir / "run.json").read_text())
    decision = json.loads((run_dir / "decision.json").read_text())
    common = json.loads((run_dir / "common-inputs.json").read_text())
    workspaces = json.loads((run_dir / "workspaces.json").read_text())
    assert run["state"] == "complete"
    assert decision["winner"] == "codex"
    assert common["mode"] == "hackathon"
    assert common["revision_rounds"] == 1
    assert len(set(common["engineer_initial_trees"].values())) == 1
    assert workspaces["schema_version"] == 1
    assert workspaces["base_commit"] == common["base_commit"]
    assert workspaces["base_tree"] == common["base_tree"]
    assert Path(workspaces["private_repository_path"]).is_absolute()
    records = {record["engineer_id"]: record for record in workspaces["engineers"]}
    assert set(records) == {"codex", "claude"}
    for engineer_id, record in records.items():
        assert record["branch"].endswith(f"/{engineer_id}")
        assert Path(record["worktree_path"]).is_absolute()
        assert record["base_commit"] == common["base_commit"]
        assert record["baseline_tree"] == common["base_tree"]
    assert (run_dir / "engineers/codex/reviews/round-1/claude/critique.json").is_file()
    assert (run_dir / "engineers/claude/reviews/round-1/codex/critique.json").is_file()
    assert (run_dir / "winner.patch").is_file()
    assert (run_dir / "salvage.md").is_file()
    manifest = verify_manifest(run_dir)
    assert "workspaces.json" in {item["path"] for item in manifest["files"]}
    for artifact in run_dir.rglob("*"):
        relative = artifact.relative_to(run_dir).as_posix()
        if artifact.is_symlink() or not artifact.is_file() or relative.startswith("private/"):
            continue
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600, relative

    assert git(source, "rev-parse", "HEAD") == original_head
    assert git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert not (run_dir / "private/repo/.git/objects/info/alternates").exists()
    assert git(run_dir / "private/repo", "remote") == ""

    integration_path = arena_fixture["root"] / "integrated"
    receipt = integrate_winner(
        run_dir,
        source,
        "arena/test-winner",
        integration_path,
    )
    assert receipt["merged_into_original_branch"] is False
    assert receipt["pushed"] is False
    assert "return left + right" in (integration_path / "calculator.py").read_text()
    assert git(source, "rev-parse", "HEAD") == original_head
    verify_manifest(run_dir)


def test_hackathon_refuses_to_start_without_demo_contract(
    arena_fixture: dict[str, Path],
) -> None:
    source = arena_fixture["source"]
    (source / "DEMO.md").unlink()
    git(source, "add", "DEMO.md")
    git(source, "commit", "-m", "remove demo contract")
    config = load_config(arena_fixture["config"])

    with pytest.raises(ArenaError, match="DEMO.md"):
        ArenaOrchestrator(config, "hackathon").run(source, arena_fixture["task"])


def test_engineering_mode_records_bounded_live_status(arena_fixture: dict[str, Path]) -> None:
    config = load_config(arena_fixture["config"])
    run_dir = ArenaOrchestrator(config, "engineering").run(
        arena_fixture["source"], arena_fixture["task"]
    )

    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    common = json.loads((run_dir / "common-inputs.json").read_text(encoding="utf-8"))
    assert run["state"] == "complete"
    assert run["mode"] == "engineering"
    assert run["source_repo"] == str(arena_fixture["source"].resolve())
    assert run["provider_timeout_seconds"] == 5
    assert run["max_run_seconds"] == 60
    assert run["active_providers"] == []
    assert run["deadline_at"] > run["started_at"]
    assert common["mode"] == "engineering"
    assert common["revision_rounds"] == 2
    status = _status_payload(run_dir)
    assert status["elapsed_seconds"] >= 0
    assert status["remaining_seconds"] >= 0
    assert status["seconds_since_progress"] >= 0
    assert status["last_event"]["event"] == "state_changed"
    verify_manifest(run_dir)


def test_active_provider_and_deadline_are_visible_before_completion(
    arena_fixture: dict[str, Path], tmp_path: Path
) -> None:
    config = load_config(arena_fixture["config"])
    orchestrator = ArenaOrchestrator(config, "engineering")
    audit = AuditStore(tmp_path / "live-run", "live-run")
    orchestrator.audit = audit
    engineer = config.engineer("codex")
    request = orchestrator._request(
        engineer,
        Phase.IMPLEMENT,
        arena_fixture["source"],
        "perform the bounded test",
    )

    orchestrator._provider_status_start(engineer, request, "engineers/codex/initial")
    status = _status_payload(audit.run_dir)
    assert request.timeout_seconds == 5
    assert status["active_providers"][0]["engineer_id"] == "codex"
    assert status["active_providers"][0]["phase"] == "implement"
    assert status["active_providers"][0]["deadline_at"]

    orchestrator._provider_status_finish("codex")
    assert _status_payload(audit.run_dir)["active_providers"] == []


def test_short_provider_success_fails_closed(arena_fixture: dict[str, Path]) -> None:
    config = load_config(arena_fixture["config"])
    strict_engineering = replace(config.mode("engineering"), min_response_bytes=5_000)
    config = replace(config, modes={**config.modes, "engineering": strict_engineering})

    run_dir = ArenaOrchestrator(config, "engineering").run(
        arena_fixture["source"], arena_fixture["task"]
    )

    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["state"] == "no_winner"
    for engineer in ("codex", "claude"):
        result = json.loads(
            (run_dir / f"engineers/{engineer}/initial/result.json").read_text(encoding="utf-8")
        )
        assert result["status"] == "failed"
        assert "substantive response bytes" in result["error"]
    verify_manifest(run_dir)


def test_integration_accepts_unchanged_source_head_when_base_ref_is_older(
    arena_fixture: dict[str, Path],
) -> None:
    source = arena_fixture["source"]
    baseline = git(source, "rev-parse", "HEAD")
    (source / "post-baseline.txt").write_text("source advanced before the run\n", encoding="utf-8")
    git(source, "add", "post-baseline.txt")
    git(source, "commit", "-m", "advance source before selected base")
    recorded_head = git(source, "rev-parse", "HEAD")

    config = load_config(arena_fixture["config"])
    run_dir = ArenaOrchestrator(config, "hackathon").run(
        source, arena_fixture["task"], base_ref=baseline
    )
    repository = json.loads((run_dir / "repository.json").read_text(encoding="utf-8"))
    assert repository["base_commit"] == baseline
    assert repository["head"] == recorded_head

    integration_path = arena_fixture["root"] / "integrated-older-base"
    receipt = integrate_winner(
        run_dir,
        source,
        "arena/older-base",
        integration_path,
    )
    assert receipt["baseline"] == baseline
    assert git(source, "rev-parse", "HEAD") == recorded_head
    assert git(integration_path, "rev-parse", "HEAD^{tree}") == receipt["winner_tree"]


def test_integration_rejects_source_head_changed_after_run(
    arena_fixture: dict[str, Path],
) -> None:
    source = arena_fixture["source"]
    config = load_config(arena_fixture["config"])
    run_dir = ArenaOrchestrator(config, "hackathon").run(source, arena_fixture["task"])
    (source / "advanced-after-run.txt").write_text("new source state\n", encoding="utf-8")
    git(source, "add", "advanced-after-run.txt")
    git(source, "commit", "-m", "advance after arena run")

    destination = arena_fixture["root"] / "integrated-after-source-move"
    with pytest.raises(RepositoryError, match="source HEAD moved from recorded run HEAD"):
        integrate_winner(run_dir, source, "arena/source-moved", destination)
    assert not destination.exists()


def test_engineering_mode_runs_two_review_revision_cycles(arena_fixture: dict[str, Path]) -> None:
    config = load_config(arena_fixture["config"])
    run_dir = ArenaOrchestrator(config, "engineering").run(
        arena_fixture["source"], arena_fixture["task"]
    )
    common = json.loads((run_dir / "common-inputs.json").read_text())
    decision = json.loads((run_dir / "decision.json").read_text())
    assert common["mode"] == "engineering"
    assert common["revision_rounds"] == 2
    assert decision["winner"] == "codex"
    for engineer, target in (("codex", "claude"), ("claude", "codex")):
        assert (run_dir / f"engineers/{engineer}/reviews/round-2/{target}/critique.json").is_file()
        assert (run_dir / f"engineers/{engineer}/revisions/round-2/result.json").is_file()
    verify_manifest(run_dir)


def test_each_engineering_round_uses_and_evaluates_the_previous_generation(
    arena_fixture: dict[str, Path],
) -> None:
    config = load_config(arena_fixture["config"])
    codex_script_path = config.path.parent / "codex.json"
    script = json.loads(codex_script_path.read_text(encoding="utf-8"))
    script["implement"]["operations"][0]["content"] = (
        "GENERATION = 0\n\ndef add(left: int, right: int) -> int:\n    return 0\n"
    )
    script["revise"] = [
        {
            "text": "revision one fixes behavior",
            "operations": [
                {
                    "op": "write",
                    "path": "calculator.py",
                    "content": (
                        "GENERATION = 1\n\n"
                        "def add(left: int, right: int) -> int:\n"
                        "    return left + right\n"
                    ),
                }
            ],
        },
        {
            "text": "revision two preserves the fix and marks the generation",
            "operations": [
                {
                    "op": "write",
                    "path": "calculator.py",
                    "content": (
                        "GENERATION = 2\n\n"
                        "def add(left: int, right: int) -> int:\n"
                        "    return left + right\n"
                    ),
                }
            ],
        },
    ]
    codex_script_path.write_text(json.dumps(script), encoding="utf-8")

    run_dir = ArenaOrchestrator(config, "engineering").run(
        arena_fixture["source"], arena_fixture["task"]
    )
    snapshots = {
        generation: json.loads(
            (run_dir / f"engineers/codex/{generation}/candidate.json").read_text()
        )
        for generation in ("initial", "revision-1", "revision-2")
    }
    assert snapshots["revision-1"]["parent_commit"] == snapshots["initial"]["commit"]
    assert snapshots["revision-2"]["parent_commit"] == snapshots["revision-1"]["commit"]
    for generation in snapshots:
        evaluation = json.loads(
            (run_dir / f"evaluations/codex/{generation}/evaluation.json").read_text()
        )
        assert evaluation["commit"] == snapshots[generation]["commit"]
    assert "GENERATION = 0" in (run_dir / "engineers/codex/initial/candidate.patch").read_text()
    assert "GENERATION = 1" in (run_dir / "engineers/codex/revision-1/candidate.patch").read_text()
    assert "GENERATION = 2" in (run_dir / "engineers/codex/revision-2/candidate.patch").read_text()
    second_review_prompt = (
        run_dir / "engineers/claude/reviews/round-2/codex/prompt.md"
    ).read_text()
    assert "GENERATION = 1" in second_review_prompt
    second_revision_prompt = (run_dir / "engineers/codex/revisions/round-2/prompt.md").read_text()
    assert snapshots["revision-1"]["commit"] in second_revision_prompt
    assert json.loads((run_dir / "decision.json").read_text())["winner"] == "codex"
    winner_patch = (run_dir / "winner.patch").read_text()
    assert "GENERATION = 2" in winner_patch
    verify_manifest(run_dir)


def test_no_change_winner_can_be_integrated_as_an_explicit_empty_commit(
    arena_fixture: dict[str, Path],
) -> None:
    source = arena_fixture["source"]
    correct_source = "def add(left: int, right: int) -> int:\n    return left + right\n"
    (source / "calculator.py").write_text(correct_source, encoding="utf-8")
    git(source, "add", "calculator.py")
    git(source, "commit", "-m", "task already satisfied")
    baseline = git(source, "rev-parse", "HEAD")
    config = load_config(arena_fixture["config"])
    for engineer, target in (("codex", "claude"), ("claude", "codex")):
        script = config.path.parent / f"{engineer}.json"
        review = json.dumps(
            {
                "target": target,
                "summary": "No defect found.",
                "findings": [],
                "attack_tests": [],
            }
        )
        script.write_text(
            json.dumps(
                {
                    "implement": {"text": "already correct", "operations": []},
                    "review": {"text": review},
                    "revise": [
                        {"text": "no change one", "operations": []},
                        {"text": "no change two", "operations": []},
                    ],
                }
            ),
            encoding="utf-8",
        )

    run_dir = ArenaOrchestrator(config, "engineering").run(source, arena_fixture["task"])
    winner = json.loads((run_dir / "winner.json").read_text(encoding="utf-8"))
    assert winner["tree"] == git(source, "rev-parse", "HEAD^{tree}")
    assert (run_dir / "winner.patch").read_bytes() == b""

    integration_path = arena_fixture["root"] / "integrated-no-change"
    receipt = integrate_winner(run_dir, source, "arena/no-change", integration_path)
    assert receipt["no_changes"] is True
    assert receipt["integration_commit"] != baseline
    assert git(integration_path, "rev-parse", "HEAD^{tree}") == winner["tree"]
    assert git(source, "rev-parse", "HEAD") == baseline
    second = integrate_winner(
        run_dir,
        source,
        "arena/no-change-two",
        arena_fixture["root"] / "integrated-no-change-two",
    )
    assert second["receipt"] != receipt["receipt"]
    assert len(list((run_dir / "integration-receipts").glob("*.json"))) == 2
    verify_manifest(run_dir)


def test_integration_rejects_semantically_inconsistent_winner_records(
    arena_fixture: dict[str, Path],
) -> None:
    config = load_config(arena_fixture["config"])
    run_dir = ArenaOrchestrator(config, "hackathon").run(
        arena_fixture["source"], arena_fixture["task"]
    )
    decision_path = run_dir / "decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["winner"] = "claude"
    decision_path.write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rebuild_manifest(run_dir, run_data["run_id"])

    with pytest.raises(IntegrityError, match="conflicts with the recorded final decision"):
        integrate_winner(
            run_dir,
            arena_fixture["source"],
            "arena/inconsistent",
            arena_fixture["root"] / "integrated-inconsistent",
        )


def test_optional_judge_prompt_removes_coordinator_identity_and_workspace_paths(
    arena_fixture: dict[str, Path],
) -> None:
    config = load_config(arena_fixture["config"])
    claude_script_path = config.path.parent / "claude.json"
    script = json.loads(claude_script_path.read_text(encoding="utf-8"))
    correct = "def add(left: int, right: int) -> int:\n    return left + right\n"
    script["implement"]["operations"][0]["content"] = correct
    for revision in script["revise"]:
        revision["operations"][0]["content"] = correct
    claude_script_path.write_text(json.dumps(script), encoding="utf-8")
    config = replace(
        config,
        judge=replace(
            config.judge,
            enabled=True,
            engineer="codex",
            score_band_basis_points=0,
        ),
    )

    run_dir = ArenaOrchestrator(config, "hackathon").run(
        arena_fixture["source"], arena_fixture["task"]
    )
    prompt = (run_dir / "judge/prompt.md").read_text(encoding="utf-8")
    assert '"engineer_id": "codex"' not in prompt
    assert '"engineer_id": "claude"' not in prompt
    assert "worktrees/engineers/codex" not in prompt
    assert "worktrees/engineers/claude" not in prompt
    assert "candidate-a" in prompt and "candidate-b" in prompt
    selection = json.loads((run_dir / "judge/selection.json").read_text())
    assert selection["blinded_mapping"] == {
        "claude": "candidate-a",
        "codex": "candidate-b",
    }
    verify_manifest(run_dir)


def test_integration_rejects_an_earlier_generation_under_the_winner_identity(
    arena_fixture: dict[str, Path],
) -> None:
    config = load_config(arena_fixture["config"])
    run_dir = ArenaOrchestrator(config, "hackathon").run(
        arena_fixture["source"], arena_fixture["task"]
    )
    winner_path = run_dir / "winner.json"
    winner = json.loads(winner_path.read_text(encoding="utf-8"))
    initial = json.loads(
        (run_dir / f"engineers/{winner['engineer_id']}/initial/candidate.json").read_text()
    )
    initial_patch = run_dir / initial["patch_path"]
    (run_dir / "winner.patch").write_bytes(initial_patch.read_bytes())
    winner.update(
        {
            "commit": initial["commit"],
            "tree": initial["tree"],
            "patch_sha256": sha256_file(initial_patch),
        }
    )
    winner_path.write_text(json.dumps(winner, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rebuild_manifest(run_dir, run_data["run_id"])

    with pytest.raises(IntegrityError, match="final frozen candidate"):
        integrate_winner(
            run_dir,
            arena_fixture["source"],
            "arena/earlier-generation",
            arena_fixture["root"] / "integrated-earlier-generation",
        )
