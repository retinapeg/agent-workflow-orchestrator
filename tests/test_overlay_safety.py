from __future__ import annotations

import stat
from dataclasses import replace
from pathlib import Path

import pytest

from agent_arena.audit import AuditStore
from agent_arena.config import ArenaConfig, RunConfig, load_config
from agent_arena.errors import EvaluationError
from agent_arena.evaluator import _apply_overlay, freeze_trusted_overlay


def _with_overlay(config: ArenaConfig, source: Path, destination: str = ".") -> ArenaConfig:
    run: RunConfig = replace(
        config.run,
        trusted_overlay=source,
        trusted_overlay_destination=destination,
    )
    return replace(config, run=run)


def test_overlay_is_frozen_once_and_external_edits_do_not_change_it(
    arena_fixture: dict[str, Path],
) -> None:
    overlay_source = arena_fixture["root"] / "overlay"
    (overlay_source / "tests").mkdir(parents=True)
    source_file = overlay_source / "tests" / "acceptance.txt"
    source_file.write_text("original\n", encoding="utf-8")
    source_file.chmod(0o755)
    config = _with_overlay(load_config(arena_fixture["config"]), overlay_source)
    audit = AuditStore(arena_fixture["root"] / "audit", "overlay-test")

    frozen = freeze_trusted_overlay(config, audit)
    assert frozen is not None
    assert stat.S_IMODE((frozen.root / "tests/acceptance.txt").stat().st_mode) == 0o600
    source_file.write_text("mutated later\n", encoding="utf-8")

    first = arena_fixture["root"] / "candidate-one"
    second = arena_fixture["root"] / "candidate-two"
    first.mkdir()
    second.mkdir()
    _apply_overlay(frozen, first)
    _apply_overlay(frozen, second)
    assert (first / "tests/acceptance.txt").read_text() == "original\n"
    assert (second / "tests/acceptance.txt").read_text() == "original\n"
    assert stat.S_IMODE((first / "tests/acceptance.txt").stat().st_mode) == 0o755
    assert stat.S_IMODE((second / "tests/acceptance.txt").stat().st_mode) == 0o755


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_overlay_never_writes_through_candidate_symlinks(
    arena_fixture: dict[str, Path], link_kind: str
) -> None:
    overlay_source = arena_fixture["root"] / f"overlay-{link_kind}"
    (overlay_source / "tests").mkdir(parents=True)
    (overlay_source / "tests/acceptance.txt").write_text("trusted\n", encoding="utf-8")
    config = _with_overlay(load_config(arena_fixture["config"]), overlay_source)
    audit = AuditStore(arena_fixture["root"] / f"audit-{link_kind}", f"overlay-{link_kind}")
    frozen = freeze_trusted_overlay(config, audit)
    assert frozen is not None

    workspace = arena_fixture["root"] / f"workspace-{link_kind}"
    workspace.mkdir()
    outside = arena_fixture["root"] / f"outside-{link_kind}"
    outside.mkdir()
    if link_kind == "directory":
        (workspace / "tests").symlink_to(outside, target_is_directory=True)
        outside_target = outside / "acceptance.txt"
    else:
        (workspace / "tests").mkdir()
        outside_target = outside / "target.txt"
        outside_target.write_text("untouched\n", encoding="utf-8")
        (workspace / "tests/acceptance.txt").symlink_to(outside_target)

    with pytest.raises(EvaluationError, match="symlink|regular file"):
        _apply_overlay(frozen, workspace)
    assert not (outside / "acceptance.txt").exists()
    if link_kind == "file":
        assert outside_target.read_text() == "untouched\n"


def test_frozen_overlay_tampering_fails_closed(arena_fixture: dict[str, Path]) -> None:
    overlay_source = arena_fixture["root"] / "overlay-tamper"
    overlay_source.mkdir()
    (overlay_source / "acceptance.txt").write_text("trusted\n", encoding="utf-8")
    config = _with_overlay(load_config(arena_fixture["config"]), overlay_source)
    audit = AuditStore(arena_fixture["root"] / "audit-tamper", "overlay-tamper")
    frozen = freeze_trusted_overlay(config, audit)
    assert frozen is not None
    (frozen.root / "acceptance.txt").write_text("changed\n", encoding="utf-8")
    workspace = arena_fixture["root"] / "workspace-tamper"
    workspace.mkdir()
    with pytest.raises(EvaluationError, match="changed after snapshot"):
        _apply_overlay(frozen, workspace)


def test_overlay_cannot_target_git_metadata(arena_fixture: dict[str, Path]) -> None:
    overlay_source = arena_fixture["root"] / "overlay-git"
    overlay_source.mkdir()
    (overlay_source / "config").write_text("bad\n", encoding="utf-8")
    config = _with_overlay(load_config(arena_fixture["config"]), overlay_source, destination=".git")
    audit = AuditStore(arena_fixture["root"] / "audit-git", "overlay-git")
    with pytest.raises(EvaluationError, match="Git metadata"):
        freeze_trusted_overlay(config, audit)
