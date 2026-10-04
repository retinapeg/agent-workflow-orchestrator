from __future__ import annotations

import json
import stat
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agent_arena.audit import verify_manifest
from agent_arena.errors import ArenaError
from agent_arena.pricing import (
    PriceTable,
    format_cost,
    load_price_table,
    price_call,
    price_calls,
)
from agent_arena.run_mode import (
    claude_argv,
    codex_argv,
    main,
    parse_claude_stream,
    parse_codex_stream,
    parse_task,
    recent_state_entries,
    reconcile_usage,
    totals,
)


@pytest.fixture(autouse=True)
def _no_real_pricing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # never read ~/.agent-arena/pricing.toml from a test
    monkeypatch.setenv("AGENT_PRICING_FILE", str(tmp_path / "no-pricing.toml"))


def _start(message_id: str) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": message_id}},
        "parent_tool_use_id": None,
    }


def _delta(output_tokens: int) -> dict:
    return {
        "type": "stream_event",
        "event": {"type": "message_delta", "usage": {"output_tokens": output_tokens}},
        "parent_tool_use_id": None,
    }


CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "model": "claude-test-1"},
    _start("m1"),
    {
        "type": "assistant",
        "message": {
            "id": "m1",
            "model": "claude-test-1",
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 500,
                "output_tokens": 3,
            },
        },
    },
    # same message id streamed again (second content block): must not double-count
    {
        "type": "assistant",
        "message": {
            "id": "m1",
            "model": "claude-test-1",
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 500,
                "output_tokens": 3,
            },
        },
    },
    _delta(20),  # the final output count; the assistant events only carry a snapshot
    # cache_creation missing on purpose: must stay null, never 0
    _start("m2"),
    {
        "type": "assistant",
        "message": {
            "id": "m2",
            "model": "claude-test-1",
            "usage": {"input_tokens": 50, "cache_read_input_tokens": 1500, "output_tokens": 7},
        },
    },
    _delta(30),
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "duration_api_ms": 1234,
        "total_cost_usd": 0.0123,
        "result": "CHANGED: x",
    },
]

FAKE_CLAUDE = """#!{python}
import json, sys, pathlib
sys.stdin.read()
pathlib.Path("touched.txt").write_text("agent was here")
for event in {events!r}:
    print(json.dumps(event))
"""


def _fake(tmp_path: Path, events: list[dict]) -> Path:
    exe = tmp_path / "fake-claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable, events=events))
    exe.chmod(0o755)
    return exe


def _task(tmp_path: Path, checks: str, extra: str = "") -> Path:
    path = tmp_path / "TASK.md"
    path.write_text(
        '+++\nagent = "claude"\nmodel = "claude-test-1"\nallowed_tools = ["Read", "Write"]\n'
        f"max_turns = 5\ntimeout_seconds = 30\n{extra}\n{checks}\n+++\n\nTouch a file.\n"
    )
    return path


PASS_CHECK = '[[checks]]\nname = "touched"\nargv = ["test", "-f", "touched.txt"]\n'
FAIL_CHECK = '[[checks]]\nname = "absent"\nargv = ["test", "-f", "nope.txt"]\n'
UNKNOWN_CHECK = (
    '[[checks]]\nname = "unknowable"\nargv = ["{python}", "-c", "raise SystemExit(2)"]\n'
)


def _pricing(tmp_path: Path, model: str = "claude-test-1") -> Path:
    path = tmp_path / "pricing.toml"
    path.write_text(
        f'version = "test-1"\n[models."{model}"]\ninput_per_mtok = 3\ncache_read_per_mtok = 0.3\n'
        "cache_write_per_mtok = 3.75\noutput_per_mtok = 15\n"
        'source = "https://example.invalid/pricing"\nretrieved = "2026-10-03"\n'
    )
    return path


