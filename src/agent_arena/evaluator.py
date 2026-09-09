from __future__ import annotations

import json
import os
import shutil
import statistics
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from stat import S_IMODE
from typing import Any

from .audit import AuditStore, sha256_file
from .config import ArenaConfig, BenchmarkConfig, CheckConfig
from .errors import DeadlineExceeded, EvaluationError
from .gitops import RepositoryManager
from .models import (
    BenchmarkResult,
    CandidateEvaluation,
    CandidateSnapshot,
    CheckResult,
    ProcessResult,
    jsonable,
)
from .process import ProcessRunner, sanitized_environment


@dataclass(frozen=True)
class FrozenOverlay:
    """One coordinator-owned, immutable acceptance overlay for an entire run."""

    root: Path
    destination: str
    files: tuple[dict[str, Any], ...]

    def audit_dict(self, source: Path) -> dict[str, Any]:
        return {
            "source": str(source),
            "frozen_at": "inputs/trusted-overlay",
            "destination": self.destination,
            "files": list(self.files),
        }


def _overlay_destination(value: str) -> PurePosixPath:
    if not value or "\\" in value or "\x00" in value:
        raise EvaluationError("trusted overlay destination must be a relative POSIX path")
    destination = PurePosixPath(value)
    if destination.is_absolute() or ".." in destination.parts or ".git" in destination.parts:
        raise EvaluationError("trusted overlay destination escapes or targets Git metadata")
    return destination


def _overlay_files(root: Path) -> tuple[dict[str, Any], ...]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            raise EvaluationError(f"trusted overlay may not target .git: {relative}")
        if path.is_symlink():
            raise EvaluationError(f"trusted overlay may not contain symlinks: {path}")
        if path.is_file():
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": path.stat().st_size,
                    "mode": oct(S_IMODE(path.stat().st_mode)),
                    "sha256": sha256_file(path),
                }
            )
        elif not path.is_dir():
            raise EvaluationError(f"trusted overlay contains a special file: {path}")
    return tuple(files)


def trusted_overlay_manifest(config: ArenaConfig) -> dict[str, Any] | None:
    """Inspect the live overlay for doctor output only.

    Competition evaluations use :func:`freeze_trusted_overlay` instead, so a
    later edit to the configured source cannot change one candidate's tests.
    """

    root = config.run.trusted_overlay
    if root is None:
        return None
    destination = _overlay_destination(config.run.trusted_overlay_destination)
    return {
        "source": str(root),
        "destination": destination.as_posix(),
        "files": list(_overlay_files(root)),
    }


def freeze_trusted_overlay(config: ArenaConfig, audit: AuditStore) -> FrozenOverlay | None:
    """Copy acceptance inputs once, preserving symlinks only long enough to reject them."""

    source = config.run.trusted_overlay
    if source is None:
        return None
    destination = _overlay_destination(config.run.trusted_overlay_destination)
    frozen_root = audit.path("inputs/trusted-overlay")
    if frozen_root.exists():
        raise EvaluationError("trusted overlay snapshot already exists")
    frozen_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        # Preserving source symlinks prevents copytree from ever following one
        # outside the coordinator-owned source during this snapshot operation.
        shutil.copytree(source, frozen_root, symlinks=True)
        files = _overlay_files(frozen_root)
        os.chmod(frozen_root, 0o700)
        for path in frozen_root.rglob("*"):
            if path.is_symlink():
                continue
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except Exception:
        if frozen_root.exists():
            shutil.rmtree(frozen_root)
        raise
    return FrozenOverlay(frozen_root, destination.as_posix(), files)


