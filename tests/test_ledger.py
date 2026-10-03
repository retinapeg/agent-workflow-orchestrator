from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agent_arena import ledger


@pytest.fixture(autouse=True)
def _no_real_pricing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # never read ~/.agent-arena/pricing.toml from a test
    monkeypatch.setenv("AGENT_PRICING_FILE", str(tmp_path / "no-pricing.toml"))


def _write_jsonl(path: Path, events: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def _claude_log(root: Path, repo: Path) -> None:
    base = {"cwd": str(repo), "sessionId": "s1"}
    usage = {
        "input_tokens": 10,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 50,
        "output_tokens": 5,
    }
    _write_jsonl(
        root / "-proj" / "s1.jsonl",
        [
            {
                **base,
                "type": "user",
                "timestamp": "2026-10-03T10:00:00Z",
                "message": {"role": "user", "content": "do the task"},
            },
            {
                **base,
                "type": "user",
                "isMeta": True,
                "timestamp": "2026-10-03T10:00:01Z",
                "message": {"role": "user", "content": "Caveat: meta"},
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-10-03T10:00:05Z",
                "message": {"id": "m1", "model": "claude-test-1", "usage": usage},
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-10-03T10:00:06Z",
                "message": {
                    "id": "m1",
                    "model": "claude-test-1",
                    "usage": {**usage, "output_tokens": 9},
                },
            },
            {
                **base,
                "type": "user",
                "timestamp": "2026-10-03T10:00:07Z",
                "toolUseResult": {},
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
            },
            {
                **base,
                "type": "user",
                "timestamp": "2026-10-03T10:02:00Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "fix it"}]},
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-10-03T10:03:00Z",
                "message": {"id": "m2", "model": "claude-test-1", "usage": usage},
            },
        ],
    )


def _codex_log(root: Path, repo: Path) -> None:
    day = root / "2026" / "10" / "03"
    rec = {
        "input_tokens": 1000,
        "cached_input_tokens": 600,
        "cache_write_input_tokens": 0,
        "output_tokens": 40,
        "reasoning_output_tokens": 20,
    }
    _write_jsonl(
        day / "rollout-a.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-10-03T09:00:00Z",
                "payload": {"id": "T1", "cwd": str(repo)},
            },
            {
                "type": "turn_context",
                "timestamp": "2026-10-03T09:00:01Z",
                "payload": {"model": "gpt-test"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-10-03T09:00:01Z",
                "payload": {"type": "task_started"},
            },
            {
                "type": "token_usage_record",
                "timestamp": "2026-10-03T09:00:30Z",
                "payload": {"response_id": "r1", "usage": rec},
            },
            {
                "type": "token_usage_record",
                "timestamp": "2026-10-03T09:00:30Z",
                "payload": {"response_id": "r1", "usage": rec},
            },
        ],
    )
    _write_jsonl(
        day / "rollout-child.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-10-03T09:00:10Z",
                "payload": {"id": "C1", "parent_thread_id": "T1", "cwd": str(repo)},
            },
            {"type": "turn_context", "payload": {"model": "gpt-test"}},
            {"type": "token_usage_record", "payload": {"response_id": "r2", "usage": rec}},
        ],
    )


def test_claude_session_counts_humans_and_dedupes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_log(tmp_path / "claude", repo)
    record = ledger.parse_claude_session(tmp_path / "claude" / "-proj" / "s1.jsonl", repo.resolve())
    assert record is not None
    assert record["human_messages"] == 2  # meta and tool results are not human turns
    assert len(record["calls"]) == 2 and record["calls"][0]["output"] == 9
    assert record["wall_time_s"] == 180.0


