from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Arena Test",
            "GIT_AUTHOR_EMAIL": "arena-test@localhost",
            "GIT_COMMITTER_NAME": "Arena Test",
            "GIT_COMMITTER_EMAIL": "arena-test@localhost",
        }
    )
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode())
    return result.stdout.decode().strip()


def write_script(path: Path, target: str, final_source: str) -> None:
    review = json.dumps(
        {
            "target": target,
            "summary": "Adversarial review completed.",
            "findings": [],
            "attack_tests": ["Try negative and zero inputs."],
        }
    )
    payload: dict[str, Any] = {
        "implement": {
            "text": "initial implementation",
            "operations": [
                {"op": "write", "path": "calculator.py", "content": final_source},
                {
                    "op": "write",
                    "path": "README.md",
                    "content": "# Calculator\n\n## Quick Start\n\nRun `python3 -m unittest`.\n",
                },
            ],
        },
        "review": {"text": review},
        "revise": [
            {
                "text": "revision one",
                "operations": [{"op": "write", "path": "calculator.py", "content": final_source}],
            },
            {
                "text": "revision two",
                "operations": [{"op": "write", "path": "calculator.py", "content": final_source}],
            },
        ],
        "judge": {
            "text": json.dumps({"winner": "candidate-a", "reason": "clearer", "confidence": 0.6})
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def arena_fixture(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    (source / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n", encoding="utf-8"
    )
    (source / "DEMO.md").write_text(
        "# Demo\n\n## Outcome\nReliable addition.\n\n## Beat 1\nShow 1 + 2 = 3.\n\n"
        "## Surprising moment\nNegative values work.\n\n## Not building\nNo UI.\n\n"
        "## Smoke command\n`python3 -m unittest`\n\n## Fallback\nUse the local fixture.\n",
        encoding="utf-8",
    )
    (source / "NOT.md").write_text(
        "# Not in scope\n\nNo UI, service, deployment, or unrelated refactor.\n",
        encoding="utf-8",
    )
    tests = source / "tests"
    tests.mkdir()
    (tests / "test_calculator.py").write_text(
        "import unittest\n"
        "from calculator import add\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
        "        self.assertEqual(add(-2, 2), 0)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "baseline")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    good_script = config_dir / "codex.json"
    weak_script = config_dir / "claude.json"
    write_script(
        good_script,
        "claude",
        "def add(left: int, right: int) -> int:\n    return left + right\n",
    )
    write_script(
        weak_script,
        "codex",
        "def add(left: int, right: int) -> int:\n    return left - right\n",
    )
    config = config_dir / "team.toml"
    config.write_text(
        f"""schema_version = 1

[run]
artifact_root = {json.dumps(str(tmp_path / "runs"))}
protected_paths = ["tests/**"]
allowed_paths = ["*.py", "*.md", "*.txt"]
max_workers = 2

[limits]
provider_timeout_seconds = 10
check_timeout_seconds = 10
max_output_bytes = 100000
max_prompt_bytes = 500000
max_diff_bytes = 500000
max_changed_files = 30
max_api_context_bytes = 200000
max_api_file_bytes = 100000
max_api_response_bytes = 500000
max_api_operations = 30
max_output_tokens = 1000
termination_grace_seconds = 1

[engineers.codex]
kind = "scripted"
[engineers.codex.options]
script = "codex.json"

[engineers.claude]
kind = "scripted"
[engineers.claude.options]
script = "claude.json"

[[checks]]
id = "tests"
category = "test"
command = ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]
required = true
weight = "100"

[modes.hackathon]
revision_rounds = 1
provider_timeout_seconds = 5
max_run_seconds = 60
min_response_bytes = 1
check_weights = {{ tests = "100" }}

[modes.engineering]
revision_rounds = 2
provider_timeout_seconds = 5
max_run_seconds = 60
min_response_bytes = 1
check_weights = {{ tests = "100" }}

[judge]
enabled = false
""",
        encoding="utf-8",
    )
    task = tmp_path / "task.md"
    task.write_text(
        "Fix add so it returns the mathematical sum for all integers.\n", encoding="utf-8"
    )
    return {
        "root": tmp_path,
        "source": source,
        "config": config,
        "task": task,
    }
