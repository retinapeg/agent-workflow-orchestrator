"""Token pricing: dollars derived from raw token counts and a dated price table.

This file is kept identical in agent-workflow-orchestrator (``agent_arena/pricing.py``) and
agent_reliability_lab (``agent_reliability/pricing.py``). Stdlib only.

Rules:
- raw tokens per category per model are the ground truth; a cost is always derived from them
  and the price table, so old runs can be re-priced;
- every cost is API-equivalent (subscription use has no marginal dollar cost);
- a missing price or a missing token count is never $0: the call is unpriced (``None``) and
  the reason is named; totals carry a coverage figure (priced calls / total calls);
- prices come only from the table; nothing is guessed.

A call is a dict with ``model``, ``input``, ``cache_read``, ``cache_write``, ``output``,
``reasoning`` (ints or ``None``), ``input_includes_cache_read`` (bool) and optionally
``cache_write_5m``/``cache_write_1h``, ``service_tier``, ``not_applicable``, ``call`` and
``single_request``. Long-context rates apply per request, so they are applied only to a record
with ``single_request`` true. A record that aggregates several requests (a Codex turn, a
whole CLI run) and exceeds the threshold is priced at base rates and marked
``long_context = "unknown"``: the surcharge is never guessed.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

LABEL = "API-equivalent (subscription: no marginal $)"
ENV_VAR = "AGENT_PRICING_FILE"
DEFAULT_PATH = Path("~/.agent-arena/pricing.toml")
_MTOK = Decimal(1_000_000)


def default_pricing_path() -> Path:
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PATH).expanduser()


@dataclass(frozen=True)
class PriceTable:
    models: dict[str, dict[str, Any]]
    version: str | None = None
    sha256: str | None = None
    path: str | None = None
    warnings: tuple[str, ...] = ()

    def stamp(self) -> dict[str, str | None]:
        """What to record next to every computed cost."""

        return {"file": self.path, "version": self.version, "sha256": self.sha256}


@dataclass(frozen=True)
class CallCost:
    cost: Decimal | None
    missing: str | None = None  # why the call is unpriced
    notes: tuple[str, ...] = ()  # assumptions made while pricing it
    long_context: str = "no"  # "no", "applied" or "unknown" (aggregate record over the limit)


def load_price_table(path: Path | None) -> PriceTable:
    if path is None or not path.is_file():
        return PriceTable({}, path=None if path is None else str(path), warnings=(
            f"pricing file not found ({path}); every call is unpriced",))  # fmt: skip
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return PriceTable({}, sha256=digest, path=str(path), warnings=(
            f"pricing file is invalid ({exc}); every call is unpriced",))  # fmt: skip
    warnings: list[str] = []
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        version = None
        warnings.append("pricing file has no version")
    models: dict[str, dict[str, Any]] = {}
    entries = raw.get("models", {})
    for name, entry in entries.items() if isinstance(entries, dict) else []:
        if not isinstance(entry, dict):
            continue
        if not entry.get("source") or not entry.get("retrieved"):
            warnings.append(f"pricing for {name} lacks source/retrieved; treated as unpriced")
            continue
        models[str(name)] = entry
        for alias in entry.get("aliases", []) or []:
            models[str(alias)] = entry
    return PriceTable(models, version, digest, str(path), tuple(warnings))


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() and number >= 0 else None


def _count(call: dict[str, Any], key: str) -> int | None:
    value = call.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def price_call(
    call: dict[str, Any], table: PriceTable, service_tier: str | None = None
) -> CallCost:
    """Price one call. ``service_tier`` is the run's tier, used when the call names none."""

    label = f"call {call.get('call', '?')}"
    model = call.get("model")
    entry = table.models.get(model) if isinstance(model, str) else None
    if entry is None:
        return CallCost(None, f"{label}: model {model!r} not in the pricing file")
    skip = set(call.get("not_applicable") or [])
    notes: list[str] = []

    counts: dict[str, int] = {}
    for category in ("input", "cache_read", "cache_write", "output"):
        if category in skip:
            counts[category] = 0
            continue
        tokens = _count(call, category)
        if tokens is None:
            return CallCost(None, f"{label}: {category} tokens not reported")
        counts[category] = tokens
    if call.get("input_includes_cache_read"):
        prompt_tokens = counts["input"]
        fresh = counts["input"] - counts["cache_read"] - counts["cache_write"]
        if fresh < 0:
            return CallCost(None, f"{label}: cached tokens exceed input tokens")
    else:
        fresh = counts["input"]
        prompt_tokens = counts["input"] + counts["cache_read"] + counts["cache_write"]

    tier = call.get("service_tier") or service_tier or "standard"
    tier_factor = Decimal(1)
    if tier != "standard":
        multipliers = entry.get("service_tier_multipliers")
        factor = _decimal(multipliers.get(tier)) if isinstance(multipliers, dict) else None
        if factor is None:
            return CallCost(None, f"{label}: no rate multiplier for service tier {tier!r}")
        tier_factor = factor
        notes.append(f"{label}: service tier {tier} at {factor}x")
    input_factor = output_factor = tier_factor
    long_context = "no"
    threshold = _count(entry, "long_context_over_input_tokens")
    if (
        threshold is not None
        and prompt_tokens > threshold
        and call.get("single_request") is not True
    ):
        # several requests added together: no way to tell whether any one crossed the limit
        long_context = "unknown"
        notes.append(
            f"{label}: long context unknown: {prompt_tokens:,} input tokens is an aggregate "
            f"of several requests; priced at base rates (limit {threshold:,} per request)"
        )
    elif threshold is not None and prompt_tokens > threshold:
        long_context = "applied"
        long_in = _decimal(entry.get("long_context_input_multiplier"))
        long_out = _decimal(entry.get("long_context_output_multiplier"))
        if long_in is None or long_out is None:
            return CallCost(None, f"{label}: no long-context multipliers for {model}")
        input_factor *= long_in
        output_factor *= long_out
        notes.append(f"{label}: {prompt_tokens:,} input tokens > {threshold:,}: long-context rates")

    # (tokens, price key, multiplier)
    parts: list[tuple[int, str, Decimal]] = [
        (fresh, "input_per_mtok", input_factor),
        (counts["cache_read"], "cache_read_per_mtok", input_factor),
        (counts["output"], "output_per_mtok", output_factor),
    ]
    short, long = _count(call, "cache_write_5m"), _count(call, "cache_write_1h")
    if short is not None and long is not None and "cache_write" not in skip:
        if short + long != counts["cache_write"]:
            return CallCost(None, f"{label}: 5m + 1h cache writes != cache_write tokens")
        parts.append((short, "cache_write_per_mtok", input_factor))
        parts.append((long, "cache_write_1h_per_mtok", input_factor))
    else:
        parts.append((counts["cache_write"], "cache_write_per_mtok", input_factor))
        if counts["cache_write"] and "cache_write_1h_per_mtok" in entry:
            notes.append(f"{label}: no 5m/1h cache-write split; priced at the 5m rate")
    if "reasoning" not in skip and entry.get("reasoning") != "included_in_output":
        reasoning = _count(call, "reasoning")
        if reasoning is None:
            return CallCost(None, f"{label}: reasoning tokens not reported")
        parts.append((reasoning, "reasoning_per_mtok", output_factor))

    total = Decimal(0)
    for tokens, key, factor in parts:
        if tokens == 0:
            continue
        price = _decimal(entry.get(key))
        if price is None:
            return CallCost(None, f"{label}: no {key} price for {model}")
        total += price * factor * Decimal(tokens) / _MTOK
    return CallCost(total, None, tuple(notes), long_context)