def _ensure_directory(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.is_symlink():
            raise EvaluationError(f"trusted overlay destination traverses a symlink: {current}")
        if current.exists() and not current.is_dir():
            raise EvaluationError(f"trusted overlay destination is not a directory: {current}")
        current.mkdir(mode=0o700, exist_ok=True)
    return current


def _verify_frozen_overlay(overlay: FrozenOverlay) -> None:
    actual = _overlay_files(overlay.root)
    expected_identity = [
        {key: item[key] for key in ("path", "bytes", "sha256")} for item in overlay.files
    ]
    actual_identity = [{key: item[key] for key in ("path", "bytes", "sha256")} for item in actual]
    if actual_identity != expected_identity or any(item["mode"] != oct(0o600) for item in actual):
        raise EvaluationError("the frozen trusted overlay changed after snapshot")


def _apply_overlay(overlay: FrozenOverlay | None, workspace: Path) -> None:
    if overlay is None:
        return
    _verify_frozen_overlay(overlay)
    workspace = workspace.resolve()
    destination_relative = _overlay_destination(overlay.destination)
    destination = _ensure_directory(workspace, destination_relative)
    if destination.resolve() != destination:
        raise EvaluationError("trusted overlay destination resolves through a symlink")
    for item in overlay.files:
        relative = PurePosixPath(str(item["path"]))
        target = destination / Path(*relative.parts)
        _ensure_directory(destination, relative.parent)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise EvaluationError(f"trusted overlay target is not a regular file: {relative}")
        source = overlay.root / Path(*relative.parts)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                shutil.copyfileobj(reader, writer, length=1_048_576)
                writer.flush()
                os.fsync(writer.fileno())
            os.chmod(temporary, int(str(item["mode"]), 8))
            # Atomic replacement cannot write through an existing hard link.
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _metric_from_output(stdout: str, metric_key: str) -> Decimal:
    for line in reversed(stdout.splitlines()):
        if not line.startswith("ARENA_METRIC "):
            continue
        try:
            payload = json.loads(line[len("ARENA_METRIC ") :])
            raw = payload[metric_key]
            if isinstance(raw, bool):
                raise ValueError
            value = Decimal(str(raw))
        except (json.JSONDecodeError, KeyError, InvalidOperation, ValueError, TypeError) as exc:
            raise EvaluationError(f"invalid ARENA_METRIC payload for key {metric_key!r}") from exc
        if not value.is_finite():
            raise EvaluationError(f"benchmark metric {metric_key!r} is not finite")
        return value
    raise EvaluationError(f"benchmark did not emit ARENA_METRIC with key {metric_key!r}")


def _normalized(value: Decimal, benchmark: BenchmarkConfig) -> Decimal:
    if benchmark.direction == "higher":
        result = (value - benchmark.worst) / (benchmark.best - benchmark.worst)
    else:
        result = (benchmark.worst - value) / (benchmark.worst - benchmark.best)
    return max(Decimal(0), min(Decimal(1), result))


class Evaluator:
    def __init__(
        self,
        config: ArenaConfig,
        repository: RepositoryManager,
        audit: AuditStore,
        runner: ProcessRunner,
        mode: str,
        overlay: FrozenOverlay | None,
        deadline: float,
    ) -> None:
        self.config = config
        self.repository = repository
        self.audit = audit
        self.runner = runner
        self.mode = mode
        self.overlay = overlay
        self.deadline = deadline

    def _remaining_timeout(self, configured: int) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded("the total run deadline was exhausted")
        return min(float(configured), remaining)

    def evaluate(self, snapshot: CandidateSnapshot, *, provider_ok: bool) -> CandidateEvaluation:
        self._remaining_timeout(1)
        evaluation = CandidateEvaluation(
            engineer_id=snapshot.engineer_id,
            generation=snapshot.generation,
            commit=snapshot.commit,
            valid_candidate=snapshot.valid,
            provider_ok=provider_ok,
            policy_violations=list(snapshot.policy_violations),
        )
        if not snapshot.valid or not provider_ok:
            self.audit.write_json(
                f"evaluations/{snapshot.engineer_id}/{snapshot.generation}/evaluation.json",
                evaluation,
            )
            return evaluation
        if self.mode == "hackathon":
            evaluation.checks.append(self._run_demo_documentation_check(snapshot))
        for check in self.config.checks:
            evaluation.checks.append(self._run_check(snapshot, check))
        for benchmark in self.config.benchmarks:
            evaluation.benchmarks.append(self._run_benchmark(snapshot, benchmark))
        self.audit.write_json(
            f"evaluations/{snapshot.engineer_id}/{snapshot.generation}/evaluation.json",
            evaluation,
        )
        return evaluation

    def _run_demo_documentation_check(self, snapshot: CandidateSnapshot) -> CheckResult:
        self._remaining_timeout(1)
        check_id = "arena-demo-readiness"
        relative_root = f"evaluations/{snapshot.engineer_id}/{snapshot.generation}/{check_id}"
        label = f"check-{snapshot.engineer_id}-{snapshot.generation}-demo-docs"
        workspace = self.repository.create_detached_worktree(label, snapshot.commit)
        missing: list[str] = []
        try:
            checklist = workspace / "DEMO_CHECKLIST.md"
            readme = workspace / "README.md"
            if not checklist.is_file():
                missing.append("DEMO_CHECKLIST.md")
            else:
                lowered = checklist.read_text(encoding="utf-8", errors="replace").lower()
                required_concepts = {
                    "startup command": ("startup", "command"),
                    "primary demo flow": ("demo", "flow"),
                    "demo/sample data": ("data",),
                    "expected outputs": ("expected", "output"),
                    "required environment variables": ("environment",),
                    "fallback/demo mode": ("fallback",),
                    "known limitations": ("known", "limitation"),
                }
                for label_name, terms in required_concepts.items():
                    if not all(term in lowered for term in terms):
                        missing.append(f"DEMO_CHECKLIST.md section: {label_name}")
            if not readme.is_file():
                missing.append("README.md")
            else:
                lowered_readme = readme.read_text(encoding="utf-8", errors="replace").lower()
                if "quick start" not in lowered_readme and "quick-start" not in lowered_readme:
                    missing.append("README.md short Quick Start section")
        finally:
            self.repository.remove_worktree(workspace)
        message = "PASS\n" if not missing else "Missing: " + "; ".join(missing) + "\n"
        self.audit.write_text(f"{relative_root}/stdout.log", message)
        self.audit.write_text(f"{relative_root}/stderr.log", "")
        result = CheckResult(
            check_id=check_id,
            category="demo",
            required=True,
            weight="0",
            passed=not missing,
            command=("arena-internal", "demo-readiness-docs"),
            exit_code=0 if not missing else 1,
            timed_out=False,
            duration_seconds=0.0,
            stdout_path=f"{relative_root}/stdout.log",
            stderr_path=f"{relative_root}/stderr.log",
            error=None if not missing else "; ".join(missing),
        )
        self.audit.write_json(f"{relative_root}/result.json", result)
        return result

    def _run_process(
        self,
        command: tuple[str, ...],
        *,
        workspace: Path,
        timeout_seconds: int,
        env_additions: dict[str, str],
    ) -> ProcessResult:
        env = sanitized_environment(self.config.run.check_env_passthrough, env_additions)
        return self.runner.run(
            command,
            cwd=workspace,
            timeout_seconds=self._remaining_timeout(timeout_seconds),
            max_output_bytes=self.config.limits.max_output_bytes,
            env=env,
        )

    def _run_check(self, snapshot: CandidateSnapshot, check: CheckConfig) -> CheckResult:
        label = f"check-{snapshot.engineer_id}-{snapshot.generation}-{check.check_id}"
        relative_root = f"evaluations/{snapshot.engineer_id}/{snapshot.generation}/{check.check_id}"
        workspace = self.repository.create_detached_worktree(label, snapshot.commit)
        process: ProcessResult | None = None
        error: str | None = None
        try:
            _apply_overlay(self.overlay, workspace)
            process = self._run_process(
                check.command,
                workspace=workspace,
                timeout_seconds=(check.timeout_seconds or self.config.limits.check_timeout_seconds),
                env_additions=check.env,
            )
        except DeadlineExceeded:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self.repository.remove_worktree(workspace)
        stdout = process.stdout if process else ""
        stderr = process.stderr if process else ""
        self.audit.write_text(f"{relative_root}/stdout.log", stdout)
        self.audit.write_text(f"{relative_root}/stderr.log", stderr)
        if process:
            self.audit.write_json(f"{relative_root}/process.json", process)
        result = CheckResult(
            check_id=check.check_id,
            category=check.category,
            required=check.required,
            weight=str(check.weight),
            passed=bool(process and process.ok and error is None),
            command=check.command,
            exit_code=process.exit_code if process else None,
            timed_out=process.timed_out if process else False,
            duration_seconds=process.duration_seconds if process else 0.0,
            stdout_path=f"{relative_root}/stdout.log",
            stderr_path=f"{relative_root}/stderr.log",
            error=error,
        )
        self.audit.write_json(f"{relative_root}/result.json", result)
        return result

    def _run_benchmark(
        self, snapshot: CandidateSnapshot, benchmark: BenchmarkConfig
    ) -> BenchmarkResult:
        relative_root = (
            f"evaluations/{snapshot.engineer_id}/{snapshot.generation}/{benchmark.benchmark_id}"
        )
        run_records: list[dict[str, Any]] = []
        values: list[Decimal] = []
        error: str | None = None
        total_runs = benchmark.warmups + benchmark.repetitions
        for index in range(total_runs):
            kind = "warmup" if index < benchmark.warmups else "measured"
            ordinal = index if kind == "warmup" else index - benchmark.warmups
            label = (
                f"bench-{snapshot.engineer_id}-{snapshot.generation}-"
                f"{benchmark.benchmark_id}-{kind}-{ordinal}"
            )
            workspace = self.repository.create_detached_worktree(label, snapshot.commit)
            process: ProcessResult | None = None
            metric: Decimal | None = None
            run_error: str | None = None
            try:
                _apply_overlay(self.overlay, workspace)
                process = self._run_process(
                    benchmark.command,
                    workspace=workspace,
                    timeout_seconds=(
                        benchmark.timeout_seconds or self.config.limits.check_timeout_seconds
                    ),
                    env_additions=benchmark.env,
                )
                if not process.ok:
                    raise EvaluationError(
                        f"benchmark command failed with status {process.exit_code}"
                    )
                metric = _metric_from_output(process.stdout, benchmark.metric_key)
                if kind == "measured":
                    values.append(metric)
            except DeadlineExceeded:
                raise
            except Exception as exc:
                run_error = f"{type(exc).__name__}: {exc}"
                error = error or run_error
            finally:
                self.repository.remove_worktree(workspace)
            run_relative = f"{relative_root}/{kind}-{ordinal}"
            self.audit.write_text(f"{run_relative}.stdout.log", process.stdout if process else "")
            self.audit.write_text(f"{run_relative}.stderr.log", process.stderr if process else "")
            record = {
                "kind": kind,
                "ordinal": ordinal,
                "metric": str(metric) if metric is not None else None,
                "process": jsonable(process) if process else None,
                "error": run_error,
            }
            self.audit.write_json(f"{run_relative}.json", record)
            run_records.append(record)

        aggregate: Decimal | None = None
        normalized: Decimal | None = None
        if not error and len(values) == benchmark.repetitions:
            aggregate = Decimal(str(statistics.median(values)))
            normalized = _normalized(aggregate, benchmark)
            if benchmark.gate is not None:
                gate_ok = (
                    aggregate >= benchmark.gate
                    if benchmark.direction == "higher"
                    else aggregate <= benchmark.gate
                )
                if not gate_ok:
                    error = f"aggregate {aggregate} did not meet gate {benchmark.gate}"
        elif not error:
            error = "benchmark did not produce every configured measured value"
        result = BenchmarkResult(
            benchmark_id=benchmark.benchmark_id,
            required=benchmark.required,
            weight=str(benchmark.weight),
            passed=error is None,
            direction=benchmark.direction,
            values=tuple(str(item) for item in values),
            aggregate=str(aggregate) if aggregate is not None else None,
            normalized=str(normalized) if normalized is not None else None,
            runs=tuple(run_records),
            error=error,
        )
        self.audit.write_json(f"{relative_root}/result.json", result)
        return result
