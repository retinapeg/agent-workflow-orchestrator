from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit import rebuild_manifest, sha256_file, verify_manifest
from .errors import IntegrityError, RepositoryError


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=180,
        check=False,
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
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "Agent Arena",
                "GIT_AUTHOR_EMAIL": "agent-arena@localhost",
                "GIT_COMMITTER_NAME": "Agent Arena",
                "GIT_COMMITTER_EMAIL": "agent-arena@localhost",
            }
        )
        result = subprocess.run(
            [
                "git",
                "-C",
                str(destination),
                "commit",
                "--allow-empty",
                "--no-gpg-sign",
                "-m",
                f"arena: integrate winner from {run_data['run_id']}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=180,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise RepositoryError(
                "integration commit failed: "
                + result.stderr.decode("utf-8", errors="replace")[:2000]
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
