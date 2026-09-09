from __future__ import annotations

import math
import re
import tomllib
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .errors import ConfigError

ENGINEER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
KNOWN_PROVIDER_KINDS = {
    "codex_cli",
    "claude_cli",
    "openai_api",
    "anthropic_api",
    "generic_cli",
    "scripted",
}
DEFAULT_UNTRACKED_ARTIFACT_EXCLUDES = (
    "**/__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    ".coverage.*",
    ".DS_Store",
    ".venv/",
    "venv/",
    "node_modules/",
)


@dataclass(frozen=True)
class LimitsConfig:
    provider_timeout_seconds: int = 1200
    check_timeout_seconds: int = 600
    max_output_bytes: int = 1_000_000
    max_prompt_bytes: int = 1_500_000
    max_diff_bytes: int = 2_000_000
    max_changed_files: int = 250
    max_api_context_bytes: int = 750_000
    max_api_file_bytes: int = 200_000
    max_api_response_bytes: int = 2_000_000
    max_api_operations: int = 100
    max_output_tokens: int = 16_000
    termination_grace_seconds: int = 5


@dataclass(frozen=True)
class RunConfig:
    artifact_root: Path
    require_clean_source: bool = True
    keep_private_clone: bool = True
    max_workers: int = 2
    max_run_seconds: int = 7200
    prefer_smaller_diff: bool = True
    protected_paths: tuple[str, ...] = (".git", ".git/**")
    allowed_paths: tuple[str, ...] = ()
    untracked_artifact_excludes: tuple[str, ...] = DEFAULT_UNTRACKED_ARTIFACT_EXCLUDES
    trusted_overlay: Path | None = None
    trusted_overlay_destination: str = "."
    provider_env_passthrough: tuple[str, ...] = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "VIRTUAL_ENV",
        "CODEX_HOME",
    )
    check_env_passthrough: tuple[str, ...] = (
        "PATH",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "VIRTUAL_ENV",
    )


@dataclass(frozen=True)
class EngineerConfig:
    engineer_id: str
    kind: str
    model: str | None
    timeout_seconds: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckConfig:
    check_id: str
    category: str
    command: tuple[str, ...]
    required: bool
    weight: Decimal
    timeout_seconds: int | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkConfig:
    benchmark_id: str
    command: tuple[str, ...]
    metric_key: str
    direction: str
    best: Decimal
    worst: Decimal
    required: bool
    weight: Decimal
    repetitions: int = 3
    warmups: int = 1
    gate: Decimal | None = None
    timeout_seconds: int | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeConfig:
    enabled: bool = False
    engineer: str | None = None
    score_band_basis_points: int = 0
    timeout_seconds: int = 300


@dataclass(frozen=True)
class ModeConfig:
    revision_rounds: int
    check_weights: dict[str, Decimal] = field(default_factory=dict)
    benchmark_weights: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class ArenaConfig:
    path: Path
    run: RunConfig
    limits: LimitsConfig
    engineers: tuple[EngineerConfig, ...]
    checks: tuple[CheckConfig, ...]
    benchmarks: tuple[BenchmarkConfig, ...]
    judge: JudgeConfig
    modes: dict[str, ModeConfig]
    prompt_dir: Path | None = None

    def engineer(self, engineer_id: str) -> EngineerConfig:
        for item in self.engineers:
            if item.engineer_id == engineer_id:
                return item
        raise ConfigError(f"unknown engineer: {engineer_id}")

    def audit_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        data["run"]["artifact_root"] = str(self.run.artifact_root)
        overlay = self.run.trusted_overlay
        data["run"]["trusted_overlay"] = str(overlay) if overlay else None
        data["prompt_dir"] = str(self.prompt_dir) if self.prompt_dir else None
        for check in data["checks"]:
            check["weight"] = str(check["weight"])
            check["env"] = _redact_env(check["env"])
        for benchmark in data["benchmarks"]:
            for key in ("best", "worst", "weight", "gate"):
                if benchmark[key] is not None:
                    benchmark[key] = str(benchmark[key])
            benchmark["env"] = _redact_env(benchmark["env"])
        for engineer in data["engineers"]:
            engineer["options"] = _redact_mapping(engineer["options"])
        for mode in data["modes"].values():
            mode["check_weights"] = {
                key: str(value) for key, value in mode["check_weights"].items()
            }
            mode["benchmark_weights"] = {
                key: str(value) for key, value in mode["benchmark_weights"].items()
            }
        return data

    def mode(self, name: str) -> ModeConfig:
        try:
            return self.modes[name]
        except KeyError as exc:
            raise ConfigError("mode must be exactly 'hackathon' or 'engineering'") from exc


