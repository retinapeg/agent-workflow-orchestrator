import json
from pathlib import Path

from agent_arena.acquisition.evaluation import evaluate
from agent_arena.audit import verify_manifest


def test_frozen_evaluation_reproduces_non_temporal_results(tmp_path):
    output = tmp_path / "report"
    result = evaluate(output)
    reference = json.loads(
        (
            Path(__file__).resolve().parents[2] / "docs/acquisition/evaluation/results.json"
        ).read_text()
    )
    assert result["fixture_sha256"] == reference["fixture_sha256"]
    assert len(result["scenarios"]) == 12
    for actual, expected in zip(result["scenarios"], reference["scenarios"], strict=True):
        assert {k: v for k, v in actual.items() if k != "latency_ms"} == {
            k: v for k, v in expected.items() if k != "latency_ms"
        }
        assert actual["negative_control_rejected"]
        assert not actual["policy_violations"]
        assert actual["expected_terminal_state_met"]
        assert actual["model_calls"] == 0
    assert sum(r["valid_completion"] for r in result["scenarios"]) == 8
    assert verify_manifest(output)["run_id"] == "acquisition-scripted-v1"
