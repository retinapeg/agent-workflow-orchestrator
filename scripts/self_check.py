"""Frozen acceptance check when Arena builds itself.

Runs inside the controller's verification sandbox with the candidate workspace as the
current directory. The candidate's own `src/` is put first on PYTHONPATH so the tests
exercise the candidate, not the installed (live) package. The canonical checkout's
`web/node_modules` is linked read-only so the UI type-checks and builds without network.
Exit 1 on any failure; concise output names the failing stage.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

WORKSPACE = Path.cwd().resolve()
CANONICAL = Path(__file__).resolve().parents[1]
PY = sys.executable
FAST_TESTS = [
    "tests/control/test_routing.py",
    "tests/control/test_director.py",
    "tests/control/test_safety_fixes.py",
    "tests/control/test_completion.py",
    "tests/control/test_control.py",
]


def stage(name, argv, env=None, cwd=None, timeout=420):
    print(f"[self-check] {name}: {' '.join(str(a) for a in argv)}", flush=True)
    merged = {**os.environ, **(env or {})}
    merged["PYTHONPATH"] = str(WORKSPACE / "src")
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    merged["ARENA_INSIDE_SANDBOX"] = "1"  # candidate tests must not nest sandbox-exec
    # npm must not write caches or logs into the candidate tree (HOME is the workspace here).
    merged["npm_config_cache"] = os.path.join(tempfile.gettempdir(), "arena-npm-cache")
    merged["npm_config_update_notifier"] = "false"
    result = subprocess.run(
        argv, cwd=cwd or WORKSPACE, env=merged, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        tail = (result.stdout + result.stderr)[-6000:]
        print(f"[self-check] FAIL {name} (exit {result.returncode})\n{tail}", flush=True)
        raise SystemExit(1)
    print(f"[self-check] ok {name}", flush=True)


def main():
    if not (WORKSPACE / "src/agent_arena/control/engine.py").is_file():
        print("[self-check] not an Arena workspace", flush=True)
        raise SystemExit(1)
    stage("lint", [PY, "-m", "ruff", "check", "src", "tests", "scripts"])
    stage(
        "format",
        [PY, "-m", "ruff", "format", "--check", "src/agent_arena/control", "tests/control"],
    )
    stage(
        "candidate imports",
        [
            PY,
            "-c",
            "import agent_arena.control.api, agent_arena.control.worker, "
            "agent_arena.control.activation",
        ],
    )
    existing = [t for t in FAST_TESTS if (WORKSPACE / t).is_file()]
    stage(
        "controller tests",
        [PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *existing],
        timeout=540,
    )
    modules = WORKSPACE / "web/node_modules"
    linked = False
    source_modules = (
        Path(sys.argv[sys.argv.index("--node-modules") + 1])
        if "--node-modules" in sys.argv
        else CANONICAL / "web/node_modules"
    )
    if not modules.exists() and source_modules.is_dir():
        modules.symlink_to(source_modules)
        linked = True
    try:
        if modules.exists():
            npx = Path(sys.executable).parent / "npx"
            npx = "npx" if not npx.exists() else str(npx)
            stage("typescript", [npx, "tsc", "--noEmit"], cwd=WORKSPACE / "web")
            stage("ui build", [npx, "vite", "build"], cwd=WORKSPACE / "web")
        else:
            print("[self-check] web/node_modules unavailable; UI stages skipped", flush=True)
    finally:
        # Leave the candidate tree exactly as found: the link is not part of the change.
        if linked:
            modules.unlink()
    print("[self-check] all stages passed", flush=True)


if __name__ == "__main__":
    main()
