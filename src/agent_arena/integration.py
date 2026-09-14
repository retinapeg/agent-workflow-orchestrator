from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .audit import rebuild_manifest, sha256_bytes, sha256_file, verify_manifest
from .errors import IntegrityError, RepositoryError
from .process import sanitized_environment


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=180,
        check=False,
        env=env,
    )
    if check and result.returncode != 0:
        raise RepositoryError(
            f"git {' '.join(args)} failed: "
            + result.stderr.decode("utf-8", errors="replace")[:2000]
        )
    return result


def _text(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.decode("utf-8", errors="strict").strip()


def _write_receipt(run_dir: Path, receipt: dict[str, Any]) -> Path:
    receipts = run_dir / "integration-receipts"
    receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = receipts / f"{timestamp}.json"
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rebuild_manifest(run_dir, str(run_data["run_id"]))
    return target


def _commit_environment() -> dict[str, str]:
    return sanitized_environment(
        ("PATH", "LANG", "LC_ALL", "TMPDIR"),
        {
            "GIT_AUTHOR_NAME": "Agent Arena",
            "GIT_AUTHOR_EMAIL": "agent-arena@localhost",
            "GIT_COMMITTER_NAME": "Agent Arena",
            "GIT_COMMITTER_EMAIL": "agent-arena@localhost",
        },
    )


def _verified_evaluation(
    run_dir: Path,
    relative: str,
    engineer_id: str,
    generation: str,
    commit: str,
    required_checks: set[str],
    required_benchmarks: set[str],
) -> dict[str, Any]:
    path = (run_dir / relative).resolve()
    if run_dir not in path.parents or not path.is_file():
        raise IntegrityError("adaptive evaluation path is missing or unsafe")
    evaluation = json.loads(path.read_text(encoding="utf-8"))
    if (
        evaluation.get("engineer_id") != engineer_id
        or evaluation.get("generation") != generation
        or evaluation.get("commit") != commit
        or evaluation.get("valid_candidate") is not True
        or evaluation.get("provider_ok") is not True
    ):
        raise IntegrityError("adaptive evaluation conflicts with accepted-task evidence")
    checks = evaluation.get("checks")
    benchmarks = evaluation.get("benchmarks")
    if not isinstance(checks, list) or not all(isinstance(item, dict) for item in checks):
        raise IntegrityError("adaptive evaluation checks are malformed")
    if not isinstance(benchmarks, list) or not all(isinstance(item, dict) for item in benchmarks):
        raise IntegrityError("adaptive evaluation benchmarks are malformed")
    if any(item.get("required") and not item.get("passed") for item in checks):
        raise IntegrityError("adaptive evaluation contains a failed required check")
    if any(item.get("required") and not item.get("passed") for item in benchmarks):
        raise IntegrityError("adaptive evaluation contains a failed required benchmark")
    passed_checks = {item.get("check_id") for item in checks if item.get("passed") is True}
    passed_benchmarks = {
        item.get("benchmark_id") for item in benchmarks if item.get("passed") is True
    }
    if not required_checks.issubset(passed_checks):
        raise IntegrityError("adaptive evaluation omits a passing required check")
    if not required_benchmarks.issubset(passed_benchmarks):
        raise IntegrityError("adaptive evaluation omits a passing required benchmark")
    return cast(dict[str, Any], evaluation)


def integrate_winner(
    run_dir: Path,
    source: Path,
    branch: str,
    worktree: Path,
) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    source = source.expanduser().resolve()
    destination = worktree.expanduser().resolve()
    verify_manifest(run_dir)
    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if run_data.get("state") != "complete":
        raise IntegrityError("only a complete run with an eligible winner can be integrated")
    repository = json.loads((run_dir / "repository.json").read_text(encoding="utf-8"))
    winner = json.loads((run_dir / "winner.json").read_text(encoding="utf-8"))
    decision = json.loads((run_dir / "decision.json").read_text(encoding="utf-8"))
    if winner.get("engineer_id") != decision.get("winner"):
        raise IntegrityError("winner.json conflicts with the recorded final decision")
    scores = decision.get("scores")
    if not isinstance(scores, list):
        raise IntegrityError("decision.json scores must be an array")
    matching_scores = [
        item
        for item in scores
        if isinstance(item, dict) and item.get("engineer_id") == winner.get("engineer_id")
    ]
    if len(matching_scores) != 1 or matching_scores[0].get("eligible") is not True:
        raise IntegrityError("the recorded winner is not uniquely eligible in decision.json")
    if winner.get("base_commit") != repository.get("base_commit"):
        raise IntegrityError("winner baseline conflicts with repository evidence")
    engineer_id = winner.get("engineer_id")
    if not isinstance(engineer_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", engineer_id):
        raise IntegrityError("winner engineer ID is invalid")
    common_inputs = json.loads((run_dir / "common-inputs.json").read_text(encoding="utf-8"))
    rounds = common_inputs.get("revision_rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise IntegrityError("common-inputs.json has an invalid revision round count")
    expected_generation = f"revision-{rounds}"
    if winner.get("generation") != expected_generation:
        raise IntegrityError("winner is not bound to the final configured generation")
    candidate_path = run_dir / f"engineers/{engineer_id}/{expected_generation}/candidate.json"
    evaluation_path = run_dir / f"evaluations/{engineer_id}/{expected_generation}/evaluation.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if (
        candidate.get("engineer_id") != engineer_id
        or candidate.get("generation") != expected_generation
        or candidate.get("commit") != winner.get("commit")
        or candidate.get("tree") != winner.get("tree")
        or candidate.get("valid") is not True
    ):
        raise IntegrityError("winner does not match its final frozen candidate")
    if (
        evaluation.get("engineer_id") != engineer_id
        or evaluation.get("generation") != expected_generation
        or evaluation.get("commit") != winner.get("commit")
        or evaluation.get("valid_candidate") is not True
        or evaluation.get("provider_ok") is not True
    ):
        raise IntegrityError("winner does not match its final candidate evaluation")
    checks = evaluation.get("checks")
    benchmarks = evaluation.get("benchmarks")
    if not isinstance(checks, list) or not all(isinstance(item, dict) for item in checks):
        raise IntegrityError("winner evaluation checks are malformed")
    if not isinstance(benchmarks, list) or not all(isinstance(item, dict) for item in benchmarks):
        raise IntegrityError("winner evaluation benchmarks are malformed")
    if any(item.get("required") and not item.get("passed") for item in checks):
        raise IntegrityError("winner final evaluation contains a failed required check")
    if any(item.get("required") and not item.get("passed") for item in benchmarks):
        raise IntegrityError("winner final evaluation contains a failed required benchmark")
    winner_patch = run_dir / "winner.patch"
    if sha256_file(winner_patch) != winner.get("patch_sha256"):
        raise IntegrityError("winner.patch does not match winner.json")
    expected_patch_path = f"engineers/{engineer_id}/{expected_generation}/candidate.patch"
    if candidate.get("patch_path") != expected_patch_path:
        raise IntegrityError("final candidate records an unexpected patch path")
    candidate_patch = run_dir / expected_patch_path
    if not candidate_patch.is_file() or sha256_file(candidate_patch) != winner.get("patch_sha256"):
        raise IntegrityError("winner.patch does not match the final candidate patch")

    top = Path(_text(source, "rev-parse", "--show-toplevel")).resolve()
    recorded = Path(repository["source_path"]).resolve()
    if top != recorded:
        raise RepositoryError(
            f"source identity mismatch: run recorded {recorded}, but command resolved {top}"
        )
    status = _text(top, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RepositoryError("source repository is dirty; integration refused")
    current_head = _text(top, "rev-parse", "HEAD")
    recorded_head = repository.get("head")
    if not isinstance(recorded_head, str) or not re.fullmatch(r"[0-9a-f]{40,64}", recorded_head):
        raise IntegrityError("repository evidence has an invalid recorded source HEAD")
    if current_head != recorded_head:
        raise RepositoryError(
            f"source HEAD moved from recorded run HEAD {recorded_head} to {current_head}; "
            "re-evaluate"
        )
    baseline = str(winner["base_commit"])
    if not branch or re.search(r"[\x00\r\n]", branch):
        raise RepositoryError("integration branch name is invalid")
    if _git(top, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise RepositoryError(f"integration branch name is invalid: {branch!r}")
    if _git(top, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        raise RepositoryError(f"integration branch already exists: {branch}")
    if destination.exists():
        raise RepositoryError(f"integration worktree destination already exists: {destination}")
    if destination == top or top in destination.parents:
        raise RepositoryError("integration worktree must be outside the source checkout")

    created = False
    no_changes = winner.get("tree") == repository.get("base_tree")
    try:
        _git(top, "worktree", "add", "-b", branch, str(destination), baseline)
        created = True
        if no_changes:
            if winner_patch.stat().st_size != 0:
                raise IntegrityError("no-change winner unexpectedly has a non-empty patch")
        else:
            if winner_patch.stat().st_size == 0:
                raise IntegrityError("changed winner unexpectedly has an empty patch")
            _git(destination, "apply", "--index", "--binary", str(winner_patch))
        _git(
            destination,
            "commit",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            f"arena: integrate winner from {run_data['run_id']}",
            env=_commit_environment(),
        )
        integrated_tree = _text(destination, "rev-parse", "HEAD^{tree}")
        if integrated_tree != winner["tree"]:
            raise IntegrityError(
                "integrated tree does not match the frozen winner tree; integration rolled back"
            )
        commit = _text(destination, "rev-parse", "HEAD")
    except Exception:
        if created:
            _git(top, "worktree", "remove", "--force", str(destination), check=False)
            if destination.exists():
                shutil.rmtree(destination)
            _git(top, "branch", "-D", branch, check=False)
        raise

    receipt = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_data["run_id"],
        "source": str(top),
        "branch": branch,
        "worktree": str(destination),
        "baseline": baseline,
        "winner_engineer": winner["engineer_id"],
        "winner_tree": winner["tree"],
        "integration_commit": commit,
        "no_changes": no_changes,
        "pushed": False,
        "merged_into_original_branch": False,
    }
    receipt_path = _write_receipt(run_dir, receipt)
    return {**receipt, "receipt": str(receipt_path)}


def integrate_last_green(
    run_dir: Path,
    source: Path,
    branch: str,
    worktree: Path,
) -> dict[str, Any]:
    """Materialize an adaptive run's verified last-green tree without touching source HEAD."""

    run_dir = run_dir.expanduser().resolve()
    source = source.expanduser().resolve()
    destination = worktree.expanduser().resolve()
    verify_manifest(run_dir)
    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if run_data.get("controller") != "adaptive" or run_data.get("state") not in {
        "complete",
        "blocked",
        "deadline",
        "max_steps",
        "stopped",
    }:
        raise IntegrityError("adaptive run must be terminal with a verified last-green result")
    repository = json.loads((run_dir / "repository.json").read_text(encoding="utf-8"))
    last_green = json.loads((run_dir / "last-green.json").read_text(encoding="utf-8"))
    effective = json.loads((run_dir / "effective-config.json").read_text(encoding="utf-8"))
    acceptance = json.loads((run_dir / "acceptance.json").read_text(encoding="utf-8"))
    if effective.get("selected_mode") != run_data.get("mode"):
        raise IntegrityError("adaptive effective configuration has the wrong mode")
    contract = acceptance.get("contract")
    if (
        acceptance.get("mode") != run_data.get("mode")
        or not isinstance(contract, str)
        or sha256_bytes(contract.encode()) != acceptance.get("contract_sha256")
    ):
        raise IntegrityError("adaptive acceptance contract is invalid")
    effective_config = effective.get("config")
    if not isinstance(effective_config, dict):
        raise IntegrityError("adaptive effective configuration is malformed")
    configured_checks = effective_config.get("checks")
    configured_benchmarks = effective_config.get("benchmarks")
    if not isinstance(configured_checks, list) or not all(
        isinstance(item, dict) for item in configured_checks
    ):
        raise IntegrityError("adaptive configured checks are malformed")
    if not isinstance(configured_benchmarks, list) or not all(
        isinstance(item, dict) for item in configured_benchmarks
    ):
        raise IntegrityError("adaptive configured benchmarks are malformed")
    required_checks = {
        str(item["check_id"]) for item in configured_checks if item.get("required") is True
    }
    if run_data.get("mode") == "hackathon":
        required_checks.add("arena-demo-readiness")
    required_benchmarks = {
        str(item["benchmark_id"]) for item in configured_benchmarks if item.get("required") is True
    }
    patch = run_dir / "last-green.patch"
    if last_green.get("base_commit") != repository.get("base_commit"):
        raise IntegrityError("last-green baseline conflicts with repository evidence")
    for field in ("base_commit", "commit", "tree", "patch_sha256"):
        value = last_green.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40,64}", value):
            raise IntegrityError(f"last-green {field} is invalid")
    if sha256_file(patch) != last_green["patch_sha256"]:
        raise IntegrityError("last-green.patch does not match last-green.json")
    accepted = run_data.get("accepted_tasks")
    if not isinstance(accepted, list) or accepted != last_green.get("accepted_tasks"):
        raise IntegrityError("last-green accepted-task record conflicts with run status")
    if accepted and accepted[-1].get("commit") != last_green["commit"]:
        raise IntegrityError("last-green commit does not match the final accepted task")
    if not accepted and last_green["commit"] != repository.get("base_commit"):
        raise IntegrityError("a changed last-green commit has no accepted task evidence")
    writer = run_data.get("writer")
    if not isinstance(writer, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", writer):
        raise IntegrityError("adaptive run has no valid writer identity")
    for record in accepted:
        if not isinstance(record, dict):
            raise IntegrityError("adaptive accepted-task evidence is malformed")
        step = record.get("step")
        commit = record.get("commit")
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise IntegrityError("adaptive accepted task has an invalid step")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise IntegrityError("adaptive accepted task has an invalid commit")
        generation = f"step-{step}"
        candidate = json.loads(
            (run_dir / f"engineers/{writer}/{generation}/candidate.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            candidate.get("engineer_id") != writer
            or candidate.get("generation") != generation
            or candidate.get("commit") != commit
            or candidate.get("valid") is not True
            or candidate.get("changed_files") != record.get("changed_files")
        ):
            raise IntegrityError("accepted task conflicts with its frozen candidate")
        _verified_evaluation(
            run_dir,
            f"evaluations/{writer}/{generation}/evaluation.json",
            writer,
            generation,
            commit,
            required_checks,
            required_benchmarks,
        )
        if run_data.get("mode") == "hackathon":
            scope_review = json.loads(
                (run_dir / f"steps/step-{step}/scope-review.json").read_text(encoding="utf-8")
            )
            if (
                scope_review.get("task_id") != record.get("task_id")
                or scope_review.get("commit") != commit
                or scope_review.get("demo_beat") != record.get("demo_beat")
                or scope_review.get("verdict") != "approve"
                or not isinstance(scope_review.get("rationale"), str)
                or not scope_review["rationale"].strip()
            ):
                raise IntegrityError("accepted Hackathon task lacks an approving scope review")
    verification = last_green.get("verification")
    expected_generation = f"step-{accepted[-1]['step']}" if accepted else "last-green"
    expected_verification = f"evaluations/{writer}/{expected_generation}/evaluation.json"
    if verification != expected_verification:
        raise IntegrityError("last-green does not name its required evaluation evidence")
    _verified_evaluation(
        run_dir,
        verification,
        writer,
        expected_generation,
        last_green["commit"],
        required_checks,
        required_benchmarks,
    )

    top = Path(_text(source, "rev-parse", "--show-toplevel")).resolve()
    recorded = Path(repository["source_path"]).resolve()
    if top != recorded:
        raise RepositoryError(
            f"source identity mismatch: run recorded {recorded}, but command resolved {top}"
        )
    if _text(top, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RepositoryError("source repository is dirty; integration refused")
    current_head = _text(top, "rev-parse", "HEAD")
    if current_head != repository.get("head"):
        raise RepositoryError("source HEAD moved after the adaptive run; re-evaluate")
    if not branch or re.search(r"[\x00\r\n]", branch):
        raise RepositoryError("integration branch name is invalid")
    if _git(top, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise RepositoryError(f"integration branch name is invalid: {branch!r}")
    if _git(top, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        raise RepositoryError(f"integration branch already exists: {branch}")
    if destination.exists():
        raise RepositoryError(f"integration worktree destination already exists: {destination}")
    if destination == top or top in destination.parents:
        raise RepositoryError("integration worktree must be outside the source checkout")

    baseline = last_green["base_commit"]
    no_changes = last_green["tree"] == repository.get("base_tree")
    created = False
    try:
        _git(top, "worktree", "add", "-b", branch, str(destination), baseline)
        created = True
        if no_changes:
            if patch.stat().st_size != 0:
                raise IntegrityError("unchanged last-green unexpectedly has a non-empty patch")
        else:
            if patch.stat().st_size == 0:
                raise IntegrityError("changed last-green unexpectedly has an empty patch")
            _git(destination, "apply", "--index", "--binary", str(patch))
        _git(
            destination,
            "commit",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            f"arena: integrate last-green from {run_data['run_id']}",
            env=_commit_environment(),
        )
        if _text(destination, "rev-parse", "HEAD^{tree}") != last_green["tree"]:
            raise IntegrityError("integrated tree does not match verified last-green tree")
        commit = _text(destination, "rev-parse", "HEAD")
    except Exception:
        if created:
            _git(top, "worktree", "remove", "--force", str(destination), check=False)
            if destination.exists():
                shutil.rmtree(destination)
            _git(top, "branch", "-D", branch, check=False)
        raise

    receipt = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_data["run_id"],
        "source": str(top),
        "branch": branch,
        "worktree": str(destination),
        "baseline": baseline,
        "last_green": last_green["commit"],
        "last_green_tree": last_green["tree"],
        "integration_commit": commit,
        "no_changes": no_changes,
        "pushed": False,
        "merged_into_original_branch": False,
    }
    receipt_path = _write_receipt(run_dir, receipt)
    return {**receipt, "receipt": str(receipt_path)}
