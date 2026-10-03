"""Eval ledger: harness runs vs. prompting alone, recorded automatically.

    python -m agent_arena.ledger grade TASK.md --repo PATH   # right after a manual session
    python -m agent_arena.ledger csv                         # rebuild the CSV (also automatic)
    python -m agent_arena.ledger summary                     # per task, per arm
    python -m agent_arena.ledger attach-usage RUN_ID TASK.md --repo PATH   # nested usage, late

The source of truth is the write-once run directories (default ``~/.agent-arena/runs``).
``agent_arena run`` writes harness rows. ``grade`` writes "prompt-only" rows: it runs the same
TASK.md checks on the repo and pulls tokens, duration and message counts from the most recent
interactive Claude Code or Codex session whose working directory is that repo. It keeps
numbers only, never session text. The CSV is rebuilt from those directories every time, so
nobody edits a spreadsheet by hand. Costs are recomputed from the raw tokens and the current
price table on every rebuild, so sealed runs are re-priced, never edited.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import stat
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .audit import AuditStore, rebuild_manifest
from .errors import ArenaError
from .pricing import default_pricing_path, load_price_table
from .process import ProcessRunner
from .run_mode import (
    DEFAULT_EXCLUDED,
    FAIL,
    PASS,
    UNKNOWN,
    _new_run_id,
    _seal,
    _validate_repo,
    cost_summary,
    load_exclusions,
    nested_sidecar,
    parse_task,
    peak_context,
    reconcile_usage,
    reprice_run,
    run_checks,
    run_usage_import,
    totals,
)

DEFAULT_RUNS = Path("~/.agent-arena/runs")
DEFAULT_CSV = Path(__file__).resolve().parents[2] / "experiments" / "harness_vs_prompting.csv"
CLAUDE_LOGS = Path("~/.claude/projects")
CODEX_LOGS = Path("~/.codex/sessions")

COLUMNS = [
    "run_id",
    "finished_at",
    "task",
    "arm",
    "agent",
    "model",
    "verified",
    "checks_pass",
    "checks_fail",
    "checks_unknown",
    "human_messages",
    "wall_time_s",
    "turns",
    "input",
    "cache_read",
    "cache_write",
    "output",
    "reasoning",
    "peak_context",
    "cli_result_totals_match",
    "effort",
    "service_tier",
    "ultracode",
    "quota_used_pct_delta",
    "cli_reported_cost_usd",
    "agent_cost_usd",
    "nested_cost_usd",
    "total_cost_usd",
    "priced_calls",
    "total_calls",
    "pricing_version",
    "repo",
    "metrics_source",
    "excluded",
]


# --------------------------------------------------------------------------- session logs


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _lines(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                out.append(event)
    return out


def _same_dir(cwd: Any, repo: Path) -> bool:
    if not isinstance(cwd, str):
        return False
    try:
        return Path(cwd).resolve() == repo
    except OSError:
        return False


def parse_claude_session(path: Path, repo: Path) -> dict[str, Any] | None:
    """Claude Code transcript (~/.claude/projects/*/<session>.jsonl): numbers only."""

    events = _lines(path)
    if not any(_same_dir(e.get("cwd"), repo) for e in events):
        return None
    calls: dict[str, dict[str, Any]] = {}
    humans = 0
    stamps = [t for t in (_ts(e.get("timestamp")) for e in events) if t]
    for event in events:
        raw_message = event.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
        if event.get("type") == "user" and not event.get("isMeta") and not event.get("isSidechain"):
            content = message.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            if content and not is_tool_result and "toolUseResult" not in event:
                humans += 1
        if event.get("type") == "assistant" and isinstance(message.get("usage"), dict):
            model = message.get("model")
            if model == "<synthetic>":
                continue
            usage = message["usage"]
            raw_split = usage.get("cache_creation")
            split: dict[str, Any] = raw_split if isinstance(raw_split, dict) else {}
            key = str(message.get("id") or event.get("uuid") or len(calls))
            calls[key] = {
                "provider": "claude_code",
                "model": model if isinstance(model, str) else None,
                "model_source": "reported" if isinstance(model, str) else "missing",
                "input": _int(usage.get("input_tokens")),
                "cache_read": _int(usage.get("cache_read_input_tokens")),
                "cache_write": _int(usage.get("cache_creation_input_tokens")),
                "cache_write_5m": _int(split.get("ephemeral_5m_input_tokens")),
                "cache_write_1h": _int(split.get("ephemeral_1h_input_tokens")),
                "output": _int(usage.get("output_tokens")),
                "reasoning": None,
                "not_applicable": ["reasoning"],
                "input_includes_cache_read": False,
                "single_request": True,  # one message id = one API request
                "wall_time_s": None,
            }
    if not stamps:
        return None
    return _session_record("claude", path, calls, humans, stamps)


def parse_codex_session(path: Path, repo: Path, children: list[Path]) -> dict[str, Any] | None:
    """Codex rollout (~/.codex/sessions/Y/M/D/*.jsonl) plus its sub-agent rollouts."""

    events = _lines(path)
    meta = next((e.get("payload") for e in events if e.get("type") == "session_meta"), None)
    if not isinstance(meta, dict) or not _same_dir(meta.get("cwd"), repo):
        return None
    calls: dict[str, dict[str, Any]] = {}
    humans = 0
    stamps = []
    for source in [path, *children]:
        source_events = events if source == path else _lines(source)
        model = None
        for event in source_events:
            stamp = _ts(event.get("timestamp"))
            if stamp and source == path:
                stamps.append(stamp)
            raw_payload = event.get("payload")
            payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
            if event.get("type") == "turn_context" and isinstance(payload.get("model"), str):
                model = payload["model"]
            if (
                source == path
                and event.get("type") == "event_msg"
                and payload.get("type") == "task_started"
            ):
                humans += 1
            if event.get("type") == "token_usage_record" and isinstance(payload.get("usage"), dict):
                usage = payload["usage"]
                key = str(payload.get("response_id") or f"{source.name}-{len(calls)}")
                calls[key] = {
                    "provider": "codex",
                    "model": model,
                    "model_source": "turn_context" if model else "missing",
                    "input": _int(usage.get("input_tokens")),
                    "cache_read": _int(usage.get("cached_input_tokens")),
                    "cache_write": _int(usage.get("cache_write_input_tokens")),
                    "output": _int(usage.get("output_tokens")),
                    "reasoning": _int(usage.get("reasoning_output_tokens")),
                    "not_applicable": [],
                    "input_includes_cache_read": True,
                    "wall_time_s": None,
                }
    if not stamps:
        return None
    return _session_record("codex", path, calls, humans, stamps)


def _session_record(
    agent: str, path: Path, calls: dict[str, dict[str, Any]], humans: int, stamps: list[datetime]
) -> dict[str, Any]:
    records = list(calls.values())
    for index, call in enumerate(records, start=1):
        call["call"] = index
    return {
        "agent": agent,
        "session_file": path.name,
        "started_at": min(stamps).isoformat(),
        "finished_at": max(stamps).isoformat(),
        "wall_time_s": round((max(stamps) - min(stamps)).total_seconds(), 3),
        "human_messages": humans,
        "calls": records,
    }


def find_latest_session(
    repo: Path, since: datetime, claude_root: Path, codex_root: Path, agent: str | None = None
) -> dict[str, Any] | None:
    found: list[dict[str, Any]] = []
    cutoff = since.timestamp()
    if agent in (None, "claude") and claude_root.is_dir():
        for path in claude_root.glob("*/*.jsonl"):
            if path.stat().st_mtime >= cutoff:
                record = parse_claude_session(path, repo)
                if record:
                    found.append(record)
    if agent in (None, "codex") and codex_root.is_dir():
        recent = [p for p in codex_root.glob("*/*/*/*.jsonl") if p.stat().st_mtime >= cutoff]
        parents: dict[str, list[Path]] = {}
        tops: list[Path] = []
        for path in recent:
            head = _first_meta(path)
            parent = head.get("parent_thread_id") if head else None
            if parent:
                parents.setdefault(str(parent), []).append(path)
            else:
                tops.append(path)
        for path in tops:
            head = _first_meta(path) or {}
            children = parents.get(str(head.get("id") or head.get("session_id")), [])
            record = parse_codex_session(path, repo, children)
            if record:
                found.append(record)
    return max(found, key=lambda r: r["finished_at"]) if found else None


def _first_meta(path: Path) -> dict[str, Any] | None:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for _, line in zip(range(5), handle, strict=False):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "session_meta":
                payload = event.get("payload")
                return payload if isinstance(payload, dict) else None
    return None


# --------------------------------------------------------------------------- grade


def grade(
    task_path: Path,
    repo: Path,
    runs_dir: Path,
    pricing_path: Path | None,
    since_hours: float = 12,
    agent: str | None = None,
    claude_root: Path = CLAUDE_LOGS,
    codex_root: Path = CODEX_LOGS,
) -> dict[str, Any]:
    spec = parse_task(task_path)
    repo = _validate_repo(repo)
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    session = find_latest_session(
        repo, since, claude_root.expanduser(), codex_root.expanduser(), agent
    )
    runs_dir = runs_dir.expanduser().resolve()
    runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = _new_run_id(spec.name) + "-prompt"
    store = AuditStore(runs_dir / run_id, run_id)
    checks = run_checks(spec, repo, store.run_dir, ProcessRunner())
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (PASS, FAIL, UNKNOWN)}
    calls = session["calls"] if session else []
    cost = cost_summary(calls, None, load_price_table(pricing_path), None, None)
    warnings = (
        []
        if session
        else [
            f"no Claude Code/Codex session with cwd={repo} in the last {since_hours}h; "
            "usage and time are null"
        ]
    )
    result = {
        "schema": "agent-arena-run-v1",
        "arm": "prompt-only",
        "run_id": run_id,
        "finished_at": session["finished_at"] if session else datetime.now(UTC).isoformat(),
        "task": str(spec.path),
        "repo": str(repo),
        "invocation": {
            "provider": f"{session['agent']}_interactive" if session else None,
            "model_declared": None,
            "models_reported": sorted({c["model"] for c in calls if c.get("model")}),
            "wall_time_s": session["wall_time_s"] if session else None,
            "turns": len(calls) or None,
            "human_messages": session["human_messages"] if session else None,
            "session_file": session["session_file"] if session else None,
        },
        "checks": checks,
        "check_counts": counts,
        "verified": counts[PASS] == len(checks),
        "usage": {
            "calls": calls,
            "totals": totals(calls),
            "peak_context_tokens": peak_context(calls),
        },
        "cost": cost,  # as priced today; the ledger re-prices from the calls on every rebuild
        "warnings": warnings,
    }
    store.write_json("result.json", result)
    rebuild_manifest(store.run_dir, run_id)
    _seal(store.run_dir)
    return result


