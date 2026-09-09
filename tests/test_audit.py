from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_arena.audit import AuditStore, verify_manifest
from agent_arena.errors import IntegrityError


def test_manifest_itself_may_not_be_a_symlink(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    audit = AuditStore(run_dir, "audit-test")
    audit.write_text("evidence.txt", "evidence\n")
    audit.build_manifest()
    external = tmp_path / "external-manifest.json"
    external.write_text((run_dir / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8")
    (run_dir / "manifest.json").unlink()
    (run_dir / "manifest.json").symlink_to(external)

    with pytest.raises(IntegrityError, match="must not be a symlink"):
        verify_manifest(run_dir)


@pytest.mark.parametrize("files", ["not-an-array", ["not-an-object"]])
def test_manifest_file_entries_require_an_array_of_objects(tmp_path: Path, files: object) -> None:
    run_dir = tmp_path / "run"
    audit = AuditStore(run_dir, "audit-test")
    audit.write_text("evidence.txt", "evidence\n")
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "run_id": "audit-test", "files": files}),
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="manifest files|non-object file entry"):
        verify_manifest(run_dir)