def test_codex_session_includes_subagents_once(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _codex_log(tmp_path / "codex", repo)
    from datetime import UTC, datetime, timedelta

    record = ledger.find_latest_session(
        repo.resolve(),
        datetime.now(UTC) - timedelta(hours=1),
        tmp_path / "none",
        tmp_path / "codex",
    )
    assert record is not None and record["agent"] == "codex"
    assert len(record["calls"]) == 2  # r1 deduped, r2 from the sub-agent
    assert record["human_messages"] == 1 and record["calls"][0]["model"] == "gpt-test"


def test_grade_writes_prompt_row_and_csv(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "done.txt").write_text("x")
    _claude_log(tmp_path / "claude", repo)
    task = tmp_path / "t.md"
    task.write_text(
        '+++\nagent = "claude"\nmodel = "m"\nallowed_tools = ["Read"]\n'
        "max_turns = 1\ntimeout_seconds = 5\n"
        '[[checks]]\nname = "done"\nargv = ["test", "-f", "done.txt"]\n+++\nDo it.\n'
    )
    runs = tmp_path / "runs"
    result = ledger.grade(
        task,
        repo,
        runs,
        None,
        since_hours=1,
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "none",
    )
    assert result["verified"] and result["arm"] == "prompt-only"
    # a harness row alongside it, as `agent_arena run` would write
    harness = runs / "20261003T000000Z-t-abc"
    harness.mkdir()
    (harness / "result.json").write_text(
        json.dumps(
            {
                "run_id": "20261003T000000Z-t-abc",
                "task": str(task),
                "repo": str(repo),
                "verified": False,
                "check_counts": {"PASS": 0, "FAIL": 1, "UNKNOWN": 0},
                "invocation": {
                    "provider": "claude_cli",
                    "models_reported": ["m"],
                    "wall_time_s": 3.0,
                },
                "usage": {"totals": {"input": 1, "output": 2}},
                "cost": {},
            }
        )
    )
    out = tmp_path / "ledger.csv"
    assert ledger.write_csv(runs, out) == 2
    rows = list(csv.DictReader(out.open()))
    arms = {r["arm"]: r for r in rows}
    assert arms["prompt-only"]["human_messages"] == "2"
    assert arms["prompt-only"]["metrics_source"] == "session log"
    assert arms["prompt-only"]["cache_write"] == "100"
    assert arms["harness"]["human_messages"] == "1" and arms["harness"]["cache_read"] == ""
    assert arms["harness"]["repo"] == "repo"  # basename only, safe to publish
    assert "prompt-only" in ledger.summary(runs)


def test_grade_without_session_still_logs_checks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    task = tmp_path / "t.md"
    task.write_text(
        '+++\nagent = "codex"\nmodel = "m"\nmax_turns = 1\ntimeout_seconds = 5\n'
        '[[checks]]\nargv = ["false"]\n+++\nDo it.\n'
    )
    result = ledger.grade(
        task,
        repo,
        tmp_path / "runs",
        None,
        since_hours=1,
        claude_root=tmp_path / "a",
        codex_root=tmp_path / "b",
    )
    assert not result["verified"] and result["usage"]["totals"]["input"] is None
    assert "no Claude Code/Codex session" in result["warnings"][0]


def test_csv_refuses_missing_runs_dir(tmp_path: Path) -> None:
    import pytest

    from agent_arena.errors import ArenaError

    out = tmp_path / "keep.csv"
    out.write_text("precious\n")
    with pytest.raises(ArenaError):
        ledger.write_csv(tmp_path / "missing", out)
    assert out.read_text() == "precious\n"


def _harness_run(runs: Path, run_id: str, usage: dict, verified: bool = True) -> None:
    (runs / run_id).mkdir(parents=True)
    (runs / run_id / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": "/x/live-hello.md",
                "repo": "/x/repo",
                "verified": verified,
                "check_counts": {"PASS": 1, "FAIL": 0, "UNKNOWN": 0},
                "invocation": {"provider": "claude_cli", "models_reported": ["m"]},
                "usage": usage,
                "cost": {},
            }
        )
    )


