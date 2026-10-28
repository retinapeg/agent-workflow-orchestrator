from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .configuration import validate_origin
from .engine import Engine, atomic_json, git, identity
from .store import Store, encode, uid
from .worker import auth_status

DEFAULT_HOME = Path.home() / ".local/share/agent-arena/build-control"


def add_account(store, name, provider, config_home=None, model=None):
    executable = shutil.which(provider)
    if not executable:
        raise ValueError(f"Install {provider} before enrolling this slot")
    config = str(
        Path(config_home or (Path.home() / (".codex" if provider == "codex" else ".claude")))
        .expanduser()
        .resolve()
    )
    if not Path(config).is_dir():
        raise ValueError(
            "Config home must already exist; authenticate using the provider's supported login flow"
        )
    existing = store.rows("accounts")
    if len(existing) >= 3:
        raise ValueError("This release supports at most three enrolled slots")
    if any(a["provider"] == provider and a["config_home"] == config for a in existing):
        raise ValueError("This account/config home already owns a slot")
    if provider == "claude" and any(a["provider"] == "claude" for a in existing):
        raise ValueError(
            "A second Claude slot is disabled until independent macOS "
            "keychain isolation is demonstrated"
        )
    version = subprocess.check_output([executable, "--version"], text=True, timeout=10).strip()
    data = dict(
        name=name,
        provider=provider,
        config_home=config,
        executable=executable,
        version=version,
        model=model or ("gpt-6-sol" if provider == "codex" else "sonnet"),
        effort="medium",
        enabled=False,
        health="needs_auth",
        cooldown_until=0,
        quota=None,
        reset_time=None,
        roles=["build", "review", "plan"],
        created=time.time(),
    )
    ok, _ = auth_status(data)
    data.update(
        health="ready" if ok else "needs_auth",
        enabled=ok,
        last_health=time.time(),
        reason="Subscription login verified; Claude uses validated tool-free file operations"
        if provider == "claude"
        else "ChatGPT login verified; workspace-write sandbox",
    )
    ident = uid("account")
    with store.transaction() as db:
        db.execute("INSERT INTO accounts(id,data) VALUES(?,?)", (ident, encode(data)))
        store.event(db, "account.enrolled", account_id=ident, provider=provider)
    return {"id": ident, "name": name, "health": data["health"], "roles": data["roles"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arena mobile build control")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ["serve", "start"]:
        p = sub.add_parser(command)
        p.add_argument("--port", type=int)
        p.add_argument("--origin", default=None)
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("pair")
    sub.add_parser("revoke-sessions")
    p = sub.add_parser("check-accounts")
    p.add_argument("--probe", action="store_true", help="validate Fable 5.1 max on Claude slots")
    sub.add_parser("doctor")
    sub.add_parser("remote-status")
    p = sub.add_parser("configure-remote")
    p.add_argument("--port", type=int)
    p = sub.add_parser("install-service")
    p.add_argument("--port", type=int)
    p.add_argument("--origin")
    sub.add_parser("uninstall-service")
    p = sub.add_parser("enroll-account")
    p.add_argument("name")
    p.add_argument("--provider", choices=["codex", "claude"], required=True)
    p.add_argument("--config-home")
    p.add_argument("--model")
    p = sub.add_parser("enroll-project")
    p.add_argument("--manifest", type=Path, required=True)
    sub.add_parser("create-sandbox")
    p = sub.add_parser("backup")
    p.add_argument("destination", type=Path)
    p = sub.add_parser("service-plist")
    p.add_argument("destination", type=Path)
    p.add_argument("--port", type=int)
    p.add_argument("--origin")
    p.add_argument("--keep-awake", action="store_true")
    args = parser.parse_args(argv)
    connection_path = args.home.expanduser() / "connection.json"
    connection = json.loads(connection_path.read_text()) if connection_path.exists() else {}
    if hasattr(args, "port"):
        args.port = args.port or connection.get("port", 8790)
    if hasattr(args, "port") and not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if hasattr(args, "origin"):
        args.origin = validate_origin(
            args.origin or connection.get("origin") or f"http://127.0.0.1:{args.port}"
        )
    os.umask(0o077)
    store = Store(args.home)
    if args.command == "doctor":
        from .diagnostics import doctor

        report = doctor(store)
        print(json.dumps(report, indent=2))
        return 0 if report["execution_ready"] else 1
    elif args.command == "remote-status":
        from .remote import remote_status

        print(encode(remote_status()))
    elif args.command == "configure-remote":
        from .remote import configure_remote

        print(encode(configure_remote(store, args.port)))
    elif args.command == "install-service":
        from .diagnostics import doctor

        report = doctor(store)
        if not report["execution_ready"]:
            raise ValueError("Host preflight failed: " + encode(report["checks"]))
        destination = store.home / "control.plist"
        if destination.exists():
            existing = plistlib.loads(destination.read_bytes())
            expected = [
                "/usr/bin/caffeinate",
                "-i",
                sys.executable,
                "-m",
                "agent_arena.control",
                "--home",
                str(store.home),
                "serve",
                "--port",
                str(args.port),
                "--origin",
                args.origin,
            ]
            if existing["ProgramArguments"] != expected:
                raise ValueError("Existing service settings differ; preserved for review")
        else:
            main(
                [
                    "--home",
                    str(store.home),
                    "service-plist",
                    str(destination),
                    "--port",
                    str(args.port),
                    "--origin",
                    args.origin,
                    "--keep-awake",
                ]
            )
        label = plistlib.loads(destination.read_bytes())["Label"]
        launch_agent = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if launch_agent.exists() or launch_agent.is_symlink():
            if not launch_agent.is_symlink() or launch_agent.resolve() != destination:
                raise ValueError("An unrelated login service occupies this path; nothing replaced")
        else:
            launch_agent.parent.mkdir(parents=True, exist_ok=True)
            launch_agent.symlink_to(destination)
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(launch_agent)],
            check=True,
            timeout=20,
        )
        print("Login service installed; controller restarts automatically and keeps the Mac awake")
    elif args.command == "uninstall-service":
        destination = store.home / "control.plist"
        if not destination.is_file():
            raise ValueError("No service plist belongs to this state directory")
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)], check=True, timeout=20
        )
        label = plistlib.loads(destination.read_bytes())["Label"]
        launch_agent = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        if launch_agent.is_symlink() and launch_agent.resolve() == destination:
            launch_agent.unlink()
        print("Login service unloaded; state and worker evidence preserved")
    elif args.command == "serve":
        import uvicorn

        from .api import make_app

        atomic_json(
            store.home / "server.json",
            {**identity(os.getpid()), "port": args.port, "home": str(store.home)},
        )
        uvicorn.run(
            make_app(store.home, args.origin or f"http://127.0.0.1:{args.port}"),
            host="127.0.0.1",
            port=args.port,
            workers=1,
            proxy_headers=False,
            access_log=False,
            # Open event streams must not block a restart; clients reconnect on their own.
            timeout_graceful_shutdown=5,
        )
    elif args.command == "start":
        path = store.home / "server.json"
        if path.exists():
            data = json.loads(path.read_text())
            try:
                p = psutil.Process(data["pid"])
                if (
                    p.status() != psutil.STATUS_ZOMBIE
                    and abs(p.create_time() - data["created"]) < 0.01
                ):
                    raise ValueError("Service already running")
            except psutil.NoSuchProcess:
                pass
        command = [
            sys.executable,
            "-m",
            "agent_arena.control",
            "--home",
            str(store.home),
            "serve",
            "--port",
            str(args.port),
        ]
        if args.origin:
            command += ["--origin", args.origin]
        with (store.home / "server.log").open("ab") as log:
            p = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
            )
        print(
            f"Starting PID {p.pid}; http://127.0.0.1:{args.port}; logs {store.home / 'server.log'}"
        )
    elif args.command == "stop":
        data = json.loads((store.home / "server.json").read_text())
        p = psutil.Process(data["pid"])
        if abs(p.create_time() - data["created"]) >= 0.01 or str(store.home) not in p.cmdline():
            raise ValueError("Service process identity mismatch; no signal sent")
        p.send_signal(signal.SIGTERM)
        print(
            "Service stop requested. Bounded workers retain ownership; "
            "use Stop all in the UI to cancel them."
        )
    elif args.command == "status":
        print(
            encode(
                {
                    "home": str(store.home),
                    "projects": len(store.rows("projects")),
                    "tasks": len(store.rows("tasks")),
                    "accounts": [
                        {"name": a["name"], "health": a["health"]} for a in store.rows("accounts")
                    ],
                }
            )
        )
    elif args.command == "pair":
        code = secrets.token_urlsafe(18)
        atomic_json(
            store.home / "pairing.json",
            {"hash": hashlib.sha256(code.encode()).hexdigest(), "expires": time.time() + 600},
        )
        print(code)
    elif args.command == "revoke-sessions":
        with store.transaction() as db:
            db.execute("DELETE FROM sessions")
            store.event(db, "sessions.revoked")
        print("All paired sessions revoked")
    elif args.command == "enroll-account":
        print(encode(add_account(store, args.name, args.provider, args.config_home, args.model)))
    elif args.command == "check-accounts":
        for account in store.rows("accounts"):
            ok, env = auth_status(account)
            account.update(
                health="ready" if ok else "needs_auth", enabled=ok, last_health=time.time()
            )
            account["roles"] = ["build", "review", "plan"]
            if ok and args.probe and account["provider"] == "claude":
                from .diagnostics import probe_claude_capabilities

                account["model_capabilities"] = probe_claude_capabilities(account, env)
                account["capabilities_checked"] = time.time()
            with store.transaction() as db:
                db.execute(
                    "UPDATE accounts SET rev=rev+1,data=? WHERE id=?",
                    (
                        encode({k: v for k, v in account.items() if k not in {"id", "rev"}}),
                        account["id"],
                    ),
                )
                store.event(
                    db, "account.health", account_id=account["id"], health=account["health"]
                )
            print(account["name"], account["health"])
    elif args.command == "enroll-project":
        m = json.loads(args.manifest.read_text())
        print(encode(Engine(store).enroll(**m)))
    elif args.command == "create-sandbox":
        root = store.home / "sandbox-source"
        if root.exists():
            raise ValueError("Sandbox already exists; preserve it and its evidence")
        root.mkdir()
        (root / "src").mkdir()
        (root / "acceptance").mkdir()
        (root / "src/greeting.py").write_text('def greet(name):\n    return "TODO"\n')
        (root / "acceptance/check.py").write_text(
            "import runpy\ngreet = "
            'runpy.run_path("src/greeting.py")["greet"]\nassert '
            'greet("Ada") == "Hello, Ada!"\nassert greet("  Leo  ") == '
            '"Hello, Leo!"\nassert greet("") == "Hello, friend!"\nprint("3 '
            'greeting acceptance cases passed")\n'
        )
        git(root, "init", "-q")
        git(root, "add", "src", "acceptance")
        git(root, "commit", "-qm", "Disposable acceptance fixture")
        print(
            encode(
                Engine(store).enroll(
                    "Greeting sandbox",
                    str(root),
                    ["src/greeting.py"],
                    [
                        {
                            "name": "Greeting acceptance",
                            "argv": ["{python}", "acceptance/check.py"],
                            "timeout": 30,
                        }
                    ],
                    ["acceptance"],
                    trusted=True,
                )
            )
        )
    elif args.command == "backup":
        store.backup(args.destination)
        print("SQLite online backup complete")
    elif args.command == "service-plist":
        if args.destination.exists():
            raise ValueError("Destination exists; refusing to replace it")
        data = {
            "Label": "local.agent-arena.control."
            + hashlib.sha256(str(store.home).encode()).hexdigest()[:12],
            "ProgramArguments": [
                sys.executable,
                "-m",
                "agent_arena.control",
                "--home",
                str(store.home),
                "serve",
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "WorkingDirectory": str(Path(__file__).resolve().parents[3]),
            "StandardOutPath": str(store.home / "service.log"),
            "StandardErrorPath": str(store.home / "service.log"),
            "EnvironmentVariables": {"PATH": os.environ.get("PATH", os.defpath)},
        }
        data["ProgramArguments"] += ["--port", str(args.port), "--origin", args.origin]
        if args.keep_awake:
            data["ProgramArguments"] = ["/usr/bin/caffeinate", "-i", *data["ProgramArguments"]]
        args.destination.write_bytes(plistlib.dumps(data))
        print(
            "Plist generated, not installed. Load it with launchctl after stopping a manual server."
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, psutil.Error) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
