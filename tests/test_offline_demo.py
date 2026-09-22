from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_documented_offline_demo_selects_correct_candidate(tmp_path: Path) -> None:
    """The README's offline demo must run end to end and pick the correct candidate."""

    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "TMPDIR": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "examples/offline/run_demo.py"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "State: `COMPLETE`" in result.stdout
    assert "Winner: `codex`" in result.stdout