def test_excluded_runs_stay_in_csv_but_not_in_summary(tmp_path: Path) -> None:
    import pytest

    from agent_arena.errors import ArenaError

    runs = tmp_path / "runs"
    sums = {"input": 4, "cache_read": 10, "cache_write": 2, "output": 316}
    # pre-fix parser: no CLI totals recorded -> match is null
    _harness_run(runs, "20261003T000001Z-live-hello-aaa", {"totals": {**sums, "output": 10}})
    _harness_run(
        runs, "20261003T000002Z-live-hello-bbb", {"totals": sums, "cli_result_totals": sums}
    )
    _harness_run(
        runs,
        "20261003T000003Z-live-hello-ccc",
        {"totals": {**sums, "output": None}, "cli_result_totals": sums},
        verified=False,
    )
    excluded = tmp_path / "excluded_runs.toml"
    excluded.write_text(
        '[[excluded]]\nrun_id = "20261003T000001Z-live-hello-aaa"\n'
        'reason = "output tokens under-reported by the pre-fix parser"\n'
    )
    out = tmp_path / "ledger.csv"
    assert ledger.write_csv(runs, out, excluded) == 3  # excluded runs keep their row
    rows = {r["run_id"][-3:]: r for r in csv.DictReader(out.open())}
    assert rows["aaa"]["excluded"] == "output tokens under-reported by the pre-fix parser"
    assert rows["bbb"]["excluded"] == "" and rows["ccc"]["excluded"] == ""
    assert rows["aaa"]["cli_result_totals_match"] == ""
    assert rows["bbb"]["cli_result_totals_match"] == "True"
    assert rows["ccc"]["cli_result_totals_match"] == "False"

    text = ledger.summary(runs, excluded)
    assert "  2    1/2" in text and "1 excluded runs left out" in text
    assert "  3    2/3" in ledger.summary(runs)  # a failed run that is not excluded still counts

    excluded.write_text('[[excluded]]\nrun_id = "x"\n')
    with pytest.raises(ArenaError, match="needs a run_id and a reason"):
        ledger.write_csv(runs, out, excluded)


def _prices(tmp_path: Path, output_price: int = 10) -> Path:
    path = tmp_path / f"prices-{output_price}.toml"
    path.write_text(
        f'version = "v{output_price}"\n[models."claude-sonnet-5"]\ninput_per_mtok = 2\n'
        "cache_read_per_mtok = 0.20\ncache_write_per_mtok = 2.50\ncache_write_1h_per_mtok = 4\n"
        f'output_per_mtok = {output_price}\nsource = "https://example.invalid"\n'
        'retrieved = "2026-10-03"\n'
    )
    return path


def _call(model: str, output: int, **extra: object) -> dict:
    return {
        "call": 1,
        "provider": "claude_cli",
        "model": model,
        "input": 1000,
        "cache_read": 0,
        "cache_write": 0,
        "output": output,
        "reasoning": None,
        "not_applicable": ["reasoning"],
        "input_includes_cache_read": False,
        **extra,
    }


def _run(
    runs: Path, run_id: str, calls: list[dict], arm: str = "harness", **fields: object
) -> Path:
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    result = {
        "run_id": run_id,
        "arm": arm,
        "task": "/x/fix-bug.md",
        "repo": "/x/repo",
        "verified": True,
        "check_counts": {"PASS": 1, "FAIL": 0, "UNKNOWN": 0},
        "invocation": {"provider": "claude_cli", "wall_time_s": 10.0, "human_messages": 1},
        "usage": {"calls": calls},
        "cost": {"cli_reported_cost": "0.5", "total": {"cost_usd": "999"}},  # stale on purpose
        **fields,
    }
    (run_dir / "result.json").write_text(json.dumps(result))
    return run_dir


