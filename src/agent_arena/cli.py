from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .audit import verify_manifest
from .config import ArenaConfig, load_config, with_model_overrides
from .errors import ArenaError
from .integration import integrate_winner
from .orchestrator import ArenaOrchestrator


def _add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--codex-model", metavar="MODEL", help="override the Codex/OpenAI model for this invocation"
    )
    parser.add_argument(
        "--claude-model",
        metavar="MODEL",
        help="override the Claude/Anthropic model for this invocation",
    )


def _invocation_config(args: argparse.Namespace) -> ArenaConfig:
    return with_model_overrides(
        load_config(args.config), codex_model=args.codex_model, claude_model=args.claude_model
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task", help="task Markdown file, or an inline task string")
    parser.add_argument(
        "--repo", default=".", help="source Git repository (default: current directory)"
    )
    parser.add_argument("--config", default="team.toml", help="TOML configuration file")
    parser.add_argument("--base-ref", default="HEAD", help="commit/ref to freeze (default: HEAD)")
    parser.add_argument("--json", action="store_true", help="print machine-readable final output")
    _add_model_arguments(parser)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="team",
        description="Run Codex and Claude as isolated, adversarial engineering competitors.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    hack = commands.add_parser("hack", help="one fast, demo-first adversarial cycle")
    _add_run_arguments(hack)
    engineer = commands.add_parser(
        "engineer", help="deeper production-quality adversarial engineering"
    )
    _add_run_arguments(engineer)

    doctor = commands.add_parser("doctor", help="validate configuration and provider availability")
    doctor.add_argument("--config", default="team.toml")
    doctor.add_argument("--mode", choices=("hackathon", "engineering"), default="hackathon")
    doctor.add_argument("--json", action="store_true")
    _add_model_arguments(doctor)

    for name, help_text in (
        ("status", "show the latest run state"),
        ("results", "show the latest run report"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("run", nargs="?", help="run directory; defaults to latest")
        command.add_argument("--config", default="team.toml")
        command.add_argument("--json", action="store_true")

    open_command = commands.add_parser("open", help="open a winner patch or human report")
    open_command.add_argument("artifact", choices=("winner", "report"))
    open_command.add_argument("run", nargs="?", help="run directory; defaults to latest")
    open_command.add_argument("--config", default="team.toml")
    open_command.add_argument("--print-only", action="store_true")

    verify = commands.add_parser("verify", help="verify the audit artifact hash manifest")
    verify.add_argument("run", help="run directory")
    verify.add_argument("--json", action="store_true")

    integrate = commands.add_parser(
        "integrate",
        help="create a new branch/worktree containing exactly the frozen winner tree",
    )
    integrate.add_argument("run", help="completed run directory")
    integrate.add_argument("--source", required=True, help="original source repository")
    integrate.add_argument("--branch", required=True, help="new integration branch name")
    integrate.add_argument("--worktree", required=True, help="new integration worktree path")
    integrate.add_argument("--json", action="store_true")
    return parser


def _latest_run(config: ArenaConfig) -> Path:
    root = config.run.artifact_root
    candidates = (
        sorted(
            (path for path in root.iterdir() if path.is_dir() and (path / "run.json").is_file()),
            reverse=True,
        )
        if root.is_dir()
        else []
    )
    if not candidates:
        raise ArenaError(f"no runs found under {root}")
    return candidates[0]


def _resolve_run(value: str | None, config_path: str) -> Path:
    if value:
        path = Path(value).expanduser().resolve()
        if not (path / "run.json").is_file():
            raise ArenaError(f"not an Agent Arena run directory: {path}")
        return path
    return _latest_run(load_config(config_path))


def _run_task(args: argparse.Namespace, mode: str) -> int:
    config = _invocation_config(args)
    orchestrator = ArenaOrchestrator(config, mode)
    supplied = Path(args.task).expanduser()
    if supplied.is_file():
        run_dir = orchestrator.run(Path(args.repo), supplied, args.base_ref)
    else:
        with tempfile.TemporaryDirectory(prefix="agent-arena-task-") as temporary:
            task_path = Path(temporary) / "task.md"
            task_path.write_text(args.task.strip() + "\n", encoding="utf-8")
            run_dir = orchestrator.run(Path(args.repo), task_path, args.base_ref)
    run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    decision_path = run_dir / "decision.json"
    decision = (
        json.loads(decision_path.read_text(encoding="utf-8")) if decision_path.exists() else {}
    )
    payload = {
        "run_dir": str(run_dir),
        "state": run_data.get("state"),
        "winner": decision.get("winner"),
        "report": str(run_dir / "report.md"),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Run: {payload['run_dir']}")
        print(f"State: {payload['state']}")
        print(f"Winner: {payload['winner'] or 'none'}")
        print(f"Report: {payload['report']}")
    return 0 if payload["winner"] else 3


def _open_path(path: Path, print_only: bool) -> None:
    print(path)
    if print_only:
        return
    opener = shutil.which("open") or shutil.which("xdg-open")
    if not opener:
        return
    subprocess.Popen(
        [opener, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "hack":
            return _run_task(args, "hackathon")
        if args.command == "engineer":
            return _run_task(args, "engineering")
        if args.command == "doctor":
            config = _invocation_config(args)
            result = ArenaOrchestrator(config, args.mode).doctor()
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print("READY" if result["ok"] else "NOT READY")
                for engineer_id, details in result["engineers"].items():
                    print(f"{engineer_id}: {'OK' if details['ok'] else details['error']}")
            return 0 if result["ok"] else 2
        if args.command in {"status", "results"}:
            run_dir = _resolve_run(args.run, args.config)
            if args.command == "status":
                data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
                if args.json:
                    print(json.dumps(data, indent=2, sort_keys=True))
                else:
                    print(f"{data['run_id']}: {data['state']}")
                    if data.get("error"):
                        print(f"Error: {data['error']}")
            else:
                report = run_dir / "report.md"
                if args.json:
                    decision = run_dir / "decision.json"
                    print(decision.read_text(encoding="utf-8") if decision.exists() else "{}")
                else:
                    print(report.read_text(encoding="utf-8"))
            return 0
        if args.command == "open":
            run_dir = _resolve_run(args.run, args.config)
            target = run_dir / ("winner.patch" if args.artifact == "winner" else "report.md")
            if not target.is_file():
                raise ArenaError(f"run has no {args.artifact} artifact: {target}")
            _open_path(target, args.print_only)
            return 0
        if args.command == "verify":
            manifest = verify_manifest(Path(args.run).expanduser().resolve())
            if args.json:
                print(json.dumps(manifest, indent=2, sort_keys=True))
            else:
                print(f"OK: {len(manifest['files'])} audited files match their SHA-256 hashes")
            return 0
        if args.command == "integrate":
            receipt = integrate_winner(
                Path(args.run), Path(args.source), args.branch, Path(args.worktree)
            )
            if args.json:
                print(json.dumps(receipt, indent=2, sort_keys=True))
            else:
                print(f"Created {receipt['branch']} at {receipt['worktree']}")
                print(f"Commit: {receipt['integration_commit']}")
                print("Nothing was pushed or merged into the original branch.")
            return 0
        raise ArenaError(f"unknown command: {args.command}")
    except (ArenaError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"team: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