# --------------------------------------------------------------------------- csv


def _stamp_from_run_id(run_id: str) -> str | None:
    try:
        return datetime.strptime(run_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None


def _totals_match(usage: dict[str, Any]) -> bool | None:
    """Do the per-call sums equal the CLI's own result totals? None if it reported none."""

    reported = usage.get("cli_result_totals")
    if not isinstance(reported, dict):
        return None
    return not reconcile_usage(usage.get("totals") or {}, reported)


def _total_tokens(calls: list[dict[str, Any]]) -> int | None:
    """Every token the calls processed: input (with cache) + output. None if any is unknown."""

    total = 0
    for call in calls:
        keys = ["input", "output"]
        if not call.get("input_includes_cache_read"):
            keys += [k for k in ("cache_read", "cache_write") if k not in call["not_applicable"]]
        values = [call.get(k) for k in keys]
        if any(not isinstance(v, int) for v in values):
            return None
        total += sum(v for v in values if isinstance(v, int))
    return total if calls else None


def _row(
    result: dict[str, Any],
    run_dir: Path,
    exclusions: dict[str, str],
    table: Any,
) -> dict[str, Any]:
    inv = result.get("invocation") or {}
    repriced = reprice_run(run_dir, result, table)
    calls = repriced["calls"]
    cost = repriced["cost"]
    tot = totals(calls) if calls else ((result.get("usage") or {}).get("totals") or {})
    counts = result.get("check_counts") or {}
    arm = result.get("arm", "harness")
    models = sorted({str(c["model"]) for c in calls if c.get("model")}) or (
        inv.get("models_reported") or []
    )
    humans = inv.get("human_messages")
    if humans is None and arm == "harness":
        humans = 1  # the one launch command; nothing relayed mid-run
    meta = result.get("metadata") or {}
    tiers = sorted({str(c["service_tier"]) for c in calls if c.get("service_tier")})
    nested = cost["nested"]
    return {
        "run_id": result.get("run_id"),
        "finished_at": result.get("finished_at") or _stamp_from_run_id(str(result.get("run_id"))),
        "task": Path(str(result.get("task", ""))).stem,
        "arm": arm,
        "agent": inv.get("provider"),
        "model": ";".join(models) or inv.get("model_declared"),
        "verified": result.get("verified"),
        "checks_pass": counts.get(PASS),
        "checks_fail": counts.get(FAIL),
        "checks_unknown": counts.get(UNKNOWN),
        "human_messages": humans,
        "wall_time_s": inv.get("wall_time_s"),
        "turns": inv.get("turns"),
        **{c: tot.get(c) for c in ("input", "cache_read", "cache_write", "output", "reasoning")},
        "peak_context": peak_context(calls),
        "cli_result_totals_match": _totals_match({**(result.get("usage") or {}), "totals": tot}),
        "effort": meta.get("effort"),
        "service_tier": meta.get("service_tier") or ";".join(tiers) or None,
        "ultracode": meta.get("ultracode"),
        "quota_used_pct_delta": (meta.get("quota") or {}).get("used_pct_delta"),
        "cli_reported_cost_usd": cost["cli_reported_cost"],
        "agent_cost_usd": cost["agent"]["cost_usd"],
        "nested_cost_usd": None if nested is None else nested["cost_usd"],
        "total_cost_usd": cost["total"]["cost_usd"],
        "priced_calls": cost["total"]["priced_calls"],
        "total_calls": cost["total"]["total_calls"],
        "pricing_version": cost["pricing"]["version"],
        "repo": Path(str(result.get("repo", ""))).name,  # basename only: safe to publish
        "metrics_source": "harness transcript"
        if arm == "harness"
        else ("session log" if inv.get("session_file") else "none found"),
        "excluded": exclusions.get(str(result.get("run_id"))),
        "_tokens": _total_tokens(calls),  # not a CSV column; used by the summary
    }


def load_rows(
    runs_dir: Path, excluded: Path | None = None, pricing: Path | None = None
) -> list[dict[str, Any]]:
    exclusions = load_exclusions(excluded)
    table = load_price_table(pricing or default_pricing_path())
    rows = []
    for path in sorted(runs_dir.expanduser().glob("*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(_row(data, path.parent, exclusions, table))
    return sorted(rows, key=lambda r: str(r["run_id"]))


def write_csv(
    runs_dir: Path, out: Path, excluded: Path | None = None, pricing: Path | None = None
) -> int:
    if not runs_dir.expanduser().is_dir():
        # Never replace a populated CSV with an empty one from a machine without the runs.
        raise ArenaError(f"runs dir not found: {runs_dir}; CSV left unchanged")
    rows = load_rows(runs_dir, excluded, pricing)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "" if row[k] is None else row[k] for k in COLUMNS})
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_suffix(".csv.tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(out)
    return len(rows)


def _median(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if v not in (None, "")]
    return statistics.median(nums) if nums else None


_RATIOS = (
    ("tokens", "_tokens"),
    ("total_cost_usd", "total_cost_usd"),
    ("wall_s", "wall_time_s"),
    ("msgs", "human_messages"),
)


def summary(runs_dir: Path, excluded: Path | None = None, pricing: Path | None = None) -> str:
    every = load_rows(runs_dir, excluded, pricing)
    rows = [r for r in every if not r["excluded"]]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["task"], row["arm"]), []).append(row)

    def med(values: list[Any], digits: int = 0) -> str:
        value = _median(values)
        return "null" if value is None else f"{value:,.{digits}f}"

    lines = [
        f"{'task':<22} {'arm':<12} {'n':>3} {'verified':>9} {'msgs':>6} "
        f"{'wall_s':>8} {'tokens':>11} {'total_cost_usd':>15}"
    ]
    for (task, arm), group in sorted(groups.items()):
        verified = sum(1 for r in group if r["verified"])
        lines.append(
            f"{task:<22} {arm:<12} {len(group):>3} {verified:>4}/{len(group):<4} "
            f"{med([r['human_messages'] for r in group]):>6} "
            f"{med([r['wall_time_s'] for r in group]):>8} "
            f"{med([r['_tokens'] for r in group]):>11} "
            f"{med([r['total_cost_usd'] for r in group], 4):>15}"
        )
    lines.append("medians per group; n is tiny, so read this as directional, not proof")
    lines.append("tokens = input incl. cache + output; costs are API-equivalent, agent + nested")

    pairs = sorted(t for t, arm in groups if arm == "harness" and (t, "prompt-only") in groups)
    if pairs:
        lines += ["", "harness ÷ prompt-only (ratio of medians)"]
        lines.append(f"{'task':<22} " + " ".join(f"{name:>15}" for name, _ in _RATIOS))
    for task in pairs:
        harness, prompt = groups[(task, "harness")], groups[(task, "prompt-only")]
        if {r["model"] for r in harness} != {r["model"] for r in prompt}:
            lines.append(f"{task:<22} n/a (different models)")
            continue
        cells = []
        for _, key in _RATIOS:
            top, bottom = _median([r[key] for r in harness]), _median([r[key] for r in prompt])
            cells.append("null" if top is None or not bottom else f"{top / bottom:.2f}")
        lines.append(f"{task:<22} " + " ".join(f"{c:>15}" for c in cells))

    partial = [r for r in rows if (r["priced_calls"] or 0) < (r["total_calls"] or 0)]
    if partial:
        lines += ["", "NOT FULLY PRICED (cost covers only the priced calls):"]
        lines += [f"  {r['run_id']}: {r['priced_calls']}/{r['total_calls']} calls" for r in partial]
    if len(every) != len(rows):
        lines.append(f"{len(every) - len(rows)} excluded runs left out (reasons are in the CSV)")
    return "\n".join(lines)


