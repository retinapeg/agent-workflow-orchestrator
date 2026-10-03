from __future__ import annotations

import csv
import json
from pathlib import Path

from agent_arena import ledger


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
