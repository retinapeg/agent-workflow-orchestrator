from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .config import ArenaConfig
from .models import CandidateEvaluation, CandidateSnapshot, Decision, ScoreResult


def score_candidates(
    config: ArenaConfig,
    mode: str,
    snapshots: dict[str, CandidateSnapshot],
    evaluations: dict[str, CandidateEvaluation],
) -> tuple[ScoreResult, ...]:
    profile = config.mode(mode)

    def check_weight(check_id: str, default: Decimal) -> Decimal:
        return profile.check_weights.get(check_id, default)

    def benchmark_weight(benchmark_id: str, default: Decimal) -> Decimal:
        return profile.benchmark_weights.get(benchmark_id, default)

    total_weight = sum(
        (check_weight(item.check_id, item.weight) for item in config.checks), Decimal(0)
    ) + sum(
        (benchmark_weight(item.benchmark_id, item.weight) for item in config.benchmarks),
        Decimal(0),
    )
    results: list[ScoreResult] = []
    for engineer_id in sorted(snapshots):
        snapshot = snapshots[engineer_id]
        evaluation = evaluations[engineer_id]
        disqualifications: list[str] = []
        if not snapshot.valid or not evaluation.valid_candidate:
            disqualifications.append("candidate snapshot is invalid")
        if not evaluation.provider_ok:
            disqualifications.append("provider phase did not complete successfully")
        disqualifications.extend(evaluation.policy_violations)

        checks_by_id = {item.check_id: item for item in evaluation.checks}
        benchmarks_by_id = {item.benchmark_id: item for item in evaluation.benchmarks}
        earned = Decimal(0)
        for check_config in config.checks:
            result = checks_by_id.get(check_config.check_id)
            if result and result.passed:
                earned += check_weight(check_config.check_id, check_config.weight)
            elif check_config.required:
                reason = "missing" if result is None else "failed"
                disqualifications.append(f"required check {check_config.check_id} {reason}")
        for benchmark_config in config.benchmarks:
            benchmark_result = benchmarks_by_id.get(benchmark_config.benchmark_id)
            if (
                benchmark_result
                and benchmark_result.passed
                and benchmark_result.normalized is not None
            ):
                earned += benchmark_weight(
                    benchmark_config.benchmark_id, benchmark_config.weight
                ) * Decimal(benchmark_result.normalized)
            elif benchmark_config.required:
                reason = "missing" if benchmark_result is None else "failed"
                disqualifications.append(
                    f"required benchmark {benchmark_config.benchmark_id} {reason}"
                )
        configured_check_ids = {item.check_id for item in config.checks}
        for result in evaluation.checks:
            if (
                result.required
                and not result.passed
                and result.check_id not in configured_check_ids
            ):
                disqualifications.append(f"required internal check {result.check_id} failed")
        basis_points = int(
            ((earned / total_weight) * Decimal(10_000)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        results.append(
            ScoreResult(
                engineer_id=engineer_id,
                eligible=not disqualifications,
                score_basis_points=basis_points,
                earned_weight=str(earned),
                total_weight=str(total_weight),
                disqualifications=tuple(sorted(set(disqualifications))),
                changed_lines=snapshot.changed_lines,
            )
        )

    def order_key(item: ScoreResult) -> tuple[int, int, int, str]:
        diff_key = item.changed_lines if config.run.prefer_smaller_diff else 0
        return (0 if item.eligible else 1, -item.score_basis_points, diff_key, item.engineer_id)

    return tuple(sorted(results, key=order_key))


def deterministic_decision(scores: tuple[ScoreResult, ...]) -> Decision:
    eligible = [item for item in scores if item.eligible]
    winner = eligible[0].engineer_id if eligible else None
    return Decision(
        winner=winner,
        deterministic_winner=winner,
        ranking=tuple(item.engineer_id for item in scores),
        scores=scores,
        judge_used=False,
        judge_candidates=(),
        judge_reason=None,
        fallback_reason=None,
    )
