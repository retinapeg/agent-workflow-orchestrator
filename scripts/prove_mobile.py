"""Prepare and prove real phone/API -> planner -> Claude/Codex -> checks -> main.

All source writes are in an isolated copy of this repository. Provider usage is
explicitly opted into with ARENA_LIVE=1. No production branch or remote is pushed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from agent_arena.control.__main__ import add_account
from agent_arena.control.diagnostics import doctor
from agent_arena.control.engine import Engine, git
from agent_arena.control.store import Store

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ["src/agent_arena/control/configuration.py", "web/public/manifest.webmanifest"]
INSTRUCTIONS = """Make this app easier to recognise on my phone, and stop malformed server
addresses getting through. Keep the existing behaviour working."""


def prepare(home, label=""):
    store = Store(home)
    for provider in ("codex", "claude"):
        if not any(a["provider"] == provider for a in store.rows("accounts")):
            add_account(store, provider.title(), provider)
    name = ("Orchestrator live proof " + label).strip()
    suffix = "-" + label if label else ""
    existing = [p for p in store.rows("projects") if p["name"] == name]
    if existing:
        return store, existing[0]
    source = store.home / ("proof-source" + suffix)
    if source.exists():
        raise ValueError("Proof source exists without enrollment; preserved for inspection")
    git(ROOT, "clone", "--no-hardlinks", "--", str(ROOT), str(source))
    git(source, "remote", "remove", "origin")
    git(source, "branch", "-M", "main")
    # Snapshot current work without stashing, resetting, or modifying the source checkout.
    paths = git(ROOT, "ls-files", "-c", "-o", "--exclude-standard").splitlines()
    # Workers refuse projects carrying agent startup configuration; keep it out of the snapshot.
    agent_config = ("AGENTS.md", "CLAUDE.md")
    for relative in set(paths):
        if relative in agent_config or relative.startswith((".claude/", ".codex/")):
            continue
        original = ROOT / relative
        destination = source / relative
        if original.is_symlink():
            raise ValueError("Review source symlink before snapshot: " + relative)
        if original.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        elif destination.is_file():
            destination.unlink()  # Reflect a tracked deletion only in the isolated snapshot.
    hashes = {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in SCOPE}
    acceptance = source / "acceptance"
    acceptance.mkdir(exist_ok=True)
    checker = """import hashlib, json, runpy
from pathlib import Path
BASELINES = BASELINES_LITERAL
# Untouched peer files are allowed while independent candidates compose.
# Any changed file must satisfy its complete new contract.
for name, baseline in BASELINES.items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() == baseline:
        continue
    if name.endswith("configuration.py"):
        validate = runpy.run_path(name)["validate_origin"]
        assert validate("https://example.com") == "https://example.com"
        assert validate("http://127.0.0.1:8790") == "http://127.0.0.1:8790"
        for bad in ["https://exa mple.com", "https://example.com\\\\evil",
                    "https://example.com\\n", "\\thttps://example.com"]:
            try: validate(bad)
            except ValueError: pass
            else: raise AssertionError("Accepted unsafe origin " + repr(bad))
    else:
        manifest = json.loads(Path(name).read_text())
        assert manifest["description"] == "Control your private engineering agents from your phone."
        assert manifest["lang"] == "en"
        assert manifest["start_url"] == "/" and manifest["icons"]
print("Changed-file contracts and baseline compatibility passed")
""".replace("BASELINES_LITERAL", repr(hashes))
    (acceptance / "proof.py").write_text(checker)
    git(source, "add", "--all")
    git(source, "commit", "-qm", "Preserved orchestrator snapshot with frozen live proof checks")
    project = Engine(store).enroll(
        name,
        str(source),
        SCOPE,
        [
            {
                "name": "Frozen origin and manifest contracts",
                "argv": ["{python}", "acceptance/proof.py"],
                "timeout": 30,
            }
        ],
        ["acceptance"],
        trusted=True,
    )
    (store.home / f"proof-input{suffix}.json").write_text(
        json.dumps(
            {
                "project_id": project["id"],
                "instructions": INSTRUCTIONS,
                "scope": SCOPE,
                "baselines": hashes,
            },
            indent=2,
        )
    )
    return store, project


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=ROOT / ".arena-control")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8791")
    parser.add_argument("--label", default="", help="fresh snapshot project name suffix")
    args = parser.parse_args()
    store, project = prepare(args.home, args.label)
    if args.prepare_only:
        print(
            json.dumps({"project": project["id"], "source": project["repo"], "state": "prepared"})
        )
        return 0
    if os.environ.get("ARENA_LIVE") != "1":
        raise ValueError("Set ARENA_LIVE=1 to run real subscription agents")
    preflight = doctor(store)
    if not preflight["execution_ready"]:
        target = ROOT / "docs/build-evidence/live-proof-blocked.json"
        target.write_text(json.dumps(preflight, indent=2) + "\n")
        print("LIVE PROOF BLOCKED: " + str(target))
        return 2
    address = urlsplit(args.url)
    try:
        running = httpx.get(args.url, timeout=2).status_code == 200
    except httpx.HTTPError:
        running = False
    if not running:
        if address.hostname != "127.0.0.1" or address.scheme != "http":
            raise ValueError("Start the private HTTPS service before testing its URL")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_arena.control",
                "--home",
                str(store.home),
                "start",
                "--port",
                str(address.port or 8791),
                "--origin",
                args.url,
            ],
            cwd=ROOT,
            check=True,
        )
        for _ in range(40):
            time.sleep(0.25)
            try:
                if httpx.get(args.url, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
        else:
            raise ValueError("Service did not start; inspect " + str(store.home / "server.log"))
    # The browser driver creates the one-use code locally and submits the goal.
    env = dict(
        os.environ,
        ARENA_HOME=str(store.home),
        ARENA_URL=args.url,
        ARENA_PROJECT=project["id"],
        ARENA_INSTRUCTIONS=INSTRUCTIONS,
        ARENA_PROOF_INPUT=str(
            store.home / ("proof-input" + ("-" + args.label if args.label else "") + ".json")
        ),
    )
    subprocess.run(["node", "web/prove-mobile.mjs"], cwd=ROOT, env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
