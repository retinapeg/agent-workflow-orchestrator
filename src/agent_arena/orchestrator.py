from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .audit import AuditStore, sha256_bytes
from .config import ArenaConfig, EngineerConfig
from .errors import ArenaError, DeadlineExceeded, ProviderError, RepositoryError
from .evaluator import Evaluator, freeze_trusted_overlay, trusted_overlay_manifest
from .gitops import RepositoryManager, SourceEvidence
from .models import (
    AgentRequest,
    AgentResult,
    CandidateEvaluation,
    CandidateSnapshot,
    Decision,
    Phase,
    RunState,
    jsonable,
)
from .process import ProcessRunner
from .prompting import (
    JUDGE_SCHEMA,
    REVIEW_SCHEMA,
    PromptBook,
    acceptance_contract,
)
from .providers import Provider, create_provider
from .providers.api import extract_json_object
from .scoring import deterministic_decision, score_candidates


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _git_top_level(path: Path, timeout_seconds: int = 30) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryError(f"could not inspect source Git repository: {exc}") from exc
    if result.returncode != 0:
        raise RepositoryError(f"not a Git repository: {path}")
    return Path(result.stdout.decode().strip()).resolve()


def _bounded(text: str, maximum: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= maximum:
        return text
    half = maximum // 2
    return (
        raw[:half].decode("utf-8", errors="replace")
        + f"\n... <{len(raw) - maximum} bytes omitted> ...\n"
        + raw[-half:].decode("utf-8", errors="replace")
    )


def _review_is_valid(text: str, target: str) -> tuple[bool, str | None]:
    try:
        payload = extract_json_object(text)
    except ProviderError as exc:
        return False, str(exc)
    if payload.get("target") != target:
        return False, f"review target must be {target!r}"
    findings = payload.get("findings")
    attack_tests = payload.get("attack_tests")
    if not isinstance(payload.get("summary"), str):
        return False, "review summary must be a string"
    if not isinstance(findings, list) or not isinstance(attack_tests, list):
        return False, "review findings and attack_tests must be arrays"
    for finding in findings:
        if not isinstance(finding, dict):
            return False, "every finding must be an object"
        required = {
            "severity",
            "path",
            "line",
            "reproduction",
            "violated_requirement",
            "explanation",
            "confidence",
        }
        if set(finding) != required:
            return False, "a review finding has missing or extra fields"
        confidence = finding.get("confidence")
        severity = finding.get("severity")
        path = finding.get("path")
        line = finding.get("line")
        if not isinstance(severity, str) or severity not in {
            "critical",
            "high",
            "medium",
            "low",
            "note",
        }:
            return False, "finding severity is invalid"
        if path is not None and not isinstance(path, str):
            return False, "finding path must be a string or null"
        if line is not None and (isinstance(line, bool) or not isinstance(line, int)):
            return False, "finding line must be an integer or null"
        for field_name in (
            "reproduction",
            "violated_requirement",
            "explanation",
        ):
            if not isinstance(finding.get(field_name), str):
                return False, f"finding {field_name} must be a string"
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return False, "finding confidence must be numeric"
        if not 0 <= confidence <= 1:
            return False, "finding confidence must be between 0 and 1"
    if not all(isinstance(item, str) for item in attack_tests):
        return False, "attack_tests entries must be strings"
    return True, None


class ArenaOrchestrator:
    def __init__(self, config: ArenaConfig, mode: str) -> None:
        self.config = config
        self.mode = mode
        self.mode_config = config.mode(mode)
        self.runner = ProcessRunner(config.limits.termination_grace_seconds)
        self.prompt_book = PromptBook(config)
        self.started = time.monotonic()
        self.max_run_seconds = min(config.run.max_run_seconds, self.mode_config.max_run_seconds)
        self.deadline = self.started + self.max_run_seconds
        self.audit: AuditStore | None = None
        self.repository: RepositoryManager | None = None
        self.providers: dict[str, Provider] = {}
        self._active_providers: dict[str, dict[str, Any]] = {}
        self._active_lock = threading.Lock()

    def doctor(self) -> dict[str, Any]:
        details: dict[str, Any] = {"ok": True, "engineers": {}}
        for engineer in self.config.engineers:
            try:
                provider = create_provider(engineer, self.config, self.runner)
                details["engineers"][engineer.engineer_id] = {
                    "ok": True,
                    **provider.preflight(),
                }
            except Exception as exc:
                details["ok"] = False
                details["engineers"][engineer.engineer_id] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        try:
            details["trusted_overlay"] = trusted_overlay_manifest(self.config)
        except Exception as exc:
            details["ok"] = False
            details["trusted_overlay"] = {"error": f"{type(exc).__name__}: {exc}"}
        return details

    def run(self, source: Path, task_path: Path, base_ref: str = "HEAD") -> Path:
        self.runner.reset_cancellation()
        self.started = time.monotonic()
        self.deadline = self.started + self.max_run_seconds
        source_top = _git_top_level(source.expanduser().resolve(), min(30, self.max_run_seconds))
        artifact_root = self.config.run.artifact_root.resolve()
        if artifact_root == source_top or source_top in artifact_root.parents:
            raise RepositoryError(
                "run.artifact_root must be outside the source repository so audit files cannot "
                "dirty or recurse into the frozen source"
            )
        task = task_path.expanduser().resolve().read_text(encoding="utf-8")
        if not task.strip():
            raise ArenaError("task specification is empty")
        if len(task.encode("utf-8")) > self.config.limits.max_prompt_bytes // 2:
            raise ArenaError("task specification is too large for the configured prompt budget")

        run_id = _run_id()
        artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        audit = AuditStore(artifact_root / run_id, run_id)
        self.audit = audit
        started_at = datetime.now(UTC)
        audit.update_run(
            mode=self.mode,
            source_repo=str(source_top),
            base_ref=base_ref,
            started_at=started_at.isoformat(),
            deadline_at=(started_at + timedelta(seconds=self.max_run_seconds)).isoformat(),
            max_run_seconds=self.max_run_seconds,
            provider_timeout_seconds=self.mode_config.provider_timeout_seconds,
            min_response_bytes=self.mode_config.min_response_bytes,
        )
        try:
            if self.mode == "hackathon":
                missing = [
                    name
                    for name in ("DEMO.md", "NOT.md")
                    if not (source_top / name).is_file()
                    or (source_top / name).is_symlink()
                    or (source_top / name).stat().st_size == 0
                ]
                if missing:
                    raise ArenaError(
                        "hackathon mode requires nonempty, regular DEMO.md and NOT.md files "
                        f"before any provider starts; missing or invalid: {', '.join(missing)}"
                    )
            repository = RepositoryManager(source_top, base_ref, self.config, audit, self.deadline)
            self.repository = repository
            return self._run_lifecycle(repository, audit, task)
        except KeyboardInterrupt:
            self.runner.cancel_all()
            audit.update_run(active_providers=[])
            audit.transition(RunState.CANCELLED, error="interrupted by user")
            audit.build_manifest()
            raise
        except Exception as exc:
            audit.update_run(active_providers=[])
            audit.write_text("failure.txt", f"{type(exc).__name__}: {exc}\n")
            audit.transition(RunState.FAILED, error=f"{type(exc).__name__}: {exc}")
            audit.build_manifest()
            raise

    def _run_lifecycle(self, repository: RepositoryManager, audit: AuditStore, task: str) -> Path:
        audit.transition(RunState.PREFLIGHT)
        audit.write_json(
            "effective-config.json",
            {"selected_mode": self.mode, "config": self.config.audit_dict()},
        )
        doctor = self.doctor()
        audit.write_json("preflight.json", doctor)
        if not doctor["ok"]:
            raise ProviderError("one or more provider preflight checks failed; see preflight.json")
        for engineer in self.config.engineers:
            self.providers[engineer.engineer_id] = create_provider(
                engineer, self.config, self.runner
            )
        frozen_overlay = freeze_trusted_overlay(self.config, audit)
        overlay = (
            frozen_overlay.audit_dict(self.config.run.trusted_overlay)
            if frozen_overlay is not None and self.config.run.trusted_overlay is not None
            else None
        )
        acceptance = acceptance_contract(self.config, self.mode)
        audit.write_text("spec.md", task)
        audit.write_json(
            "acceptance.json",
            {
                "contract": acceptance,
                "contract_sha256": sha256_bytes(acceptance.encode()),
                "mode": self.mode,
                "trusted_overlay": overlay,
                "note": (
                    "commands and overlays are coordinator-owned; agent suggestions do not "
                    "alter them"
                ),
            },
        )

        evidence = repository.inspect_source()
        audit.write_json("repository.json", evidence)
        audit.transition(RunState.SNAPSHOT)
        repository.create_private_clone(evidence)
        workspaces = {
            engineer.engineer_id: repository.create_engineer_worktree(
                engineer.engineer_id, evidence.base_commit
            )
            for engineer in self.config.engineers
        }
        workspace_records = repository.engineer_workspace_records()
        if {record.engineer_id for record in workspace_records} != set(workspaces):
            raise RepositoryError("coordinator workspace records are incomplete")
        initial_trees = {record.engineer_id: record.baseline_tree for record in workspace_records}
        if (
            len(set(initial_trees.values())) != 1
            or next(iter(initial_trees.values())) != evidence.base_tree
        ):
            raise RepositoryError("engineer worktrees did not begin from one identical tree")
        audit.write_json(
            "workspaces.json",
            {
                "schema_version": 1,
                "private_repository_path": str(repository.private_repo.resolve()),
                "base_commit": evidence.base_commit,
                "base_tree": evidence.base_tree,
                "engineers": workspace_records,
            },
        )
        audit.write_json(
            "common-inputs.json",
            {
                "task_sha256": sha256_bytes(task.encode()),
                "acceptance_sha256": sha256_bytes(acceptance.encode()),
                "base_commit": evidence.base_commit,
                "base_tree": evidence.base_tree,
                "mode": self.mode,
                "revision_rounds": self.mode_config.revision_rounds,
                "engineer_initial_trees": initial_trees,
            },
        )

        audit.transition(RunState.IMPLEMENT)
        initial_results = self._parallel_by_engineer(
            lambda engineer: self._invoke_implementation(
                engineer, workspaces[engineer.engineer_id], evidence, task, acceptance
            )
        )

        audit.transition(RunState.FREEZE_INITIAL)
        initial_snapshots: dict[str, CandidateSnapshot] = {}
        for engineer in self.config.engineers:
            initial_snapshots[engineer.engineer_id] = self._freeze_or_invalid(
                engineer.engineer_id,
                "initial",
                workspaces[engineer.engineer_id],
                evidence.base_commit,
                evidence.base_commit,
            )

        evaluator = Evaluator(
            self.config,
            repository,
            audit,
            self.runner,
            self.mode,
            frozen_overlay,
            self.deadline,
        )
        audit.transition(RunState.EVALUATE_INITIAL)
        initial_evaluations: dict[str, CandidateEvaluation] = {}
        for engineer in self.config.engineers:
            engineer_id = engineer.engineer_id
            initial_evaluations[engineer_id] = evaluator.evaluate(
                initial_snapshots[engineer_id], provider_ok=initial_results[engineer_id].ok
            )

        final_snapshots = initial_snapshots
        final_evaluations = initial_evaluations
        current_results = initial_results
        for round_number in range(1, self.mode_config.revision_rounds + 1):
            audit.transition(RunState.CROSS_REVIEW)
            audit.event(
                "adversarial_round_started",
                round_number=round_number,
                total_rounds=self.mode_config.revision_rounds,
                mode=self.mode,
            )
            reviews = self._cross_review(
                evidence,
                task,
                acceptance,
                final_snapshots,
                final_evaluations,
                current_results,
                round_number,
            )

            audit.transition(RunState.REVISE)
            revision_results: dict[str, AgentResult] = {}
            eligible_for_revision = [
                engineer
                for engineer in self.config.engineers
                if current_results[engineer.engineer_id].ok
                and final_snapshots[engineer.engineer_id].valid
            ]
            executor = ThreadPoolExecutor(
                max_workers=min(self.config.run.max_workers, max(1, len(eligible_for_revision)))
            )
            future_to_id: dict[Any, str] = {}
            try:
                future_to_id = {
                    executor.submit(
                        self._invoke_revision,
                        engineer,
                        workspaces[engineer.engineer_id],
                        evidence,
                        task,
                        acceptance,
                        final_snapshots[engineer.engineer_id],
                        final_evaluations[engineer.engineer_id],
                        reviews.get(engineer.engineer_id, []),
                        round_number,
                        self.mode_config.revision_rounds,
                    ): engineer.engineer_id
                    for engineer in eligible_for_revision
                }
                for future in as_completed(future_to_id):
                    engineer_id = future_to_id[future]
                    revision_results[engineer_id] = future.result()
            except BaseException:
                self._cancel_parallel(executor, future_to_id)
                raise
            else:
                executor.shutdown(wait=True)
            for engineer in self.config.engineers:
                if engineer.engineer_id not in revision_results:
                    revision_results[engineer.engineer_id] = AgentResult(
                        engineer_id=engineer.engineer_id,
                        phase=Phase.REVISE,
                        provider_kind=engineer.kind,
                        model=engineer.model,
                        status="failed",
                        error="revision skipped because the prior candidate was invalid",
                    )
                    self._save_agent_result(
                        revision_results[engineer.engineer_id],
                        f"engineers/{engineer.engineer_id}/revisions/round-{round_number}",
                    )

            audit.transition(RunState.FREEZE_FINAL)
            round_snapshots: dict[str, CandidateSnapshot] = {}
            revising_ids = {item.engineer_id for item in eligible_for_revision}
            for engineer in self.config.engineers:
                engineer_id = engineer.engineer_id
                if engineer_id in revising_ids:
                    round_snapshots[engineer_id] = self._freeze_or_invalid(
                        engineer_id,
                        f"revision-{round_number}",
                        workspaces[engineer_id],
                        evidence.base_commit,
                        final_snapshots[engineer_id].commit,
                    )
                else:
                    round_snapshots[engineer_id] = self._skipped_snapshot(
                        final_snapshots[engineer_id], round_number
                    )

            audit.transition(RunState.EVALUATE_FINAL)
            round_evaluations: dict[str, CandidateEvaluation] = {}
            for engineer in self.config.engineers:
                engineer_id = engineer.engineer_id
                round_evaluations[engineer_id] = evaluator.evaluate(
                    round_snapshots[engineer_id], provider_ok=revision_results[engineer_id].ok
                )
            final_snapshots = round_snapshots
            final_evaluations = round_evaluations
            current_results = revision_results
            audit.event(
                "adversarial_round_finished",
                round_number=round_number,
                candidates={key: value.commit for key, value in final_snapshots.items()},
            )

        audit.transition(RunState.SCORE)
        scores = score_candidates(self.config, self.mode, final_snapshots, final_evaluations)
        decision = deterministic_decision(scores)
        audit.write_json("deterministic-scores.json", scores)

        if self.config.judge.enabled and decision.winner:
            audit.transition(RunState.OPTIONAL_JUDGE)
            decision = self._judge(
                decision,
                evidence,
                task,
                acceptance,
                final_snapshots,
                final_evaluations,
            )

        audit.write_json("decision.json", decision)
        self._write_salvage(audit, decision, final_snapshots)
        if decision.winner is None:
            audit.transition(RunState.NO_WINNER)
            self._write_report(audit, evidence, decision, final_snapshots, final_evaluations)
            audit.build_manifest()
            return audit.run_dir

        audit.transition(RunState.EXPORT_WINNER)
        winner_snapshot = final_snapshots[decision.winner]
        winner_patch = audit.path(winner_snapshot.patch_path).read_bytes()
        audit.write_bytes("winner.patch", winner_patch)
        repository.export_bundle(winner_snapshot.commit, audit.path("winner.bundle"))
        audit.write_json(
            "winner.json",
            {
                "engineer_id": decision.winner,
                "generation": winner_snapshot.generation,
                "base_commit": evidence.base_commit,
                "commit": winner_snapshot.commit,
                "tree": winner_snapshot.tree,
                "patch_sha256": sha256_bytes(winner_patch),
                "integration": "not applied; use the explicit integrate command",
            },
        )
        audit.transition(RunState.COMPLETE)
        self._write_report(audit, evidence, decision, final_snapshots, final_evaluations)
        if not self.config.run.keep_private_clone:
            private = audit.path("private")
            if private.exists() and private.parent == audit.run_dir:
                shutil.rmtree(private)
        audit.build_manifest()
        return audit.run_dir

    def _parallel_by_engineer(
        self, action: Callable[[EngineerConfig], AgentResult]
    ) -> dict[str, AgentResult]:
        results: dict[str, AgentResult] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(self.config.run.max_workers, len(self.config.engineers))
        )
        future_to_id: dict[Any, str] = {}
        try:
            future_to_id = {
                executor.submit(action, engineer): engineer.engineer_id
                for engineer in self.config.engineers
            }
            for future in as_completed(future_to_id):
                results[future_to_id[future]] = future.result()
        except BaseException:
            self._cancel_parallel(executor, future_to_id)
            raise
        else:
            executor.shutdown(wait=True)
        return results

    def _cancel_parallel(self, executor: ThreadPoolExecutor, futures: dict[Any, str]) -> None:
        self.runner.cancel_all()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)

    def _timeout(self, engineer: EngineerConfig, override: int | None = None) -> int:
        remaining = int(self.deadline - time.monotonic())
        if remaining <= 0:
            raise DeadlineExceeded("the total run deadline was exhausted")
        configured = min(
            override or self.config.limits.provider_timeout_seconds,
            engineer.timeout_seconds or self.config.limits.provider_timeout_seconds,
            self.mode_config.provider_timeout_seconds,
        )
        return min(configured, remaining)

    def _provider_status_start(
        self, engineer: EngineerConfig, request: AgentRequest, relative_dir: str
    ) -> None:
        assert self.audit is not None
        started_at = datetime.now(UTC)
        with self._active_lock:
            self._active_providers[engineer.engineer_id] = {
                "engineer_id": engineer.engineer_id,
                "provider_kind": engineer.kind,
                "phase": request.phase.value,
                "artifact_dir": relative_dir,
                "started_at": started_at.isoformat(),
                "deadline_at": (
                    started_at + timedelta(seconds=request.timeout_seconds)
                ).isoformat(),
            }
            active = [self._active_providers[key] for key in sorted(self._active_providers)]
        self.audit.update_run(active_providers=active)

    def _provider_status_finish(self, engineer_id: str) -> None:
        assert self.audit is not None
        with self._active_lock:
            self._active_providers.pop(engineer_id, None)
            active = [self._active_providers[key] for key in sorted(self._active_providers)]
        self.audit.update_run(active_providers=active)

    def _request(
        self,
        engineer: EngineerConfig,
        phase: Phase,
        workspace: Path,
        prompt: str,
        *,
        response_schema: dict[str, Any] | None = None,
        timeout_override: int | None = None,
    ) -> AgentRequest:
        return AgentRequest(
            engineer_id=engineer.engineer_id,
            phase=phase,
            model=engineer.model,
            prompt=prompt,
            workspace=workspace,
            timeout_seconds=self._timeout(engineer, timeout_override),
            max_output_bytes=self.config.limits.max_output_bytes,
            max_output_tokens=engineer.max_output_tokens or self.config.limits.max_output_tokens,
            max_cost_usd=engineer.max_cost_usd,
            response_schema=response_schema,
        )

    def _invoke(
        self, engineer: EngineerConfig, request: AgentRequest, relative_dir: str
    ) -> AgentResult:
        assert self.audit is not None
        artifact_dir = self.audit.mkdir(relative_dir)
        self.audit.write_text(f"{relative_dir}/prompt.md", request.prompt)
        self._provider_status_start(engineer, request, relative_dir)
        self.audit.event(
            "provider_started",
            engineer_id=engineer.engineer_id,
            phase=request.phase.value,
            prompt_sha256=sha256_bytes(request.prompt.encode()),
        )
        try:
            result = self.providers[engineer.engineer_id].run(request, artifact_dir)
        except Exception as exc:
            result = AgentResult(
                engineer_id=engineer.engineer_id,
                phase=request.phase,
                provider_kind=engineer.kind,
                model=engineer.model,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            # Provider CLIs/SDKs may create auxiliary audit files using the
            # process umask. Normalize retained regular files; symlinks are
            # left untouched and rejected later by manifest verification.
            for path in artifact_dir.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                os.chmod(path, 0o600)
        response_bytes = len(result.text.strip().encode("utf-8"))
        if result.ok and response_bytes < self.mode_config.min_response_bytes:
            result.status = "failed"
            result.error = (
                f"provider returned {response_bytes} substantive response bytes; "
                f"mode requires at least {self.mode_config.min_response_bytes}"
            )
        try:
            self._save_agent_result(result, relative_dir)
            self.audit.event(
                "provider_finished",
                engineer_id=engineer.engineer_id,
                phase=request.phase.value,
                status=result.status,
                error=result.error,
            )
            return result
        finally:
            self._provider_status_finish(engineer.engineer_id)

    def _save_agent_result(self, result: AgentResult, relative_dir: str) -> None:
        assert self.audit is not None
        self.audit.mkdir(relative_dir)
        self.audit.write_text(f"{relative_dir}/response.md", result.text)
        if result.process:
            self.audit.write_text(f"{relative_dir}/stdout.log", result.process.stdout)
            self.audit.write_text(f"{relative_dir}/stderr.log", result.process.stderr)
        self.audit.write_json(f"{relative_dir}/result.json", result)

    def _invoke_implementation(
        self,
        engineer: EngineerConfig,
        workspace: Path,
        evidence: SourceEvidence,
        task: str,
        acceptance: str,
    ) -> AgentResult:
        prompt = self.prompt_book.render(
            "implement",
            ENGINEER_ID=engineer.engineer_id,
            TASK=task,
            ACCEPTANCE=acceptance,
            BASE_SHA=evidence.base_commit,
        )
        return self._invoke(
            engineer,
            self._request(engineer, Phase.IMPLEMENT, workspace, prompt),
            f"engineers/{engineer.engineer_id}/initial",
        )

    def _freeze_or_invalid(
        self,
        engineer_id: str,
        generation: str,
        workspace: Path,
        baseline: str,
        ancestor: str,
    ) -> CandidateSnapshot:
        assert self.repository is not None
        assert self.audit is not None
        try:
            snapshot = self.repository.freeze_candidate(
                engineer_id, generation, workspace, baseline, ancestor
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            relative = f"engineers/{engineer_id}/{generation}"
            self.audit.write_text(f"{relative}/freeze_error.txt", error + "\n")
            self.audit.write_bytes(f"{relative}/candidate.patch", b"")
            snapshot = CandidateSnapshot(
                engineer_id=engineer_id,
                generation=generation,
                commit=baseline,
                tree=self.repository.tree_for(baseline),
                parent_commit=ancestor,
                changed_files=(),
                changed_lines=0,
                diff_bytes=0,
                patch_path=f"{relative}/candidate.patch",
                policy_violations=("candidate could not be frozen safely",),
                valid=False,
                error=error,
            )
            self.audit.write_json(f"{relative}/candidate.json", snapshot)
        self.audit.event(
            "candidate_frozen",
            engineer_id=engineer_id,
            generation=generation,
            commit=snapshot.commit,
            tree=snapshot.tree,
            valid=snapshot.valid,
        )
        return snapshot

    def _skipped_snapshot(
        self, previous: CandidateSnapshot, round_number: int
    ) -> CandidateSnapshot:
        assert self.audit is not None
        generation = f"revision-{round_number}"
        relative = f"engineers/{previous.engineer_id}/{generation}"
        source_patch = self.audit.path(previous.patch_path).read_bytes()
        patch_path = f"{relative}/candidate.patch"
        self.audit.write_bytes(patch_path, source_patch)
        snapshot = replace(
            previous,
            generation=generation,
            patch_path=patch_path,
            valid=False,
            error="revision skipped because the prior candidate was invalid",
            policy_violations=tuple(
                sorted(set(previous.policy_violations) | {"required revision was skipped"})
            ),
        )
        self.audit.write_json(f"{relative}/candidate.json", snapshot)
        return snapshot

    def _cross_review(
        self,
        evidence: SourceEvidence,
        task: str,
        acceptance: str,
        snapshots: dict[str, CandidateSnapshot],
        evaluations: dict[str, CandidateEvaluation],
        initial_results: dict[str, AgentResult],
        round_number: int,
    ) -> dict[str, list[dict[str, Any]]]:
        assert self.repository is not None
        assert self.audit is not None
        repository = self.repository
        audit = self.audit
        critiques_for_target: dict[str, list[dict[str, Any]]] = {
            engineer.engineer_id: [] for engineer in self.config.engineers
        }

        def review_as(reviewer: EngineerConfig) -> list[tuple[str, dict[str, Any]]]:
            authored: list[tuple[str, dict[str, Any]]] = []
            for target in sorted(snapshots):
                if target == reviewer.engineer_id:
                    continue
                snapshot = snapshots[target]
                label = f"review-r{round_number}-{reviewer.engineer_id}-of-{target}"
                workspace = repository.create_detached_worktree(label, evidence.base_commit)
                try:
                    patch = audit.path(snapshot.patch_path).read_text(
                        encoding="utf-8", errors="replace"
                    )
                    prompt = self.prompt_book.render(
                        "review",
                        ENGINEER_ID=reviewer.engineer_id,
                        TASK=task,
                        ACCEPTANCE=acceptance,
                        TARGET_ID=target,
                        BASE_SHA=evidence.base_commit,
                        TARGET_DIFF=_bounded(
                            patch, max(1, self.config.limits.max_prompt_bytes // 2)
                        ),
                        TARGET_EVALUATION=json.dumps(
                            jsonable(evaluations[target]), indent=2, sort_keys=True
                        ),
                    )
                    relative = (
                        f"engineers/{reviewer.engineer_id}/reviews/round-{round_number}/{target}"
                    )
                    result = self._invoke(
                        reviewer,
                        self._request(
                            reviewer,
                            Phase.REVIEW,
                            workspace,
                            prompt,
                            response_schema=REVIEW_SCHEMA,
                        ),
                        relative,
                    )
                    valid, error = (
                        _review_is_valid(result.text, target)
                        if result.ok
                        else (False, result.error)
                    )
                    if not valid:
                        result.status = "failed"
                        result.error = error
                        self._save_agent_result(result, relative)
                        payload: dict[str, Any] = {
                            "reviewer": reviewer.engineer_id,
                            "target": target,
                            "status": "failed",
                            "error": error,
                            "review": None,
                        }
                    else:
                        payload = {
                            "reviewer": reviewer.engineer_id,
                            "target": target,
                            "status": "completed",
                            "error": None,
                            "review": extract_json_object(result.text),
                        }
                    audit.write_json(f"{relative}/critique.json", payload)
                    authored.append((target, payload))
                finally:
                    repository.remove_worktree(workspace)
            return authored

        active_reviewers = [
            engineer
            for engineer in self.config.engineers
            if initial_results[engineer.engineer_id].ok
        ]
        executor = ThreadPoolExecutor(
            max_workers=min(self.config.run.max_workers, max(1, len(active_reviewers)))
        )
        futures: list[Any] = []
        try:
            futures = [executor.submit(review_as, reviewer) for reviewer in active_reviewers]
            for future in as_completed(futures):
                for target, payload in future.result():
                    critiques_for_target[target].append(payload)
        except BaseException:
            self._cancel_parallel(
                executor,
                {future: "review" for future in futures},
            )
            raise
        else:
            executor.shutdown(wait=True)
        for target in critiques_for_target:
            critiques_for_target[target].sort(key=lambda item: item["reviewer"])
        return critiques_for_target

    @staticmethod
    def _blinded_evaluation(evaluation: CandidateEvaluation) -> dict[str, Any]:
        """Remove provider identity, commit IDs, and workspace-bearing evidence paths."""

        return {
            "generation": evaluation.generation,
            "valid_candidate": evaluation.valid_candidate,
            "provider_ok": evaluation.provider_ok,
            "policy_violations": list(evaluation.policy_violations),
            "checks": [
                {
                    "check_id": item.check_id,
                    "category": item.category,
                    "required": item.required,
                    "weight": item.weight,
                    "passed": item.passed,
                    "exit_code": item.exit_code,
                    "timed_out": item.timed_out,
                    "duration_seconds": item.duration_seconds,
                    "error_present": item.error is not None,
                }
                for item in evaluation.checks
            ],
            "benchmarks": [
                {
                    "benchmark_id": item.benchmark_id,
                    "required": item.required,
                    "weight": item.weight,
                    "passed": item.passed,
                    "direction": item.direction,
                    "values": list(item.values),
                    "aggregate": item.aggregate,
                    "normalized": item.normalized,
                    "error_present": item.error is not None,
                }
                for item in evaluation.benchmarks
            ],
        }

    def _invoke_revision(
        self,
        engineer: EngineerConfig,
        workspace: Path,
        evidence: SourceEvidence,
        task: str,
        acceptance: str,
        current_snapshot: CandidateSnapshot,
        own_evaluation: CandidateEvaluation,
        critiques: list[dict[str, Any]],
        round_number: int,
        total_rounds: int,
    ) -> AgentResult:
        prompt = self.prompt_book.render(
            "revise",
            ENGINEER_ID=engineer.engineer_id,
            TASK=task,
            ACCEPTANCE=acceptance,
            CURRENT_COMMIT=current_snapshot.commit,
            # Backward-compatible value for custom prompt directories created
            # before CURRENT_COMMIT named the evolving round input accurately.
            INITIAL_COMMIT=current_snapshot.commit,
            OWN_EVALUATION=json.dumps(jsonable(own_evaluation), indent=2, sort_keys=True),
            CRITIQUES=json.dumps(critiques, indent=2, sort_keys=True),
            ROUND_NUMBER=str(round_number),
            TOTAL_ROUNDS=str(total_rounds),
        )
        return self._invoke(
            engineer,
            self._request(engineer, Phase.REVISE, workspace, prompt),
            f"engineers/{engineer.engineer_id}/revisions/round-{round_number}",
        )

    def _write_salvage(
        self,
        audit: AuditStore,
        decision: Decision,
        snapshots: dict[str, CandidateSnapshot],
    ) -> None:
        lines = [
            "# Preserved losing-candidate assets",
            "",
            "Nothing in this file is merged automatically. It is an inventory for human review.",
            "",
        ]
        losers = [item for item in sorted(snapshots) if item != decision.winner]
        if not losers:
            lines.append("No losing candidate was available.")
        for engineer_id in losers:
            snapshot = snapshots[engineer_id]
            likely_tests = [
                path
                for path in snapshot.changed_files
                if "test" in Path(path).name.lower()
                or any(
                    part.lower() in {"test", "tests", "spec", "specs"} for part in Path(path).parts
                )
            ]
            lines.extend(
                [
                    f"## {engineer_id}",
                    "",
                    f"- Frozen commit: `{snapshot.commit}`",
                    f"- Full patch: `{snapshot.patch_path}`",
                    "- Candidate-authored test/spec files: "
                    + (
                        ", ".join(f"`{path}`" for path in likely_tests)
                        if likely_tests
                        else "none detected"
                    ),
                    f"- Adversarial reviews remain under `engineers/*/reviews/*/{engineer_id}/`.",
                    "",
                ]
            )
        audit.write_text("salvage.md", "\n".join(lines))

    def _judge(
        self,
        decision: Decision,
        evidence: SourceEvidence,
        task: str,
        acceptance: str,
        snapshots: dict[str, CandidateSnapshot],
        evaluations: dict[str, CandidateEvaluation],
    ) -> Decision:
        assert self.repository is not None
        assert self.audit is not None
        eligible_scores = [item for item in decision.scores if item.eligible]
        if len(eligible_scores) < 2:
            return replace(
                decision, fallback_reason="judge skipped: fewer than two eligible candidates"
            )
        top_score = eligible_scores[0].score_basis_points
        allowed_ids = [
            item.engineer_id
            for item in eligible_scores
            if top_score - item.score_basis_points <= self.config.judge.score_band_basis_points
        ]
        if len(allowed_ids) < 2:
            return replace(
                decision,
                judge_candidates=tuple(allowed_ids),
                fallback_reason=(
                    "judge skipped: only one candidate is inside the configured score band"
                ),
            )
        labels = {
            engineer_id: f"candidate-{chr(97 + index)}"
            for index, engineer_id in enumerate(sorted(allowed_ids))
        }
        evidence_parts: list[str] = []
        per_candidate_budget = max(1, self.config.limits.max_prompt_bytes // (2 * len(allowed_ids)))
        for engineer_id in sorted(allowed_ids):
            patch = self.audit.path(snapshots[engineer_id].patch_path).read_text(
                encoding="utf-8", errors="replace"
            )
            evidence_parts.append(
                f"\n### {labels[engineer_id]}\n"
                + json.dumps(
                    self._blinded_evaluation(evaluations[engineer_id]),
                    indent=2,
                    sort_keys=True,
                )
                + "\nDIFF:\n"
                + _bounded(patch, per_candidate_budget)
            )
        prompt = self.prompt_book.render(
            "judge",
            TASK=task,
            ACCEPTANCE=acceptance,
            ALLOWED_CANDIDATES=json.dumps(sorted(labels.values())),
            CANDIDATE_EVIDENCE="\n".join(evidence_parts),
        )
        judge_engineer = self.config.engineer(self.config.judge.engineer or "")
        workspace = self.repository.create_detached_worktree("judge", evidence.base_commit)
        try:
            result = self._invoke(
                judge_engineer,
                self._request(
                    judge_engineer,
                    Phase.JUDGE,
                    workspace,
                    prompt,
                    response_schema=JUDGE_SCHEMA,
                    timeout_override=self.config.judge.timeout_seconds,
                ),
                "judge",
            )
        finally:
            self.repository.remove_worktree(workspace)
        if not result.ok:
            return replace(
                decision,
                judge_candidates=tuple(allowed_ids),
                fallback_reason=f"judge failed: {result.error}",
            )
        try:
            payload = extract_json_object(result.text)
            selected_label = payload["winner"]
            reason = payload["reason"]
            confidence = payload["confidence"]
            if (
                not isinstance(selected_label, str)
                or not isinstance(reason, str)
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
            ):
                raise ValueError("judge winner/reason/confidence values are invalid")
            reverse = {label: engineer_id for engineer_id, label in labels.items()}
            selected = reverse[selected_label]
        except (KeyError, TypeError, ValueError, ProviderError) as exc:
            return replace(
                decision,
                judge_candidates=tuple(allowed_ids),
                fallback_reason=f"judge response rejected: {exc}",
            )
        self.audit.write_json(
            "judge/selection.json",
            {
                "blinded_mapping": labels,
                "selected_label": selected_label,
                "selected_engineer": selected,
                "reason": reason,
                "confidence": confidence,
            },
        )
        return replace(
            decision,
            winner=selected,
            judge_used=True,
            judge_candidates=tuple(allowed_ids),
            judge_reason=reason,
            fallback_reason=None,
        )

    def _write_report(
        self,
        audit: AuditStore,
        evidence: SourceEvidence,
        decision: Decision,
        snapshots: dict[str, CandidateSnapshot],
        evaluations: dict[str, CandidateEvaluation],
    ) -> None:
        lines = [
            "# Agent Arena run report",
            "",
            f"- Run: `{audit.run_id}`",
            f"- Source: `{evidence.source_path}`",
            f"- Frozen baseline: `{evidence.base_commit}`",
            f"- Mode: `{self.mode}`",
            f"- Adversarial revision rounds: `{self.mode_config.revision_rounds}`",
            f"- State: `{'COMPLETE' if decision.winner else 'NO_WINNER'}`",
            f"- Winner: `{decision.winner}`" if decision.winner else "- Winner: none",
            f"- Judge used: `{str(decision.judge_used).lower()}`",
            "",
            "## Deterministic ranking",
            "",
            "| Rank | Engineer | Eligible | Score / 10000 | Changed lines | Disqualifications |",
            "|---:|---|:---:|---:|---:|---|",
        ]
        for index, score in enumerate(decision.scores, 1):
            disqualifications = "; ".join(score.disqualifications) or "none"
            lines.append(
                f"| {index} | {score.engineer_id} | {str(score.eligible).lower()} | "
                f"{score.score_basis_points} | {score.changed_lines} | {disqualifications} |"
            )
        lines.extend(
            [
                "",
                "## Candidate evidence",
                "",
            ]
        )
        for engineer_id in sorted(snapshots):
            snapshot = snapshots[engineer_id]
            evaluation = evaluations[engineer_id]
            check_summary = (
                ", ".join(
                    f"{item.check_id}={'PASS' if item.passed else 'FAIL'}"
                    for item in evaluation.checks
                )
                or "not run"
            )
            benchmark_summary = (
                ", ".join(
                    f"{item.benchmark_id}="
                    f"{item.aggregate if item.aggregate is not None else 'FAIL'}"
                    for item in evaluation.benchmarks
                )
                or "none"
            )
            lines.extend(
                [
                    f"### {engineer_id}",
                    "",
                    f"- Commit/tree: `{snapshot.commit}` / `{snapshot.tree}`",
                    f"- Checks: {check_summary}",
                    f"- Benchmarks: {benchmark_summary}",
                    f"- Policy violations: {', '.join(snapshot.policy_violations) or 'none'}",
                    "",
                ]
            )
        lines.extend(
            [
                "## Integration status",
                "",
                "No source branch was changed automatically. If there is a winner, verify the "
                "audit manifest and use `team integrate` to create a new integration branch "
                "and worktree from the recorded baseline. The command refuses dirty or advanced "
                "sources.",
                "",
                "## Security boundary",
                "",
                "This run used local trusted-host isolation. The private clone and worktrees "
                "protect against ordinary collisions, not a deliberately malicious process with "
                "the same OS user permissions. Candidate tests also execute candidate code. Use a "
                "container or VM wrapper for untrusted repositories.",
                "",
            ]
        )
        if decision.fallback_reason:
            lines.extend(["## Judge fallback", "", decision.fallback_reason, ""])
        audit.write_text("report.md", "\n".join(lines))
