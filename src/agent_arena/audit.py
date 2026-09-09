from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import IntegrityError
from .models import RunState, jsonable


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AuditStore:
    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_dir = run_dir.resolve()
        self.run_id = run_id
        self.run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(self.run_dir, 0o700)
        self._sequence = 0
        self._event_lock = threading.Lock()
        self.write_json(
            "run.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "state": RunState.CREATED.value,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "error": None,
            },
        )
        self.event("run_created", state=RunState.CREATED.value)

    def path(self, relative: str | Path) -> Path:
        candidate = (self.run_dir / relative).resolve()
        if candidate != self.run_dir and self.run_dir not in candidate.parents:
            raise ValueError(f"audit path escapes run directory: {relative}")
        return candidate

    def mkdir(self, relative: str | Path) -> Path:
        path = self.path(relative)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def write_text(self, relative: str | Path, content: str) -> Path:
        return self.write_bytes(relative, content.encode("utf-8"))

    def write_bytes(self, relative: str | Path, content: bytes) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return target

    def write_json(self, relative: str | Path, value: Any) -> Path:
        payload = json.dumps(jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        return self.write_text(relative, payload)

    def event(self, event_type: str, **fields: Any) -> None:
        with self._event_lock:
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "timestamp": utc_now(),
                "event": event_type,
                **jsonable(fields),
            }
            path = self.path("events.jsonl")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)

    def transition(self, state: RunState, *, error: str | None = None) -> None:
        run_path = self.path("run.json")
        data = json.loads(run_path.read_text(encoding="utf-8"))
        data["state"] = state.value
        data["updated_at"] = utc_now()
        data["error"] = error
        self.write_json("run.json", data)
        self.event("state_changed", state=state.value, error=error)

    def build_manifest(self) -> dict[str, Any]:
        return rebuild_manifest(self.run_dir, self.run_id)


def rebuild_manifest(run_dir: Path, run_id: str) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    files: list[dict[str, Any]] = []
    excluded_roots = {"private"}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run_dir).as_posix()
        if relative == "manifest.json" or relative.split("/", 1)[0] in excluded_roots:
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": utc_now(),
        "excludes": ["manifest.json", "private/**"],
        "files": files,
    }
    target = run_dir / "manifest.json"
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest.json.", dir=run_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest


def verify_manifest(run_dir: Path) -> dict[str, Any]:
    root = run_dir.resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink():
        raise IntegrityError(f"manifest must not be a symlink: {manifest_path}")
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid or missing manifest: {manifest_path}") from exc
    if not isinstance(loaded, dict):
        raise IntegrityError(f"manifest must be a JSON object: {manifest_path}")
    manifest: dict[str, Any] = loaded
    failures: list[str] = []
    expected_paths: set[str] = set()
    file_entries = manifest.get("files")
    if not isinstance(file_entries, list):
        raise IntegrityError("manifest files must be an array")
    for item in file_entries:
        if not isinstance(item, dict):
            failures.append("manifest contains a non-object file entry")
            continue
        relative = item.get("path")
        if not isinstance(relative, str):
            failures.append("manifest contains an invalid path")
            continue
        expected_paths.add(relative)
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file() or path.is_symlink():
            failures.append(f"missing or unsafe artifact: {relative}")
            continue
        actual = sha256_file(path)
        if actual != item.get("sha256"):
            failures.append(f"hash mismatch: {relative}")
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json" or relative.split("/", 1)[0] == "private":
            continue
        if path.is_symlink():
            failures.append(f"unexpected symlink artifact: {relative}")
        elif path.is_file():
            actual_paths.add(relative)
    for relative in sorted(actual_paths - expected_paths):
        failures.append(f"unexpected artifact not listed in manifest: {relative}")
    for relative in sorted(expected_paths - actual_paths):
        failures.append(f"manifest artifact missing: {relative}")
    if failures:
        raise IntegrityError("; ".join(failures))
    return manifest