def test_parse_task_rejects_unknown_keys_and_missing_model(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text('+++\nagent = "claude"\nsurprise = 1\n+++\nx\n')
    with pytest.raises(ArenaError, match="unknown front-matter"):
        parse_task(bad)
    bad.write_text(
        '+++\nagent = "claude"\nallowed_tools=["Read"]\nmax_turns=1\n'
        'timeout_seconds=1\n[[checks]]\nargv=["true"]\n+++\nx\n'
    )
    with pytest.raises(ArenaError, match="model is required"):
        parse_task(bad)


def test_claude_stream_dedupes_calls_and_keeps_nulls() -> None:
    parsed = parse_claude_stream("\n".join(json.dumps(e) for e in CLAUDE_STREAM))
    calls = parsed["calls"]
    assert len(calls) == 2
    assert calls[0]["output"] == 20
    assert calls[1]["cache_write"] is None  # missing -> null, never 0
    assert parsed["summary"]["cli_reported_cost"] == "0.0123"
    assert parsed["summary"]["turns"] == 2


def test_missing_token_count_is_unpriced_not_zero(tmp_path: Path) -> None:
    calls = parse_claude_stream("\n".join(json.dumps(e) for e in CLAUDE_STREAM))["calls"]
    table = load_price_table(_pricing(tmp_path))
    priced = price_calls(calls, table)
    # call 1: 1000*3 + 500*3.75 + 20*15 per million; call 2 has no cache_write count
    assert priced["cost_usd"] == "0.005175"
    assert (priced["priced_calls"], priced["total_calls"]) == (1, 2)
    assert priced["missing"] == ["call 2: cache_write tokens not reported"]


def test_codex_cached_input_is_not_double_billed(tmp_path: Path) -> None:
    usage = {
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "cache_write_input_tokens": 100,
        "output_tokens": 100,
    }
    calls = parse_codex_stream(json.dumps({"type": "turn.completed", "usage": usage}), "gpt-test")[
        "calls"
    ]
    # cache writes are billable for Codex: read, not n/a
    assert calls[0]["cache_write"] == 100 and calls[0]["not_applicable"] == []
    assert calls[0]["model_source"] == "declared" and calls[0]["reasoning"] is None
    table = load_price_table(_pricing(tmp_path, "gpt-test"))
    # reasoning not reported -> the pricer refuses to guess
    assert price_call(calls[0], table).missing == "call 1: reasoning tokens not reported"
    table.models["gpt-test"]["reasoning"] = "included_in_output"
    # (1000-400-100)*3 + 400*0.3 + 100*3.75 + 100*15 per million
    assert price_call(calls[0], table).cost == Decimal("0.003495")
    without = dict(usage)
    del without["cache_write_input_tokens"]
    old = parse_codex_stream(json.dumps({"type": "turn.completed", "usage": without}), "gpt-test")
    assert price_call(old["calls"][0], table).missing == "call 1: cache_write tokens not reported"


def test_unpriced_model_is_null_with_the_reason() -> None:
    calls = parse_claude_stream("\n".join(json.dumps(e) for e in CLAUDE_STREAM))["calls"]
    priced = price_calls(calls[:1], PriceTable({}))
    assert priced["cost_usd"] is None and priced["priced_calls"] == 0
    assert "'claude-test-1' not in the pricing file" in priced["missing"][0]


def test_end_to_end_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "STATE.md").write_text("# STATE\n\n## earlier\n- next: do the thing\n")
    task = _task(tmp_path, PASS_CHECK + FAIL_CHECK + UNKNOWN_CHECK)
    runs = tmp_path / "runs"
    code = main(
        [
            str(task),
            "--repo",
            str(repo),
            "--runs-dir",
            str(runs),
            "--pricing",
            str(_pricing(tmp_path)),
            "--executable",
            str(_fake(tmp_path, CLAUDE_STREAM)),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1  # a FAIL is present
    assert "PASS     touched" in out and "FAIL     absent" in out and "UNKNOWN  unknowable" in out
    assert "COST  API-equivalent (subscription: no marginal $)" in out
    assert "never merged: agent $0.0123" in out
    assert "agent:   $0.0052 (1/2 calls priced)" in out  # call 2 lacks cache_write
    assert "nested:  none (no usage_import in TASK.md)" in out
    assert "total:   $0.0052 (1/2 calls priced)" in out
    assert "missing: call 2: cache_write tokens not reported" in out
    assert "pricing: version test-1, sha256 " in out
    (run_dir,) = list(runs.iterdir())
    result = json.loads((run_dir / "result.json").read_text())
    assert result["usage"]["totals"]["cache_write"] is None
    stamp = result["cost"]["pricing"]
    assert stamp["version"] == "test-1" and len(stamp["sha256"]) == 64
    assert result["metadata"] == {
        "effort": "high",  # unset in TASK.md -> Claude's documented default, passed explicitly
        "effort_source": "harness default",
        "service_tier": "standard",
        "service_tiers_reported": [],
        "ultracode": False,
        "network": False,
        "full_access": False,
        "subagents": False,
        "quota": {"used_pct_delta": None, "window_minutes": None},
    }
    assert result["usage"]["peak_context_tokens"] == 1550
    assert "do the thing" in (run_dir / "prompt.txt").read_text()  # resumes from STATE.md
    verify_manifest(run_dir)
    assert not (run_dir / "report.txt").stat().st_mode & stat.S_IWUSR  # sealed
    state = (repo / "STATE.md").read_text()
    assert "agent_arena run: TASK" in state and "1 PASS, 1 FAIL, 1 UNKNOWN" in state


def test_verified_run_feeds_ledger(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    cut = CLAUDE_STREAM.index(_start("m2"))
    events = CLAUDE_STREAM[:cut] + CLAUDE_STREAM[-1:]
    task = _task(tmp_path, PASS_CHECK, 'next_on_pass = "ship it"\ncontext_warn_tokens = 1000')
    args = [
        str(task),
        "--repo",
        str(repo),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--pricing",
        str(_pricing(tmp_path)),
        "--executable",
        str(_fake(tmp_path, events)),
    ]
    assert main(args) == 0
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "total:   $0.0052 (1/1 calls priced)" in out
    assert "Ledger, 2 runs incl. failed" in out and "verified outcomes 2" in out
    assert "peak context 1,500 >= context_warn_tokens" in out
    assert "NEXT: ship it" in out


def test_refuses_home_as_repo(tmp_path: Path) -> None:
    task = _task(tmp_path, PASS_CHECK)
    assert main([str(task), "--repo", str(Path.home()), "--runs-dir", str(tmp_path / "r")]) == 3


def test_recent_state_entries_keeps_last_n(tmp_path: Path) -> None:
    (tmp_path / "STATE.md").write_text("# S\n\n## a\n1\n## b\n2\n## c\n3\n")
    assert recent_state_entries(tmp_path, 2) == "## b\n2\n## c\n3"


# Trimmed lines from a real `claude` 2.1.288 transcript (live-hello, 2026-10-03). Usage keys
# and values are as emitted; content, uuids and unrelated fields are removed.
REAL_CLAUDE_LINES = """\
{"type": "system", "subtype": "init", "model": "claude-sonnet-5"}
{"type": "stream_event", "event": {"type": "message_start", "message": {"model": "claude-sonnet-5", "id": "msg_011Cffx6TK73ZeuoqT8EXPA1", "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1657, "cache_read_input_tokens": 4675, "output_tokens": 3}}}, "parent_tool_use_id": null}
{"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_011Cffx6TK73ZeuoqT8EXPA1", "content": [{"type": "thinking"}], "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1657, "cache_read_input_tokens": 4675, "output_tokens": 3}}, "parent_tool_use_id": null}
{"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_011Cffx6TK73ZeuoqT8EXPA1", "content": [{"type": "tool_use"}], "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1657, "cache_read_input_tokens": 4675, "output_tokens": 3}}, "parent_tool_use_id": null}
{"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1657, "cache_read_input_tokens": 4675, "output_tokens": 230, "output_tokens_details": {"thinking_tokens": 137}}}, "parent_tool_use_id": null}
{"type": "stream_event", "event": {"type": "message_start", "message": {"model": "claude-sonnet-5", "id": "msg_011Cffx6ZRhSnEKE6WWi4poJ", "usage": {"input_tokens": 2, "cache_creation_input_tokens": 317, "cache_read_input_tokens": 6332, "output_tokens": 7}}}, "parent_tool_use_id": null}
{"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_011Cffx6ZRhSnEKE6WWi4poJ", "content": [{"type": "text"}], "usage": {"input_tokens": 2, "cache_creation_input_tokens": 317, "cache_read_input_tokens": 6332, "output_tokens": 7}}, "parent_tool_use_id": null}
{"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 317, "cache_read_input_tokens": 6332, "output_tokens": 86, "output_tokens_details": {"thinking_tokens": 0}}}, "parent_tool_use_id": null}
{"type": "result", "subtype": "success", "is_error": false, "num_turns": 2, "duration_api_ms": 2105, "total_cost_usd": 0.0132654, "usage": {"input_tokens": 4, "cache_creation_input_tokens": 1974, "cache_read_input_tokens": 11007, "output_tokens": 316}, "result": "CHANGED: hello.txt"}
"""  # noqa: E501


def test_real_transcript_output_is_the_final_count_not_the_snapshot() -> None:
    parsed = parse_claude_stream(REAL_CLAUDE_LINES)
    calls = parsed["calls"]
    assert [c["output"] for c in calls] == [230, 86]  # not the 3 and 7 on the assistant events
    assert [c["cache_read"] for c in calls] == [4675, 6332]
    assert [c["cache_write"] for c in calls] == [1657, 317]
    assert {c["model"] for c in calls} == {"claude-sonnet-5"}
    summed = totals(calls)
    assert summed["output"] == 316
    assert reconcile_usage(summed, parsed["summary"]["result_usage"]) == []


def test_output_is_null_without_a_message_delta_and_the_mismatch_is_flagged() -> None:
    lines = [ln for ln in REAL_CLAUDE_LINES.splitlines() if "message_delta" not in ln]
    parsed = parse_claude_stream("\n".join(lines))
    assert [c["output"] for c in parsed["calls"]] == [None, None]  # snapshot is never reported
    summed = totals(parsed["calls"])
    assert summed["output"] is None and summed["input"] == 4
    assert reconcile_usage(summed, parsed["summary"]["result_usage"]) == [
        "output: per-call sum null != CLI result total 316"
    ]


def test_claude_argv_asks_for_final_usage_events(tmp_path: Path) -> None:
    argv = claude_argv("claude", parse_task(_task(tmp_path, PASS_CHECK)))
    assert argv[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert "--include-partial-messages" in argv
    assert argv[argv.index("--max-turns") + 1] == "5"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == "Read,Write"
    allowed = argv.index("--allowedTools")
    assert argv[allowed + 1 : argv.index("--disallowedTools")] == ["Read", "Write"]


def test_permission_denials_and_cli_errors_reach_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # result fields as emitted by claude 2.1.288 for a denied Bash call and a hit turn cap
    denied = "python3 -m platform --terse > in.log 2>&1"
    events = CLAUDE_STREAM[:-1] + [
        {
            **CLAUDE_STREAM[-1],
            "subtype": "error_max_turns",
            "is_error": True,
            "permission_denials": [
                {"tool_name": "Bash", "tool_use_id": "toolu_1", "tool_input": {"command": denied}}
            ],
        }
    ]
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    args = [str(_task(tmp_path, PASS_CHECK)), "--repo", str(repo), "--runs-dir", str(runs)]
    assert main([*args, "--executable", str(_fake(tmp_path, events))]) == 0  # checks decide
    out = capsys.readouterr().out
    assert f"- permission denied: Bash: {denied}" in out
    assert "- the CLI ended in an error (error_max_turns)" in out
    (run_dir,) = list(runs.iterdir())
    result = json.loads((run_dir / "result.json").read_text())
    assert result["invocation"]["permission_denials"] == [{"tool": "Bash", "input": denied}]


def test_report_ledger_leaves_out_excluded_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    excluded = tmp_path / "excluded_runs.toml"
    cut = CLAUDE_STREAM.index(_start("m2"))
    args = [
        str(_task(tmp_path, PASS_CHECK)),
        "--repo",
        str(repo),
        "--runs-dir",
        str(runs),
        "--pricing",
        str(_pricing(tmp_path)),
        "--excluded",
        str(excluded),
        "--executable",
        str(_fake(tmp_path, CLAUDE_STREAM[:cut] + CLAUDE_STREAM[-1:])),
    ]
    assert main(args) == 0
    (first,) = list(runs.iterdir())
    first_out = capsys.readouterr().out
    assert "Ledger, 1 runs incl. failed: total $0.0052 (1/1 calls priced)" in first_out
    excluded.write_text(f'[[excluded]]\nrun_id = "{first.name}"\nreason = "harness probe"\n')
    assert main(args) == 0
    out = capsys.readouterr().out
    # the excluded run adds neither a verified outcome nor its cost
    assert "Ledger, 1 runs incl. failed (1 excluded): total $0.0052 (1/1 calls priced)" in out
    assert "verified outcomes 1, cost per verified outcome $0.0052" in out

    excluded.write_text('[[excluded]]\nrun_id = "x"\n')
    assert main(args) == 3  # a bad exclusion file stops before the agent runs
    assert len(list(runs.iterdir())) == 2


SONNET = (
    'version = "t"\n[models."claude-sonnet-5"]\ninput_per_mtok = 2\ncache_read_per_mtok = 0.20\n'
    "cache_write_per_mtok = 2.50\ncache_write_1h_per_mtok = 4\noutput_per_mtok = 10\n"
    'source = "https://example.invalid"\nretrieved = "2026-10-03"\n'
    '[models."gpt-x"]\ninput_per_mtok = 2\ncache_read_per_mtok = 0.20\n'
    'cache_write_per_mtok = 2.50\noutput_per_mtok = 10\nreasoning = "included_in_output"\n'
    "service_tier_multipliers = { fast = 2, ultrafast = 6 }\n"
    "long_context_over_input_tokens = 272000\nlong_context_input_multiplier = 2\n"
    'long_context_output_multiplier = 1.5\nsource = "https://example.invalid"\n'
    'retrieved = "2026-10-03"\n'
)


def _table(tmp_path: Path) -> PriceTable:
    path = tmp_path / "prices.toml"
    path.write_text(SONNET)
    return load_price_table(path)


def test_claude_cache_writes_are_priced_by_ttl(tmp_path: Path) -> None:
    # usage as claude 2.1.288 emits it on an assistant event (lab reviewer call, 2026-10-03);
    # the CLI reported total_cost_usd 0.0247516 for exactly these tokens
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 3051,
        "cache_read_input_tokens": 518,
        "cache_creation": {"ephemeral_1h_input_tokens": 3051, "ephemeral_5m_input_tokens": 0},
        "output_tokens": 3,
        "service_tier": "standard",
    }
    stream = [
        _start("m1"),
        {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5", "usage": usage}},
        _delta(1244),
    ]
    (call,) = parse_claude_stream("\n".join(json.dumps(e) for e in stream))["calls"]
    assert (call["cache_write_5m"], call["cache_write_1h"]) == (0, 3051)
    assert call["service_tier"] == "standard"
    table = _table(tmp_path)
    priced = price_call(call, table)
    assert priced.cost == Decimal("0.0247516") and priced.notes == ()
    # no split reported: the 5m rate, and it says so
    del call["cache_write_5m"], call["cache_write_1h"]
    fallback = price_call(call, table)
    assert fallback.cost == Decimal("0.0201751")
    assert fallback.notes == ("call 1: no 5m/1h cache-write split; priced at the 5m rate",)


def test_service_tier_and_long_context_multipliers(tmp_path: Path) -> None:
    table = _table(tmp_path)
    call = {
        "call": 1,
        "model": "gpt-x",
        "input": 1000,
        "cache_read": 400,
        "cache_write": 0,
        "output": 100,
        "reasoning": 50,
        "input_includes_cache_read": True,
    }
    base = Decimal("0.00228")  # 600*2 + 400*0.2 + 100*10 per million
    assert price_call(call, table).cost == base
    assert price_call(call, table, "fast").cost == base * 2
    assert price_call({**call, "service_tier": "ultrafast"}, table, "fast").cost == base * 6
    assert "no rate multiplier" in str(price_call(call, table, "priority").missing)
    long = {**call, "input": 272_001, "single_request": True}
    # the whole request: 2x input and cache, 1.5x output
    want = Decimal(271_601) * 2 * 2 + Decimal(400) * Decimal("0.2") * 2 + 100 * 10 * Decimal("1.5")
    assert price_call(long, table).cost == want / 1_000_000
    assert price_call(long, table).long_context == "applied"
    assert price_call({**call, "input": 272_000, "single_request": True}, table).notes == ()


def test_long_context_is_never_applied_to_an_aggregate_record(tmp_path: Path) -> None:
    table = _table(tmp_path)
    # a Codex turn of five ~80K-token requests: 400K input in total, no request over 272K
    usage = {
        "input_tokens": 400_000,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 1000,
    }
    (call,) = parse_codex_stream(json.dumps({"type": "turn.completed", "usage": usage}), "gpt-x")[
        "calls"
    ]
    assert call["single_request"] is False
    priced = price_call(call, table)
    assert priced.cost == Decimal("0.81")  # 400000*2 + 1000*10 per million: base rates, no 2x
    assert priced.long_context == "unknown"
    assert "long context unknown: 400,000 input tokens is an aggregate" in priced.notes[0]
    summary = price_calls([call], table)
    assert summary["long_context_unknown"] == 1
    assert format_cost(summary) == "$0.8100 (1/1 calls priced; long context unknown for 1)"
    # the same tokens as one request are surcharged
    assert price_call({**call, "single_request": True}, table).cost == Decimal("1.615")
    # an imported record is an aggregate unless it says otherwise
    assert price_call({**call, "single_request": None}, table).long_context == "unknown"


def test_effort_reaches_the_cli_and_codex_quota_delta_is_recorded(tmp_path: Path) -> None:
    spec = parse_task(_task(tmp_path, PASS_CHECK, 'effort = "high"\nultracode = true'))
    argv = claude_argv("claude", spec)
    assert argv[argv.index("--effort") + 1] == "high" and spec.ultracode
    assert 'model_reasoning_effort="high"' in codex_argv("codex", spec, tmp_path)
    assert "--effort" not in claude_argv("claude", parse_task(_task(tmp_path, PASS_CHECK)))
    events = [
        {"type": "x", "payload": {"rate_limits": {"primary": {"used_percent": 10.5}}}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
        {"type": "x", "rate_limits": {"primary": {"used_percent": 12.0, "window_minutes": 300}}},
    ]
    parsed = parse_codex_stream("\n".join(json.dumps(e) for e in events), "gpt-x")
    assert parsed["summary"]["quota"] == {"used_pct_delta": 1.5, "window_minutes": 300}
    bare = parse_codex_stream(json.dumps(events[1]), "gpt-x")  # not exposed -> null, not 0
    assert bare["summary"]["quota"] == {"used_pct_delta": None, "window_minutes": None}


def _importer(tmp_path: Path, body: str) -> str:
    script = tmp_path / "usage.py"
    script.write_text(body)
    return f'usage_import = {{ argv = ["{{python}}", "{script}"] }}'


def test_nested_usage_is_imported_and_priced_with_the_same_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nested = [
        {
            "provider": "claude_cli",
            "model": "claude-test-1",
            "input": 1000,
            "cache_read": 0,
            "cache_write": 0,
            "output": 100,
            "not_applicable": ["reasoning"],
            "input_includes_cache_read": False,
            "cli_reported_cost": 0.0046,
        },
        {"provider": "codex_cli", "model": "gpt-unknown", "input": 5, "output": 5},
    ]
    extra = _importer(tmp_path, f"import json\nprint(json.dumps({{'calls': {nested!r}}}))\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    cut = CLAUDE_STREAM.index(_start("m2"))
    fake = str(_fake(tmp_path, CLAUDE_STREAM[:cut] + CLAUDE_STREAM[-1:]))
    pricing = str(_pricing(tmp_path))

    def run(task: Path) -> tuple[int, str]:
        args = [str(task), "--repo", str(repo), "--runs-dir", str(runs), "--pricing", pricing]
        return main([*args, "--executable", fake]), capsys.readouterr().out

    code, out = run(_task(tmp_path, PASS_CHECK, extra))
    assert code == 0
    assert "agent:   $0.0052 (1/1 calls priced)" in out
    assert "nested:  $0.0045 (1/2 calls priced)" in out  # 1000*3 + 100*15 per million
    assert "total:   $0.0097 (2/3 calls priced)" in out
    assert "missing: nested call 2: model 'gpt-unknown' not in the pricing file" in out
    assert "nested $0.0046 (1/2 calls reported one)" in out  # kept apart, never merged
    (run_dir,) = list(runs.iterdir())
    result = json.loads((run_dir / "result.json").read_text())
    assert result["cost"]["total"]["cost_usd"] == "0.009675"
    assert [c["model"] for c in result["nested_usage"]["calls"]] == ["claude-test-1", "gpt-unknown"]

    broken = _importer(tmp_path, "raise SystemExit('lab run not found')\n")
    code, out = run(_task(tmp_path, PASS_CHECK, broken))
    assert code == 0  # bookkeeping never changes the verdict
    # a failed import is unknown spend: coverage below 100% and the run is not complete
    assert "nested:  null (import failed: unknown spend)" in out
    assert "total:   $0.0052 (1/2 calls priced)" in out
    assert "missing: nested usage not imported (exit 1: lab run not found): unknown spend" in out
    # ledger: run 1 is 2/3 priced, run 2 is 1/2 -> not complete, so no cost per verified outcome
    assert "total $0.0148 (3/5 calls priced), verified outcomes 2" in out  # 0.01485
    assert "cost per verified outcome null" in out
    (failed,) = [d for d in runs.iterdir() if d != run_dir]
    total = json.loads((failed / "result.json").read_text())["cost"]["total"]
    assert (total["priced_calls"], total["total_calls"]) == (1, 2)
    from agent_arena import ledger as ledger_module

    rows = {r["run_id"]: r for r in ledger_module.load_rows(runs, pricing=Path(pricing))}
    assert (rows[failed.name]["priced_calls"], rows[failed.name]["total_calls"]) == (1, 2)
    assert rows[failed.name]["nested_cost_usd"] is None
    assert f"{failed.name}: 1/2 calls" in ledger_module.summary(runs, pricing=Path(pricing))


def _usage_script() -> object:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tasks" / "codex_smoke_usage.py"
    spec = importlib.util.spec_from_file_location("codex_smoke_usage", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_usage_import_selects_the_lab_run_made_by_this_harness_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    usage: Any = _usage_script()
    harness = tmp_path / "harness-run"
    harness.mkdir()
    (harness / "events.jsonl").write_text(
        json.dumps({"event": "agent_started", "timestamp": "2026-10-03T21:11:18.520797+00:00"})
        + "\n"
        + json.dumps({"event": "agent_finished", "timestamp": "2026-10-03T21:14:37.830215+00:00"})
        + "\n"
    )
    lab = tmp_path / "lab"

    def lab_run(name: str, started_at: str, model: str) -> None:
        run = lab / "results" / "runs" / name
        run.mkdir(parents=True)
        (run / "run.json").write_text(json.dumps({"started_at": started_at}))
        call = {
            "provider": "codex_cli",
            "role": "coder",
            "model": model,
            "models_used": [model],
            "list_cost_usd": None,
            "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2},
        }
        (run / "episodes.jsonl").write_text(json.dumps({"calls": [call]}) + "\n")

    lab_run("20261003T211124Z-aaaa-codex-smoke", "2026-10-03T21:11:24+00:00", "ours")
    # a newer run with the same label, started after this harness run finished
    lab_run("20261003T220000Z-bbbb-codex-smoke", "2026-10-03T22:00:00+00:00", "someone-elses")
    monkeypatch.chdir(lab)
    assert usage.main(["codex_smoke_usage.py", str(harness)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"].endswith("20261003T211124Z-aaaa-codex-smoke")  # not the newest
    assert [c["model"] for c in payload["calls"]] == ["ours"]
    assert "single_request" not in payload["calls"][0]  # one CLI run = an aggregate record

    lab_run("20261003T211300Z-cccc-codex-smoke", "2026-10-03T21:13:00+00:00", "second")
    assert usage.main(["codex_smoke_usage.py", str(harness)]) == 2  # ambiguous
    assert "found 2" in capsys.readouterr().err

    (harness / "events.jsonl").write_text(
        json.dumps({"event": "agent_started", "timestamp": "2026-10-01T00:00:00+00:00"})
        + "\n"
        + json.dumps({"event": "agent_finished", "timestamp": "2026-10-01T00:05:00+00:00"})
        + "\n"
    )
    assert usage.main(["codex_smoke_usage.py", str(harness)]) == 2  # none in the window
    assert "found 0 (none)" in capsys.readouterr().err


def test_agent_override_switches_to_codex_and_back(tmp_path: Path, capsys) -> None:
    """--agent codex on a Claude task runs the codex argv; --agent claude on a task with no
    allowed_tools falls back to the default allowlist and says so."""
    import agent_arena.run_mode as rm

    seen: list[tuple[str, str, tuple[str, ...]]] = []

    def fake_execute(spec, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((spec.agent, spec.model, spec.allowed_tools))
        raise rm.ArenaError("stop here")

    task = tmp_path / "t.md"
    task.write_text(
        '+++\nagent = "codex"\nmodel = "gpt-6-sol"\nmax_turns = 1\ntimeout_seconds = 5\n'
        '[[checks]]\nargv = ["true"]\n+++\nDo it.\n'
    )
    original = rm.execute
    rm.execute = fake_execute
    try:
        assert (
            rm.main(
                [
                    str(task),
                    "--repo",
                    str(tmp_path),
                    "--agent",
                    "claude",
                    "--model",
                    "claude-sonnet-5",
                ]
            )
            == 3
        )
    finally:
        rm.execute = original
    agent, model, tools = seen[0]
    assert agent == "claude" and model == "claude-sonnet-5"
    assert tools == rm.DEFAULT_CLAUDE_TOOLS
    assert "default Claude allowlist" in capsys.readouterr().err


def test_effort_is_always_explicit_and_recorded() -> None:
    """No effort in TASK.md -> the harness passes its documented default and records the source."""
    import agent_arena.run_mode as rm

    assert rm.DEFAULT_EFFORT == {"claude": "high", "codex": "medium"}
    spec = rm.TaskSpec(
        path=Path("t.md"),
        name="t",
        agent="codex",
        model="gpt-6-sol",
        instructions="x",
        allowed_tools=(),
        max_turns=1,
        timeout_seconds=5,
        checks=(),
    )
    resolved = rm.replace(
        spec, effort=rm.DEFAULT_EFFORT[spec.agent], effort_source="harness default"
    )
    argv = rm.codex_argv("codex", resolved, Path("/tmp"))
    assert 'model_reasoning_effort="medium"' in argv


def test_full_access_network_and_subagents_flags(tmp_path: Path) -> None:
    import agent_arena.run_mode as rm

    spec = parse_task(
        _task(tmp_path, PASS_CHECK, "network = true\nfull_access = true\nsubagents = true")
    )
    codex = rm.codex_argv("codex", spec, tmp_path)
    assert codex[codex.index("--sandbox") + 1] == "danger-full-access"
    assert "--enable" in codex and "multi_agent" in codex
    claude = rm.claude_argv("claude", spec)
    assert claude[claude.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--allowedTools" not in claude
    net_only = parse_task(_task(tmp_path, PASS_CHECK, "network = true"))
    argv = rm.codex_argv("codex", net_only, tmp_path)
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=true" in argv
    assert "FULL ACCESS" in rm.enforcement(spec)["tools"]
