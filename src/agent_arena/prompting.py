from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any

from .config import ArenaConfig

PLACEHOLDER = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
ANY_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "note"],
                    },
                    "path": {"type": ["string", "null"]},
                    "line": {"type": ["integer", "null"]},
                    "reproduction": {"type": "string"},
                    "violated_requirement": {"type": "string"},
                    "explanation": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "severity",
                    "path",
                    "line",
                    "reproduction",
                    "violated_requirement",
                    "explanation",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "attack_tests": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["target", "summary", "findings", "attack_tests"],
    "additionalProperties": False,
}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "winner": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["winner", "reason", "confidence"],
    "additionalProperties": False,
}


class PromptBook:
    def __init__(self, config: ArenaConfig) -> None:
        self.config = config

    def template(self, name: str) -> str:
        filename = f"{name}.md"
        if self.config.prompt_dir:
            return (self.config.prompt_dir / filename).read_text(encoding="utf-8")
        return files("agent_arena.prompts").joinpath(filename).read_text(encoding="utf-8")

    def render(self, name: str, **values: str) -> str:
        content = self.template(name)
        declared = set(PLACEHOLDER.findall(content))
        malformed = [
            match.group(1)
            for match in ANY_PLACEHOLDER.finditer(content)
            if not PLACEHOLDER.fullmatch(match.group(0))
        ]
        if malformed:
            raise ValueError(f"invalid prompt variables in {name}: {sorted(set(malformed))}")
        missing = sorted(declared - values.keys())
        if missing:
            raise ValueError(f"unresolved prompt variables in {name}: {missing}")
        # One pass over the original template keeps task text, diffs, and source
        # snippets opaque even when those values contain {{...}} syntax.
        return PLACEHOLDER.sub(lambda match: values[match.group(1)], content)


MODE_GUIDANCE = {
    "hackathon": """MODE: HACKATHON
Primary objective: maximize the probability of a convincing, reliable live demo quickly.
Priority: end-to-end demo, judging criteria, demo reliability, visible UX, critical correctness,
useful tests, then architecture. Cut scope aggressively. Prefer a one-command start, obvious UX,
graceful fallbacks, and deterministic validation of important AI outputs. Major architecture work is
justified only when it fixes a critical demo blocker. Ask continually: if judging started in 30
minutes, what would prevent a convincing demonstration? Maintain DEMO_CHECKLIST.md with startup,
demo flow/data, expected outputs, environment variables, fallback mode, and known limitations.""",
    "engineering": """MODE: ENGINEERING
Primary objective: build the strongest correct, maintainable, production-quality implementation.
Priority: correctness, completeness, reliability, security, maintainability, architecture, testing,
performance, UX, and documentation. Do not accept success only on demo data. Seek evidence across
unit, integration, regression, edge/property, type, lint, security, concurrency, failure-mode,
dependency/API, performance, and architecture concerns where relevant. Refactor only when it
materially improves those outcomes.""",
}


def acceptance_contract(config: ArenaConfig, mode: str) -> str:
    profile = config.mode(mode)
    lines = [
        MODE_GUIDANCE[mode],
        f"Configured adversarial revision rounds: {profile.revision_rounds}.",
        "Every required check must pass. Optional checks and benchmarks affect the deterministic "
        "score.",
        "Commands are run by the coordinator as argv arrays without a shell in fresh checkouts.",
        "Configured checks:",
    ]
    for check in config.checks:
        effective_weight = profile.check_weights.get(check.check_id, check.weight)
        lines.append(
            f"- {check.check_id} ({check.category}, required={str(check.required).lower()}, "
            f"mode_weight={effective_weight}): {json.dumps(list(check.command))}"
        )
    if config.benchmarks:
        lines.append("Configured benchmarks:")
    for benchmark in config.benchmarks:
        effective_weight = profile.benchmark_weights.get(benchmark.benchmark_id, benchmark.weight)
        gate = f", gate={benchmark.gate}" if benchmark.gate is not None else ""
        lines.append(
            f"- {benchmark.benchmark_id} (required={str(benchmark.required).lower()}, "
            f"mode_weight={effective_weight}, metric={benchmark.metric_key}, "
            f"direction={benchmark.direction}, best={benchmark.best}, worst={benchmark.worst}"
            f"{gate}, warmups={benchmark.warmups}, repetitions={benchmark.repetitions}): "
            f"{json.dumps(list(benchmark.command))}"
        )
    lines.append(
        "Benchmark commands must emit a line beginning `ARENA_METRIC ` followed by a JSON object "
        "containing the configured metric key."
    )
    lines.append(f"Protected path globs: {json.dumps(list(config.run.protected_paths))}")
    lines.append(
        "Allowed path globs: "
        + (
            json.dumps(list(config.run.allowed_paths))
            if config.run.allowed_paths
            else "unrestricted"
        )
    )
    return "\n".join(lines)