def with_model_overrides(
    config: ArenaConfig,
    *,
    codex_model: str | None = None,
    claude_model: str | None = None,
) -> ArenaConfig:
    """Apply invocation-only models to one unambiguous provider of each family."""
    replacements: dict[str, str] = {}
    for family, model, kinds in (
        ("codex", codex_model, {"codex_cli", "openai_api"}),
        ("claude", claude_model, {"claude_cli", "anthropic_api"}),
    ):
        if model is None:
            continue
        if not model.strip() or any(character.isspace() for character in model) or "\x00" in model:
            raise ConfigError(f"--{family}-model must be a nonempty model ID without whitespace")
        matches = [engineer for engineer in config.engineers if engineer.kind in kinds]
        if not matches:
            raise ConfigError(
                f"--{family}-model requires one configured provider of kind "
                + " or ".join(sorted(kinds))
            )
        if len(matches) != 1:
            names = ", ".join(engineer.engineer_id for engineer in matches)
            raise ConfigError(
                f"--{family}-model is ambiguous across engineers: {names}; "
                "set their models individually in the configuration"
            )
        replacements[matches[0].engineer_id] = model
    if not replacements:
        return config
    return replace(
        config,
        engineers=tuple(
            replace(engineer, model=replacements[engineer.engineer_id])
            if engineer.engineer_id in replacements
            else engineer
            for engineer in config.engineers
        ),
    )


def _redact_env(env: dict[str, str]) -> dict[str, str]:
    return {key: "<redacted>" for key in env}


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        lowered = key.lower()
        if any(marker in lowered for marker in ("key", "token", "secret", "password", "auth")):
            result[key] = "<redacted>"
        elif isinstance(value, dict):
            result[key] = _redact_mapping(value)
        else:
            result[key] = value
    return result


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{name} contains unknown keys: {sorted(unknown)}")


def _positive_int(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def _decimal(value: Any, name: str, *, nonnegative: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{name} must be a decimal number") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        qualifier = "finite and non-negative" if nonnegative else "finite"
        raise ConfigError(f"{name} must be {qualifier}")
    return result


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and "\x00" not in item for item in value
    ):
        raise ConfigError(f"{name} must be an array of strings")
    return tuple(value)


def _gitignore_patterns(value: Any, name: str) -> tuple[str, ...]:
    patterns = _strings(value, name)
    if any(not item or "\r" in item or "\n" in item for item in patterns):
        raise ConfigError(f"{name} must contain nonempty, single-line Git ignore patterns")
    return patterns


def _command(value: Any, name: str) -> tuple[str, ...]:
    result = _strings(value, name)
    if not result or not result[0].strip():
        raise ConfigError(f"{name} must contain an executable and optional arguments")
    return result


def _env(value: Any, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) and "\x00" not in key + item
        for key, item in value.items()
    ):
        raise ConfigError(f"{name} must be a string-to-string table")
    return dict(value)


def _weight_overrides(value: Any, name: str) -> dict[str, Decimal]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table of evaluation id to decimal weight")
    result: dict[str, Decimal] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not ENGINEER_ID.fullmatch(key):
            raise ConfigError(f"{name} contains an invalid evaluation id: {key!r}")
        result[key] = _decimal(item, f"{name}.{key}")
    return result