def test_csv_is_repriced_from_raw_tokens_on_every_rebuild(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _run(
        runs,
        "20261003T000001Z-fix-bug-aaa",
        [_call("claude-sonnet-5", 100), _call("mystery-model", 100)],
        metadata={"effort": "high", "service_tier": "standard", "ultracode": True, "quota": {}},
        nested_usage={"calls": [_call("claude-sonnet-5", 200, cli_reported_cost="0.1")]},
    )
    sealed = (run_dir / "result.json").read_text()
    out = tmp_path / "ledger.csv"

    ledger.write_csv(runs, out, pricing=_prices(tmp_path, 10))
    (row,) = list(csv.DictReader(out.open()))
    # agent: (1000*2 + 100*10)/1e6, one call unpriced; nested: (1000*2 + 200*10)/1e6
    assert (row["agent_cost_usd"], row["nested_cost_usd"]) == ("0.003", "0.004")
    assert row["total_cost_usd"] == "0.007"  # not the stale 999 stored in the sealed run
    assert (row["priced_calls"], row["total_calls"]) == ("2", "3")
    assert row["pricing_version"] == "v10" and row["cli_reported_cost_usd"] == "0.5"
    assert (row["effort"], row["service_tier"], row["ultracode"]) == ("high", "standard", "True")
    assert row["quota_used_pct_delta"] == ""

    ledger.write_csv(runs, out, pricing=_prices(tmp_path, 20))  # a new price table
    (row,) = list(csv.DictReader(out.open()))
    assert row["total_cost_usd"] == "0.010" and row["pricing_version"] == "v20"
    assert (run_dir / "result.json").read_text() == sealed  # the run itself is never edited

    ledger.write_csv(runs, out)  # no price table at all: null, never 0
    (row,) = list(csv.DictReader(out.open()))
    assert row["total_cost_usd"] == "" and (row["priced_calls"], row["total_calls"]) == ("0", "3")

    text = ledger.summary(runs, pricing=_prices(tmp_path, 10))
    assert "NOT FULLY PRICED" in text and "20261003T000001Z-fix-bug-aaa: 2/3 calls" in text


def test_summary_ratios_only_when_both_arms_used_the_same_model(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    prices = _prices(tmp_path)
    _run(runs, "20261003T000001Z-fix-bug-aaa", [_call("claude-sonnet-5", 100)])
    _run(
        runs,
        "20261003T000002Z-fix-bug-bbb-prompt",
        [_call("claude-sonnet-5", 400)],
        arm="prompt-only",
        invocation={"provider": "claude_interactive", "wall_time_s": 40.0, "human_messages": 4},
    )
    text = ledger.summary(runs, pricing=prices)
    assert "harness ÷ prompt-only" in text and "NOT FULLY PRICED" not in text
    # tokens 1100/1400, cost 0.003/0.006, wall 10/40, messages 1/4
    assert text.splitlines()[-1].split() == ["fix-bug", "0.79", "0.50", "0.25", "0.25"]

    (runs / "20261003T000002Z-fix-bug-bbb-prompt" / "result.json").unlink()
    (runs / "20261003T000002Z-fix-bug-bbb-prompt").rmdir()
    _run(
        runs, "20261003T000003Z-fix-bug-ccc-prompt", [_call("other-model", 400)], arm="prompt-only"
    )
    assert "fix-bug                n/a (different models)" in ledger.summary(runs, pricing=prices)


def test_attach_usage_adds_nested_calls_without_touching_the_sealed_run(tmp_path: Path) -> None:
    from agent_arena.errors import ArenaError

    runs = tmp_path / "runs"
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _run(runs, "20261003T000001Z-fix-bug-aaa", [_call("claude-sonnet-5", 100)])
    sealed = (run_dir / "result.json").read_text()
    script = tmp_path / "usage.py"
    nested = {"calls": [_call("claude-sonnet-5", 200)]}
    script.write_text(f"import json\nprint(json.dumps({nested!r}))\n")
    task = tmp_path / "t.md"
    task.write_text(
        '+++\nagent = "claude"\nmodel = "m"\nallowed_tools = ["Read"]\nmax_turns = 1\n'
        f'timeout_seconds = 5\nusage_import = {{ argv = ["{{python}}", "{script}"] }}\n'
        '[[checks]]\nargv = ["true"]\n+++\nDo it.\n'
    )
    ledger.attach_usage(run_dir.name, task, repo, runs)
    out = tmp_path / "ledger.csv"
    ledger.write_csv(runs, out, pricing=_prices(tmp_path))
    (row,) = list(csv.DictReader(out.open()))
    assert (row["agent_cost_usd"], row["nested_cost_usd"]) == ("0.003", "0.004")
    assert (row["priced_calls"], row["total_calls"]) == ("2", "2")
    assert (run_dir / "result.json").read_text() == sealed
    with pytest.raises(ArenaError, match="attached once"):
        ledger.attach_usage(run_dir.name, task, repo, runs)