def price_calls(
    calls: list[dict[str, Any]], table: PriceTable, service_tier: str | None = None
) -> dict[str, Any]:
    """Sum of the priced calls plus coverage. ``cost_usd`` is None when nothing is priced."""

    total = Decimal(0)
    priced = 0
    unknown_long = 0
    missing: list[str] = []
    notes: list[str] = []
    for call in calls:
        result = price_call(call, table, service_tier)
        notes.extend(result.notes)
        unknown_long += result.long_context == "unknown"
        if result.cost is None:
            missing.append(result.missing or "unpriced call")
        else:
            total += result.cost
            priced += 1
    return {
        "cost_usd": str(total) if priced else None,
        "priced_calls": priced,
        "total_calls": len(calls),
        "long_context_unknown": unknown_long,
        "missing": missing,
        "notes": notes,
    }


def combine(*parts: dict[str, Any] | None) -> dict[str, Any]:
    """Add cost summaries from ``price_calls`` (``None`` parts are skipped)."""

    present = [p for p in parts if p is not None]
    costs = [Decimal(p["cost_usd"]) for p in present if p["cost_usd"] is not None]
    return {
        "cost_usd": str(sum(costs, Decimal(0))) if costs else None,
        "priced_calls": sum(p["priced_calls"] for p in present),
        "total_calls": sum(p["total_calls"] for p in present),
        "long_context_unknown": sum(p.get("long_context_unknown", 0) for p in present),
        "missing": [m for p in present for m in p["missing"]],
        "notes": [n for p in present for n in p["notes"]],
    }


def format_cost(summary: dict[str, Any] | None) -> str:
    """``$X (k/n calls priced)``; ``null`` instead of a dollar figure when nothing is priced."""

    if summary is None:
        return "null"
    cost = summary["cost_usd"]
    figure = "null" if cost is None else f"${Decimal(cost):.4f}"
    unknown = summary.get("long_context_unknown")
    extra = f"; long context unknown for {unknown}" if unknown else ""
    return f"{figure} ({summary['priced_calls']}/{summary['total_calls']} calls priced{extra})"