def _resolve_optional_path(value: Any, base: Path, name: str) -> Path | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a path string")
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_config(path: str | Path) -> ArenaConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    _reject_unknown(
        raw,
        {
            "schema_version",
            "run",
            "limits",
            "engineers",
            "checks",
            "benchmarks",
            "judge",
            "modes",
            "prompt_dir",
        },
        "configuration",
    )
    if raw.get("schema_version", 1) != 1:
        raise ConfigError("only schema_version = 1 is supported")
    base = config_path.parent
    run_raw = _table(raw, "run")
    limits_raw = _table(raw, "limits")
    engineers_raw = _table(raw, "engineers")
    judge_raw = _table(raw, "judge")
    _reject_unknown(run_raw, set(RunConfig.__dataclass_fields__), "[run]")
    _reject_unknown(limits_raw, set(LimitsConfig.__dataclass_fields__), "[limits]")
    _reject_unknown(judge_raw, set(JudgeConfig.__dataclass_fields__), "[judge]")

    artifact_value = run_raw.get("artifact_root", "../.agent-arena-runs")
    if not isinstance(artifact_value, str):
        raise ConfigError("run.artifact_root must be a path string")
    artifact_path = Path(artifact_value).expanduser()
    if not artifact_path.is_absolute():
        artifact_path = (base / artifact_path).resolve()
    overlay_destination = run_raw.get("trusted_overlay_destination", ".")
    if not isinstance(overlay_destination, str):
        raise ConfigError("run.trusted_overlay_destination must be a path string")

    run = RunConfig(
        artifact_root=artifact_path,
        require_clean_source=_boolean(
            run_raw.get("require_clean_source", True), "run.require_clean_source"
        ),
        keep_private_clone=_boolean(
            run_raw.get("keep_private_clone", True), "run.keep_private_clone"
        ),
        max_workers=_positive_int(run_raw.get("max_workers", 2), "run.max_workers"),
        max_run_seconds=_positive_int(run_raw.get("max_run_seconds", 7200), "run.max_run_seconds"),
        prefer_smaller_diff=_boolean(
            run_raw.get("prefer_smaller_diff", True), "run.prefer_smaller_diff"
        ),
        protected_paths=_strings(
            run_raw.get("protected_paths", [".git", ".git/**"]),
            "run.protected_paths",
        ),
        allowed_paths=_strings(run_raw.get("allowed_paths", []), "run.allowed_paths"),
        untracked_artifact_excludes=_gitignore_patterns(
            run_raw.get(
                "untracked_artifact_excludes",
                list(DEFAULT_UNTRACKED_ARTIFACT_EXCLUDES),
            ),
            "run.untracked_artifact_excludes",
        ),
        trusted_overlay=_resolve_optional_path(
            run_raw.get("trusted_overlay"), base, "run.trusted_overlay"
        ),
        trusted_overlay_destination=overlay_destination,
        provider_env_passthrough=_strings(
            run_raw.get(
                "provider_env_passthrough",
                [
                    "PATH",
                    "HOME",
                    "USER",
                    "LOGNAME",
                    "LANG",
                    "LC_ALL",
                    "TMPDIR",
                    "VIRTUAL_ENV",
                    "CODEX_HOME",
                ],
            ),
            "run.provider_env_passthrough",
        ),
        check_env_passthrough=_strings(
            run_raw.get(
                "check_env_passthrough",
                ["PATH", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV"],
            ),
            "run.check_env_passthrough",
        ),
    )
    if run.trusted_overlay and not run.trusted_overlay.is_dir():
        raise ConfigError(f"trusted overlay is not a directory: {run.trusted_overlay}")
    destination = Path(run.trusted_overlay_destination)
    if destination.is_absolute() or ".." in destination.parts:
        raise ConfigError("run.trusted_overlay_destination must stay inside validation checkouts")

    limit_defaults = LimitsConfig()
    limit_values: dict[str, int] = {}
    for field_name in limit_defaults.__dataclass_fields__:
        default = getattr(limit_defaults, field_name)
        limit_values[field_name] = _positive_int(
            limits_raw.get(field_name, default), f"limits.{field_name}"
        )
    limits = LimitsConfig(**limit_values)

    if len(engineers_raw) < 2:
        raise ConfigError("configure at least two [engineers.<id>] entries")
    engineers: list[EngineerConfig] = []
    for engineer_id, item in engineers_raw.items():
        if not ENGINEER_ID.fullmatch(engineer_id):
            raise ConfigError(
                f"invalid engineer id {engineer_id!r}; use lowercase letters, digits, '_' or '-'"
            )
        if not isinstance(item, dict):
            raise ConfigError(f"[engineers.{engineer_id}] must be a table")
        _reject_unknown(
            item,
            {"kind", "model", "timeout_seconds", "max_output_tokens", "max_cost_usd", "options"},
            f"[engineers.{engineer_id}]",
        )
        kind = item.get("kind")
        if kind not in KNOWN_PROVIDER_KINDS:
            raise ConfigError(
                f"engineers.{engineer_id}.kind must be one of {sorted(KNOWN_PROVIDER_KINDS)}"
            )
        model = item.get("model")
        if model is not None and not isinstance(model, str):
            raise ConfigError(f"engineers.{engineer_id}.model must be a string")
        if kind in {"openai_api", "anthropic_api"} and not model:
            raise ConfigError(f"engineers.{engineer_id}.model is required for API providers")
        timeout = item.get("timeout_seconds")
        max_tokens = item.get("max_output_tokens")
        max_cost = item.get("max_cost_usd")
        if max_cost is not None:
            max_cost = str(_decimal(max_cost, f"engineers.{engineer_id}.max_cost_usd"))
        options = item.get("options", {})
        if not isinstance(options, dict):
            raise ConfigError(f"engineers.{engineer_id}.options must be a table")
        engineers.append(
            EngineerConfig(
                engineer_id=engineer_id,
                kind=kind,
                model=model,
                timeout_seconds=(
                    _positive_int(timeout, f"engineers.{engineer_id}.timeout_seconds")
                    if timeout is not None
                    else None
                ),
                max_output_tokens=(
                    _positive_int(max_tokens, f"engineers.{engineer_id}.max_output_tokens")
                    if max_tokens is not None
                    else None
                ),
                max_cost_usd=max_cost,
                options=dict(options),
            )
        )

    check_items = raw.get("checks", [])
    if not isinstance(check_items, list) or not check_items:
        raise ConfigError("configure at least one [[checks]] entry")
    checks: list[CheckConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(check_items):
        if not isinstance(item, dict):
            raise ConfigError(f"checks[{index}] must be a table")
        _reject_unknown(
            item,
            {"id", "category", "command", "required", "weight", "timeout_seconds", "env"},
            f"checks[{index}]",
        )
        check_id = item.get("id")
        if not isinstance(check_id, str) or not ENGINEER_ID.fullmatch(check_id):
            raise ConfigError(f"checks[{index}].id must be a safe lowercase identifier")
        if check_id in seen_ids:
            raise ConfigError(f"duplicate evaluation id: {check_id}")
        seen_ids.add(check_id)
        category = item.get("category", "test")
        if category not in {"test", "lint", "typecheck", "build", "security", "other"}:
            raise ConfigError(f"checks[{index}].category is invalid")
        timeout = item.get("timeout_seconds")
        checks.append(
            CheckConfig(
                check_id=check_id,
                category=category,
                command=_command(item.get("command"), f"checks[{index}].command"),
                required=_boolean(item.get("required", True), f"checks[{index}].required"),
                weight=_decimal(item.get("weight", "1"), f"checks[{index}].weight"),
                timeout_seconds=(
                    _positive_int(timeout, f"checks[{index}].timeout_seconds")
                    if timeout is not None
                    else None
                ),
                env=_env(item.get("env"), f"checks[{index}].env"),
            )
        )

    benchmark_items = raw.get("benchmarks", [])
    if not isinstance(benchmark_items, list):
        raise ConfigError("[[benchmarks]] must be an array of tables")
    benchmarks: list[BenchmarkConfig] = []
    for index, item in enumerate(benchmark_items):
        if not isinstance(item, dict):
            raise ConfigError(f"benchmarks[{index}] must be a table")
        _reject_unknown(
            item,
            {
                "id",
                "command",
                "metric_key",
                "direction",
                "best",
                "worst",
                "required",
                "weight",
                "repetitions",
                "warmups",
                "gate",
                "timeout_seconds",
                "env",
            },
            f"benchmarks[{index}]",
        )
        benchmark_id = item.get("id")
        if not isinstance(benchmark_id, str) or not ENGINEER_ID.fullmatch(benchmark_id):
            raise ConfigError(f"benchmarks[{index}].id must be a safe lowercase identifier")
        if benchmark_id in seen_ids:
            raise ConfigError(f"duplicate evaluation id: {benchmark_id}")
        seen_ids.add(benchmark_id)
        direction = item.get("direction")
        if direction not in {"higher", "lower"}:
            raise ConfigError(f"benchmarks[{index}].direction must be 'higher' or 'lower'")
        best = _decimal(item.get("best"), f"benchmarks[{index}].best", nonnegative=False)
        worst = _decimal(item.get("worst"), f"benchmarks[{index}].worst", nonnegative=False)
        if best == worst:
            raise ConfigError(f"benchmarks[{index}] best and worst must differ")
        if direction == "higher" and best < worst:
            raise ConfigError(f"benchmarks[{index}] higher-is-better requires best > worst")
        if direction == "lower" and best > worst:
            raise ConfigError(f"benchmarks[{index}] lower-is-better requires best < worst")
        timeout = item.get("timeout_seconds")
        gate_raw = item.get("gate")
        metric_key = item.get("metric_key", benchmark_id)
        if not isinstance(metric_key, str) or not metric_key or "\x00" in metric_key:
            raise ConfigError(f"benchmarks[{index}].metric_key must be a nonempty string")
        benchmarks.append(
            BenchmarkConfig(
                benchmark_id=benchmark_id,
                command=_command(item.get("command"), f"benchmarks[{index}].command"),
                metric_key=metric_key,
                direction=direction,
                best=best,
                worst=worst,
                required=_boolean(item.get("required", False), f"benchmarks[{index}].required"),
                weight=_decimal(item.get("weight", "1"), f"benchmarks[{index}].weight"),
                repetitions=_positive_int(
                    item.get("repetitions", 3), f"benchmarks[{index}].repetitions"
                ),
                warmups=_positive_int(
                    item.get("warmups", 1), f"benchmarks[{index}].warmups", minimum=0
                ),
                gate=(
                    _decimal(gate_raw, f"benchmarks[{index}].gate", nonnegative=False)
                    if gate_raw is not None
                    else None
                ),
                timeout_seconds=(
                    _positive_int(timeout, f"benchmarks[{index}].timeout_seconds")
                    if timeout is not None
                    else None
                ),
                env=_env(item.get("env"), f"benchmarks[{index}].env"),
            )
        )

    total_weight = sum((item.weight for item in checks), Decimal(0)) + sum(
        (item.weight for item in benchmarks), Decimal(0)
    )
    if not total_weight.is_finite() or math.isclose(float(total_weight), 0.0):
        raise ConfigError("the total evaluation weight must be greater than zero")

    judge_engineer = judge_raw.get("engineer")
    if judge_engineer is not None and (
        not isinstance(judge_engineer, str) or not ENGINEER_ID.fullmatch(judge_engineer)
    ):
        raise ConfigError("judge.engineer must be a safe lowercase engineer identifier")
    judge = JudgeConfig(
        enabled=_boolean(judge_raw.get("enabled", False), "judge.enabled"),
        engineer=judge_engineer,
        score_band_basis_points=_positive_int(
            judge_raw.get("score_band_basis_points", 0),
            "judge.score_band_basis_points",
            minimum=0,
        ),
        timeout_seconds=_positive_int(
            judge_raw.get("timeout_seconds", 300), "judge.timeout_seconds"
        ),
    )
    engineer_ids = {item.engineer_id for item in engineers}
    if judge.enabled and judge.engineer not in engineer_ids:
        raise ConfigError("judge.engineer must name a configured engineer when judge is enabled")

    modes_raw = _table(raw, "modes")
    unknown_modes = set(modes_raw) - {"hackathon", "engineering"}
    if unknown_modes:
        raise ConfigError(
            f"only hackathon and engineering modes are supported: {sorted(unknown_modes)}"
        )
    default_rounds = {"hackathon": 1, "engineering": 2}
    modes: dict[str, ModeConfig] = {}
    for mode_name in ("hackathon", "engineering"):
        item = modes_raw.get(mode_name, {})
        if not isinstance(item, dict):
            raise ConfigError(f"[modes.{mode_name}] must be a table")
        _reject_unknown(
            item,
            {"revision_rounds", "check_weights", "benchmark_weights"},
            f"[modes.{mode_name}]",
        )
        rounds = _positive_int(
            item.get("revision_rounds", default_rounds[mode_name]),
            f"modes.{mode_name}.revision_rounds",
        )
        maximum = 2 if mode_name == "hackathon" else 4
        minimum = 1 if mode_name == "hackathon" else 2
        if not minimum <= rounds <= maximum:
            raise ConfigError(
                f"modes.{mode_name}.revision_rounds must be between {minimum} and {maximum}"
            )
        check_weights = _weight_overrides(
            item.get("check_weights"), f"modes.{mode_name}.check_weights"
        )
        benchmark_weights = _weight_overrides(
            item.get("benchmark_weights"), f"modes.{mode_name}.benchmark_weights"
        )
        unknown_ids = (set(check_weights) | set(benchmark_weights)) - seen_ids
        if unknown_ids:
            raise ConfigError(
                f"modes.{mode_name} has weights for unknown evaluations: {sorted(unknown_ids)}"
            )
        overlap = set(check_weights) & set(benchmark_weights)
        if overlap:
            overlap_text = sorted(overlap)
            raise ConfigError(
                f"modes.{mode_name} repeats ids across check and benchmark weights: {overlap_text}"
            )
        modes[mode_name] = ModeConfig(
            revision_rounds=rounds,
            check_weights=check_weights,
            benchmark_weights=benchmark_weights,
        )
        effective_total = sum(
            (check_weights.get(item.check_id, item.weight) for item in checks), Decimal(0)
        ) + sum(
            (benchmark_weights.get(item.benchmark_id, item.weight) for item in benchmarks),
            Decimal(0),
        )
        if effective_total <= 0:
            raise ConfigError(f"modes.{mode_name} effective evaluation weight must exceed zero")

    prompt_dir = _resolve_optional_path(raw.get("prompt_dir"), base, "prompt_dir")
    if prompt_dir and not prompt_dir.is_dir():
        raise ConfigError(f"prompt_dir is not a directory: {prompt_dir}")

    return ArenaConfig(
        path=config_path,
        run=run,
        limits=limits,
        engineers=tuple(engineers),
        checks=tuple(checks),
        benchmarks=tuple(benchmarks),
        judge=judge,
        modes=modes,
        prompt_dir=prompt_dir,
    )
