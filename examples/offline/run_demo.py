from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from agent_arena.audit import verify_manifest
from agent_arena.config import load_config
from agent_arena.orchestrator import ArenaOrchestrator


def git(repo: Path, *args: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Agent Arena Demo",
            "GIT_AUTHOR_EMAIL": "demo@localhost",
            "GIT_COMMITTER_NAME": "Agent Arena Demo",
            "GIT_COMMITTER_EMAIL": "demo@localhost",
        }
    )
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=environment)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="agent-arena-offline-demo-"))
    source = root / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    (source / "calculator.py").write_text(
        "def add(left: int, right: int) -> int:\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (source / "tests").mkdir()
    (source / "tests/test_calculator.py").write_text(
        "import unittest\nfrom calculator import add\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
        "        self.assertEqual(add(-2, 2), 0)\n",
        encoding="utf-8",
    )
    git(source, "add", ".")
    git(source, "commit", "-m", "offline demo baseline")

    example_dir = Path(__file__).resolve().parent
    config = root / "team.toml"
    config.write_text(
        f"""schema_version = 1
[run]
artifact_root = {json.dumps(str(root / "runs"))}
protected_paths = ["tests/**"]
[limits]
provider_timeout_seconds = 30
check_timeout_seconds = 30
max_output_bytes = 100000
max_prompt_bytes = 500000
max_diff_bytes = 500000
max_changed_files = 50
max_api_context_bytes = 200000
max_api_file_bytes = 100000
max_api_response_bytes = 500000
max_api_operations = 50
max_output_tokens = 1000
termination_grace_seconds = 1
[engineers.codex]
kind = "scripted"
[engineers.codex.options]
script = {json.dumps(str(example_dir / "codex.json"))}
[engineers.claude]
kind = "scripted"
[engineers.claude.options]
script = {json.dumps(str(example_dir / "claude.json"))}
[[checks]]
id = "tests"
category = "test"
command = ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]
required = true
weight = "100"
[modes.hackathon]
revision_rounds = 1
[modes.engineering]
revision_rounds = 2
[judge]
enabled = false
""",
        encoding="utf-8",
    )
    task = root / "task.md"
    task.write_text("Implement add so it returns the mathematical sum for all integers.\n")
    run_dir = ArenaOrchestrator(load_config(config), "hackathon").run(source, task)
    verify_manifest(run_dir)
    print((run_dir / "report.md").read_text(encoding="utf-8"))
    print(f"\nAudit directory retained at: {run_dir}")


if __name__ == "__main__":
    main()
