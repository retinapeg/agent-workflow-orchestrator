from __future__ import annotations

import json
import stat
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from agent_arena.audit import verify_manifest
from agent_arena.errors import ArenaError
from agent_arena.run_mode import (
    claude_argv,
    estimate,
    main,
    parse_claude_stream,
    parse_codex_stream,
    parse_task,
    recent_state_entries,
    reconcile_usage,
    totals,
)


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
        f'[models."{model}"]\ninput_per_mtok = 3\ncache_read_per_mtok = 0.3\n'
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


def test_estimate_is_null_when_a_counted_field_is_missing(tmp_path: Path) -> None:
    calls = parse_claude_stream("\n".join(json.dumps(e) for e in CLAUDE_STREAM))["calls"]
    from agent_arena.run_mode import load_pricing

    pricing, _ = load_pricing(_pricing(tmp_path))
    cost, warnings = estimate(calls, pricing)
    assert cost is None and any("cache_write tokens not reported" in w for w in warnings)
    cost, warnings = estimate(calls[:1], pricing)
    # 1000*3 + 500*3.75 + 20*15 per million
    assert str(cost) == "0.005175" and warnings == []


def test_codex_cached_input_is_not_double_billed(tmp_path: Path) -> None:
    stream = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 100},
        }
    )
    calls = parse_codex_stream(stream, "gpt-test")["calls"]
    assert calls[0]["model_source"] == "declared" and calls[0]["reasoning"] is None
    path = _pricing(tmp_path, "gpt-test")
    from agent_arena.run_mode import load_pricing

    pricing, _ = load_pricing(path)
    cost, warnings = estimate(calls, pricing)
    # reasoning not reported -> estimate refuses to guess
    assert cost is None and "reasoning tokens not reported" in warnings[0]
    pricing["gpt-test"]["reasoning"] = "included_in_output"
    cost, _ = estimate(calls, pricing)
    # (1000-400)*3 + 400*0.3 + 100*15 per million
    assert cost == Decimal("0.00342")


def test_unpriced_model_gives_null_cost_with_warning(tmp_path: Path) -> None:
    calls = parse_claude_stream("\n".join(json.dumps(e) for e in CLAUDE_STREAM))["calls"]
    cost, warnings = estimate(calls[:1], {})
    assert cost is None and "not in pricing.toml" in warnings[0]


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
    assert "CLI-reported cost:        $0.0123" in out
    assert "API-equivalent estimate:  null" in out  # call 2 lacks cache_write
    (run_dir,) = list(runs.iterdir())
    result = json.loads((run_dir / "result.json").read_text())
    assert result["usage"]["totals"]["cache_write"] is None
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
    assert "API-equivalent estimate:  $0.0052" in out
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
    assert "Ledger, 1 runs incl. failed: total estimate $0.0052" in capsys.readouterr().out
    excluded.write_text(f'[[excluded]]\nrun_id = "{first.name}"\nreason = "harness probe"\n')
    assert main(args) == 0
    out = capsys.readouterr().out
    # the excluded run adds neither a verified outcome nor its cost
    assert "Ledger, 1 runs incl. failed (1 excluded): total estimate $0.0052" in out
    assert "verified outcomes 1, cost per verified outcome $0.0052" in out

    excluded.write_text('[[excluded]]\nrun_id = "x"\n')
    assert main(args) == 3  # a bad exclusion file stops before the agent runs
    assert len(list(runs.iterdir())) == 2
