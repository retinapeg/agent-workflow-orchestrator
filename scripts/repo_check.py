"""Generic frozen acceptance check for an enrolled repository without its own harness.

Invoked by the controller inside its verification sandbox with the candidate workspace as
the current directory and `--source <enrolled repo>` pointing at the owner's checkout.
It links the source's `node_modules`/`.venv` read-only when present (no network inside the
sandbox), then runs what the repository declares: `npm run build` / `npm test` when
package.json defines them, pytest when a tests directory exists, ruff when configured.
Exit 1 on the first failure with a concise tail of the output.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def run(name, argv, cwd, env, timeout=540):
    print(f"[repo-check] {name}: {' '.join(argv)}", flush=True)
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        tail = (result.stdout + result.stderr)[-6000:]
        print(f"[repo-check] FAIL {name} (exit {result.returncode})\n{tail}", flush=True)
        raise SystemExit(1)
    print(f"[repo-check] ok {name}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", required=True, help="the enrolled repository (for node_modules/.venv)"
    )
    parser.add_argument(
        "--pytest-ignore",
        action="append",
        default=[],
        help="test paths that need the network or a display and cannot run in the sandbox",
    )
    args = parser.parse_args()
    workspace = Path.cwd().resolve()
    source = Path(args.source).resolve()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CI": "1"}
    ran = 0
    package = workspace / "package.json"
    if package.is_file():
        scripts = json.loads(package.read_text()).get("scripts", {})
        modules = workspace / "node_modules"
        if not modules.exists() and (source / "node_modules").is_dir():
            modules.symlink_to(source / "node_modules")
        if modules.exists():
            if "build" in scripts:
                run("npm run build", ["npm", "run", "build"], workspace, env)
                ran += 1
            if "test" in scripts and "no test specified" not in scripts["test"]:
                run("npm test", ["npm", "test"], workspace, env)
                ran += 1
        else:
            print(
                "[repo-check] package.json without node_modules in the source; JS stages skipped",
                flush=True,
            )
    python = sys.executable
    venv = source / ".venv/bin/python"
    if venv.exists():
        python = str(venv)
    if (workspace / "pyproject.toml").is_file() or (workspace / "tests").is_dir():
        pyenv = {
            **env,
            "PYTHONPATH": str(workspace / "src")
            if (workspace / "src").is_dir()
            else str(workspace),
        }
        if (workspace / "tests").is_dir():
            ignores = [f"--ignore={path}" for path in args.pytest_ignore]
            run(
                "pytest",
                [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *ignores],
                workspace,
                pyenv,
            )
            ran += 1
        if (workspace / "pyproject.toml").is_file() and "ruff" in (
            workspace / "pyproject.toml"
        ).read_text():
            run("ruff", [python, "-m", "ruff", "check", "."], workspace, pyenv)
            ran += 1
    if not ran:
        print(
            "[repo-check] no build or test commands declared; only scope and review gates apply",
            flush=True,
        )
    print("[repo-check] passed", flush=True)


if __name__ == "__main__":
    main()
