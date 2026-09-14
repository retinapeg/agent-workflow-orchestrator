from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_arena.audit import AuditStore
from agent_arena.config import load_config
from agent_arena.errors import RepositoryError
from agent_arena.gitops import RepositoryManager, _redact_remote, _run_git

from .conftest import git


def manager(
    fixture: dict[str, Path], *, max_bytes: int = 500000
) -> tuple[RepositoryManager, Path, str]:
    config = load_config(fixture["config"])
    config = replace(config, limits=replace(config.limits, max_diff_bytes=max_bytes))
    audit = AuditStore(fixture["root"] / "safety-run", "safety-run")
    repository = RepositoryManager(fixture["source"], "HEAD", config, audit)
    evidence = repository.inspect_source()
    repository.create_private_clone(evidence)
    workspace = repository.create_engineer_worktree("codex", evidence.base_commit)
    return repository, workspace, evidence.base_commit


def test_protected_rename_source_is_disqualified(arena_fixture: dict[str, Path]) -> None:
    repository, workspace, baseline = manager(arena_fixture)
    git(workspace, "mv", "tests/test_calculator.py", "renamed.py")
    git(workspace, "commit", "-m", "move protected tests")
    snapshot = repository.freeze_candidate("codex", "initial", workspace, baseline, baseline)
    assert not snapshot.valid
    assert "tests/test_calculator.py" in snapshot.changed_files
    assert "renamed.py" in snapshot.changed_files
    assert "protected path changed: tests/test_calculator.py" in snapshot.policy_violations


def test_workspace_record_uses_observed_git_identity(arena_fixture: dict[str, Path]) -> None:
    repository, workspace, baseline = manager(arena_fixture)
    records = repository.engineer_workspace_records()
    assert len(records) == 1
    record = records[0]
    assert record.engineer_id == "codex"
    assert record.worktree_path == str(workspace.resolve())
    assert record.branch == git(workspace, "symbolic-ref", "--short", "HEAD")
    assert record.base_commit == baseline == git(workspace, "rev-parse", "HEAD")
    assert record.baseline_tree == git(workspace, "rev-parse", "HEAD^{tree}")


def test_allowed_scope_applies_to_rename_source(arena_fixture: dict[str, Path]) -> None:
    repository, workspace, baseline = manager(arena_fixture)
    repository.config = replace(
        repository.config,
        run=replace(repository.config.run, protected_paths=(), allowed_paths=("renamed.py",)),
    )
    git(workspace, "mv", "calculator.py", "renamed.py")
    snapshot = repository.freeze_candidate("codex", "initial", workspace, baseline, baseline)
    assert "path outside allowed scope: calculator.py" in snapshot.policy_violations


def test_untracked_runtime_artifacts_are_excluded_from_candidate_freeze(
    arena_fixture: dict[str, Path],
) -> None:
    repository, workspace, baseline = manager(arena_fixture)
    (workspace / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n", encoding="utf-8"
    )
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__/calculator.cpython-311.pyc").write_bytes(b"runtime cache")
    (workspace / "tests/__pycache__").mkdir()
    (workspace / "tests/__pycache__/test_calculator.cpython-311.pyc").write_bytes(b"test cache")

    snapshot = repository.freeze_candidate("codex", "initial", workspace, baseline, baseline)

    assert snapshot.valid
    assert snapshot.changed_files == ("calculator.py",)


def test_untracked_artifact_excludes_do_not_hide_tracked_file_edits(
    arena_fixture: dict[str, Path],
) -> None:
    tracked = arena_fixture["source"] / "tracked.pyc"
    tracked.write_bytes(b"baseline")
    git(arena_fixture["source"], "add", "tracked.pyc")
    git(arena_fixture["source"], "commit", "-m", "track an otherwise ignored artifact")
    repository, workspace, baseline = manager(arena_fixture)
    (workspace / "tracked.pyc").write_bytes(b"changed")

    snapshot = repository.freeze_candidate("codex", "initial", workspace, baseline, baseline)

    assert snapshot.changed_files == ("tracked.pyc",)


@pytest.mark.parametrize("deletion", [False, True])
def test_committed_or_deleted_oversized_blob_is_rejected_before_patch(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch, deletion: bool
) -> None:
    if deletion:
        (arena_fixture["source"] / "big.bin").write_bytes(b"x" * 5000)
        git(arena_fixture["source"], "add", "big.bin")
        git(arena_fixture["source"], "commit", "-m", "large baseline")
    repository, workspace, baseline = manager(arena_fixture, max_bytes=1024)
    if deletion:
        (workspace / "big.bin").unlink()
    else:
        (workspace / "big.bin").write_bytes(b"x" * 5000)
    git(workspace, "add", "-A")
    git(workspace, "commit", "-m", "agent committed oversized change")
    from agent_arena import gitops

    original = gitops._run_git

    def guarded(repo: Path, *args: str, **kwargs: object) -> object:
        assert "--binary" not in args, "oversized candidate reached binary diff generation"
        return original(repo, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(gitops, "_run_git", guarded)
    with pytest.raises(RepositoryError, match="safety limit|oversized blob"):
        repository.freeze_candidate("codex", "initial", workspace, baseline, baseline)


def test_git_output_limit_is_enforced(arena_fixture: dict[str, Path]) -> None:
    with pytest.raises(RepositoryError, match="output exceeds"):
        _run_git(arena_fixture["source"], "log", "--format=" + "x" * 5000, max_output_bytes=128)


def test_candidate_freeze_disables_inherited_git_hooks(
    arena_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, workspace, baseline = manager(arena_fixture)
    sentinel = arena_fixture["root"] / "hook-secret.txt"
    hooks = arena_fixture["root"] / "malicious-hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\nprintf '%s' \"$ARENA_TEST_SECRET\" > {sentinel}\n")
    hook.chmod(0o700)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
    monkeypatch.setenv("ARENA_TEST_SECRET", "must-not-reach-hook")
    (workspace / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n", encoding="utf-8"
    )

    snapshot = repository.freeze_candidate("codex", "initial", workspace, baseline, baseline)

    assert snapshot.valid
    assert not sentinel.exists()


def test_remote_credentials_and_query_are_redacted() -> None:
    remote = _redact_remote("https://user:USERSECRET@example.com/repo?token=QUERYSECRET#FRAGMENT")
    assert "USERSECRET" not in remote
    assert "QUERYSECRET" not in remote
    assert "FRAGMENT" not in remote
    assert "example.com/repo" in remote
