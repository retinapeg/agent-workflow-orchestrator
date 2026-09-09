from __future__ import annotations

from pathlib import Path

import pytest

from agent_arena.errors import ProviderError
from agent_arena.providers.api import apply_file_operations, extract_json_object


def apply(root: Path, operations: object) -> int:
    return apply_file_operations(
        root,
        operations,
        max_operations=5,
        max_file_bytes=100,
        max_total_bytes=200,
    )


def test_applies_validated_writes_and_deletes(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("old")
    count = apply(
        tmp_path,
        [
            {"op": "delete", "path": "old.txt"},
            {"op": "write", "path": "src/new.txt", "content": "new"},
        ],
    )
    assert count == 2
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "src/new.txt").read_text() == "new"


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", ".git/config", "a\\b"])
def test_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ProviderError, match="unsafe"):
        apply(tmp_path, [{"op": "write", "path": path, "content": "x"}])


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProviderError, match="symlink|escapes"):
        apply(tmp_path, [{"op": "write", "path": "link/pwned", "content": "x"}])
    assert not (outside / "pwned").exists()


def test_extracts_json_without_accepting_prose_only() -> None:
    assert extract_json_object('prefix {"winner":"a"} suffix') == {"winner": "a"}
    with pytest.raises(ProviderError):
        extract_json_object("no object")
