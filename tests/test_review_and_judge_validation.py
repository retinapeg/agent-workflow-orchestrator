from __future__ import annotations

import json

from agent_arena.models import CandidateEvaluation, CheckResult
from agent_arena.orchestrator import ArenaOrchestrator, _review_is_valid


def test_malformed_review_severity_is_rejected_without_an_exception() -> None:
    payload = {
        "target": "claude",
        "summary": "malformed",
        "findings": [
            {
                "severity": [],
                "path": None,
                "line": None,
                "reproduction": "none",
                "violated_requirement": "none",
                "explanation": "none",
                "confidence": 0.5,
            }
        ],
        "attack_tests": [],
    }
    valid, error = _review_is_valid(json.dumps(payload), "claude")
    assert not valid
    assert error == "finding severity is invalid"


def test_blinded_judge_evidence_omits_raw_identity_bearing_errors() -> None:
    evaluation = CandidateEvaluation(
        engineer_id="codex",
        generation="revision-2",
        commit="deadbeef",
        valid_candidate=True,
        provider_ok=True,
        checks=[
            CheckResult(
                check_id="optional-lint",
                category="lint",
                required=False,
                weight="1",
                passed=False,
                command=("lint",),
                exit_code=None,
                timed_out=False,
                duration_seconds=0.1,
                stdout_path="private/codex/stdout",
                stderr_path="private/codex/stderr",
                error="EvaluationError: /private/check-codex-revision-2 escaped",
            )
        ],
    )
    serialized = json.dumps(ArenaOrchestrator._blinded_evaluation(evaluation), sort_keys=True)
    assert "codex" not in serialized
    assert "/private" not in serialized
    assert "deadbeef" not in serialized
    assert '"error_present": true' in serialized
