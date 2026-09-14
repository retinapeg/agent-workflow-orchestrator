from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__version__ = "0.3.0"


class Phase(StrEnum):
    IMPLEMENT = "implement"
    REVIEW = "review"
    REVISE = "revise"
    JUDGE = "judge"


class RunMode(StrEnum):
    HACKATHON = "hackathon"
    ENGINEERING = "engineering"


class RunState(StrEnum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    SNAPSHOT = "snapshot"
    IMPLEMENT = "implement"
    FREEZE_INITIAL = "freeze_initial"
    EVALUATE_INITIAL = "evaluate_initial"
    CROSS_REVIEW = "cross_review"
    REVISE = "revise"
    FREEZE_FINAL = "freeze_final"
    EVALUATE_FINAL = "evaluate_final"
    SCORE = "score"
    OPTIONAL_JUDGE = "optional_judge"
    EXPORT_WINNER = "export_winner"
    COMPLETE = "complete"
    NO_WINNER = "no_winner"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str
    stdout_total_bytes: int
    stderr_total_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    termination: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class AgentRequest:
    engineer_id: str
    phase: Phase
    model: str | None
    prompt: str
    workspace: Path
    timeout_seconds: int
    max_output_bytes: int
    max_output_tokens: int | None
    max_cost_usd: str | None
    response_schema: dict[str, Any] | None = None


@dataclass
class AgentResult:
    engineer_id: str
    phase: Phase
    provider_kind: str
    model: str | None
    status: str
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: str | None = None
    process: ProcessResult | None = None
    operations_applied: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


@dataclass(frozen=True)
class CandidateSnapshot:
    engineer_id: str
    generation: str
    commit: str
    tree: str
    parent_commit: str
    changed_files: tuple[str, ...]
    changed_lines: int
    diff_bytes: int
    patch_path: str
    policy_violations: tuple[str, ...] = ()
    valid: bool = True
    error: str | None = None


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    category: str
    required: bool
    weight: str
    passed: bool
    command: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout_path: str
    stderr_path: str
    error: str | None = None


@dataclass(frozen=True)
class BenchmarkResult:
    benchmark_id: str
    required: bool
    weight: str
    passed: bool
    direction: str
    values: tuple[str, ...]
    aggregate: str | None
    normalized: str | None
    runs: tuple[dict[str, Any], ...]
    error: str | None = None


@dataclass
class CandidateEvaluation:
    engineer_id: str
    generation: str
    commit: str
    valid_candidate: bool
    provider_ok: bool
    policy_violations: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    benchmarks: list[BenchmarkResult] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreResult:
    engineer_id: str
    eligible: bool
    score_basis_points: int
    earned_weight: str
    total_weight: str
    disqualifications: tuple[str, ...]
    changed_lines: int


@dataclass(frozen=True)
class Decision:
    winner: str | None
    deterministic_winner: str | None
    ranking: tuple[str, ...]
    scores: tuple[ScoreResult, ...]
    judge_used: bool
    judge_candidates: tuple[str, ...]
    judge_reason: str | None
    fallback_reason: str | None


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value