def attach_usage(run_id: str, task_path: Path, repo: Path, runs_dir: Path) -> dict[str, Any]:
    """Import nested usage for a run sealed before its TASK.md had ``usage_import``.

    The sealed directory is not touched: the result goes to a write-once sidecar next to it.
    """

    run_dir = runs_dir.expanduser().resolve() / run_id
    if not (run_dir / "result.json").is_file():
        raise ArenaError(f"no such run: {run_dir}")
    sidecar = nested_sidecar(run_dir)
    if sidecar.exists():
        raise ArenaError(f"{sidecar} already exists; nested usage is attached once")
    spec = parse_task(task_path)
    nested = run_usage_import(spec, _validate_repo(repo), run_dir, ProcessRunner())
    if nested is None:
        raise ArenaError(f"{task_path}: no usage_import in the front matter")
    if nested["error"]:
        raise ArenaError(f"usage import failed: {nested['error']}")
    nested["attached_at"] = datetime.now(UTC).isoformat()
    sidecar.write_text(json.dumps(nested, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar.chmod(stat.S_IRUSR)
    return nested


def after_run(run_args: list[str]) -> None:
    """Called after `agent_arena run`: rebuild the CSV so no one updates it by hand."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--excluded", type=Path, default=DEFAULT_EXCLUDED)
    parser.add_argument("--pricing", type=Path, default=None)
    known, _ = parser.parse_known_args(run_args)
    try:
        rows = write_csv(known.runs_dir, DEFAULT_CSV, known.excluded, known.pricing)
        print(f"ledger: {DEFAULT_CSV.name} updated ({rows} rows)")
    except (OSError, ArenaError) as exc:  # bookkeeping never changes the run's exit code
        print(f"ledger: CSV not updated: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_arena.ledger")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--excluded", type=Path, default=DEFAULT_EXCLUDED)
    parser.add_argument(
        "--pricing",
        type=Path,
        default=None,
        help="price table (default: $AGENT_PRICING_FILE or ~/.agent-arena/pricing.toml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grade", help="check + log the latest manual session for TASK.md")
    g.add_argument("task", type=Path)
    g.add_argument("--repo", type=Path, required=True)
    g.add_argument("--since-hours", type=float, default=12)
    g.add_argument("--agent", choices=["claude", "codex"])
    sub.add_parser("csv", help="rebuild the CSV from run directories")
    sub.add_parser("summary", help="print per-task, per-arm medians")
    a = sub.add_parser("attach-usage", help="import nested usage for an already sealed run")
    a.add_argument("run_id")
    a.add_argument("task", type=Path)
    a.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    pricing = args.pricing or default_pricing_path()
    try:
        if args.command == "attach-usage":
            nested = attach_usage(args.run_id, args.task, args.repo, args.runs_dir)
            print(f"attached {len(nested['calls'])} nested calls to {args.run_id}")
        if args.command == "grade":
            result = grade(
                args.task, args.repo, args.runs_dir, pricing, args.since_hours, args.agent
            )
            row = _row(
                result,
                args.runs_dir.expanduser() / result["run_id"],
                {},
                load_price_table(pricing),
            )
            print(
                f"graded {row['task']} (prompt-only): verified={row['verified']} "
                f"pass={row['checks_pass']} fail={row['checks_fail']} "
                f"unknown={row['checks_unknown']} msgs={row['human_messages']} "
                f"wall={row['wall_time_s']}s source={row['metrics_source']}"
            )
            for warning in result["warnings"]:
                print(f"warning: {warning}")
        if args.command in {"grade", "csv", "attach-usage"}:
            n = write_csv(args.runs_dir, args.csv, args.excluded, pricing)
            print(f"{args.csv}: {n} rows")
        if args.command == "summary":
            print(summary(args.runs_dir, args.excluded, pricing))
    except ArenaError as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
