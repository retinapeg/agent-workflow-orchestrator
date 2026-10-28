"""Narrow host acceptance entrypoint; keeps OS and provider sandboxes enabled.

Run from Terminal as the signed-in macOS user. No sudo, credential export,
public tunnel, provider security override, or unrelated project access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from agent_arena.control.diagnostics import doctor
from agent_arena.control.engine import git
from agent_arena.control.remote import configure_remote, remote_status
from scripts.prove_mobile import prepare

ROOT = Path(__file__).resolve().parents[1]
HOME_DIR = ROOT / ".arena-control"
EVIDENCE = ROOT / "docs/build-evidence/host-acceptance"
PORT = 8791


def run(argv, log=None, timeout=900, env=None):
    if log:
        with (EVIDENCE / log).open("w") as stream:
            result = subprocess.run(
                argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, env=env
            )
        if result.returncode:
            raise RuntimeError(f"Check failed: {log}; see {EVIDENCE / log}")
        print("PASS:", log, flush=True)
        return ""
    return subprocess.check_output(argv, cwd=ROOT, text=True, timeout=timeout, env=env).strip()


def control(*args):
    return run([sys.executable, "-m", "agent_arena.control", "--home", str(HOME_DIR), *args])


def main():
    os.umask(0o077)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    store, project = prepare(HOME_DIR)
    report = doctor(store)
    (EVIDENCE / "preflight.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["execution_ready"]:
        raise RuntimeError(
            "Host execution needs process-identity sysctl, sandbox-exec, Chromium Mach IPC, "
            "and existing subscription keychain access. Run this command in normal Terminal "
            "as your signed-in user; do not disable those protections. See preflight.json."
        )
    run(
        [
            str(ROOT / ".venv/bin/ruff"),
            "check",
            "src",
            "tests",
            "scripts/prove_mobile.py",
            "scripts/verify_on_host.py",
        ],
        "lint.txt",
    )
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_cancellation.py",
            "tests/test_process.py",
        ],
        "cancellation.txt",
    )
    run([sys.executable, "-m", "pytest", "-q"], "regression.txt")
    run(["npm", "run", "build", "--prefix", "web"], "frontend.txt")
    run(["node", "web/ui-acceptance.mjs"], "browser.txt")
    env = dict(os.environ, ARENA_LIVE="1")
    run([sys.executable, "scripts/prove_mobile.py"], "real-agents.txt", timeout=1500, env=env)
    live = json.loads((ROOT / "docs/build-evidence/complete-live/result.json").read_text())
    if not live.get("passed") or not live.get("concurrent") or not live.get("restarted"):
        raise RuntimeError("Live proof did not establish real concurrency and restart recovery")
    status = remote_status()
    if not status["ready"]:
        print("Complete the normal Tailscale sign-in on this Mac and on your phone.", flush=True)
        # The supported login flow may require the owner to finish in their browser.
        subprocess.run(["tailscale", "up", "--timeout=60s"], cwd=ROOT, timeout=70, check=False)
        if not remote_status()["ready"]:
            input("Finish Tailscale sign-in, then press Return to continue: ")
    connection = configure_remote(store, PORT)
    control("stop")
    for _ in range(120):
        try:
            httpx.get(f"http://127.0.0.1:{PORT}", timeout=1)
        except httpx.HTTPError:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("Manual service did not stop; no second service was started")
    # The generated plist stays inside this repository, with a state-specific label.
    control("install-service", "--port", str(PORT), "--origin", connection["origin"])
    for _ in range(120):
        try:
            response = httpx.get(connection["origin"] + "/api/v1/overview", timeout=3)
            if response.status_code == 401:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError(
            "Private HTTPS transport did not pass TLS + unauthenticated-denial check"
        )
    started = time.time()
    code = control("pair")
    print("\nPrivate phone URL:", connection["origin"])
    print("One-time pairing code (10 minutes):", code)
    print("\nOn your phone: connect Tailscale, turn Wi-Fi off, open this URL, and pair.")
    print("Select Orchestrator live proof. Submit:")
    print("  Rename the app on my phone home screen to Build Desk. Keep everything else working.")
    print("Keep review enabled. Inspect and accept the result, then tap Integrate into main.")
    print("Waiting for that new phone task to reach main (20 minute limit)...", flush=True)
    phone_task = None
    for _ in range(600):
        tasks = [
            t
            for t in store.rows("tasks")
            if t["project_id"] == project["id"]
            and t["created"] >= started
            and t.get("integrated_commit")
        ]
        if tasks:
            phone_task = tasks[-1]
            break
        time.sleep(2)
    if not phone_task:
        raise RuntimeError("Phone-to-main step not demonstrated; service remains available")
    with store.connect() as db:
        paired = db.execute(
            "SELECT count(*) FROM events WHERE kind='device.paired' AND time>=?", (started,)
        ).fetchone()[0]
    if not paired:
        raise RuntimeError("No new device pairing was recorded for the phone step")
    actual = git(Path(project["repo"]), "rev-parse", "main")
    manifest = json.loads(
        git(Path(project["repo"]), "show", "main:web/public/manifest.webmanifest")
    )
    if actual != phone_task["integrated_commit"] or manifest.get("short_name") != "Build Desk":
        raise RuntimeError("Phone task did not produce the requested main-branch result")
    confirmation = input("Was that submitted and inspected on your phone over cellular? [yes/no] ")
    if confirmation.strip().lower() != "yes":
        raise RuntimeError("Off-network phone verification not confirmed")
    result = {
        "passed": True,
        "private_origin": connection["origin"],
        "phone_task": phone_task,
        "source_main": actual,
        "phone_cellular_confirmed_by_owner": True,
        "real_agent_evidence": "../complete-live/result.json",
        "time": time.time(),
    }
    (EVIDENCE / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    # Explicit paths preserve unrelated files and refuse to include pre-existing staged work.
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
        raise RuntimeError("Verified; existing staged work preserved. Review before committing.")
    paths = [
        ".gitignore",
        "pyproject.toml",
        "src/agent_arena/process.py",
        "src/agent_arena/orchestrator.py",
        "src/agent_arena/control",
        "tests/conftest.py",
        "tests/test_cancellation.py",
        "tests/test_process.py",
        "tests/control",
        "web",
        "scripts/prove_mobile.py",
        "scripts/verify_on_host.py",
        "BUILD_STATE.md",
        "docs/MOBILE_BUILD_CONTROL.md",
        "docs/build-evidence",
    ]
    subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Complete private mobile orchestration with live phone-to-main evidence",
        ],
        cwd=ROOT,
        check=True,
    )
    print("VERIFIED PRODUCT:", connection["origin"])
    print("Review commit:", git(ROOT, "rev-parse", "HEAD"))
    print("Evidence:", EVIDENCE / "result.json")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
