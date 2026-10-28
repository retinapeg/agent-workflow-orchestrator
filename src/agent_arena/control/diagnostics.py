"""Report host restrictions before spending a provider invocation."""

import json
import os
import subprocess
import sys

from .engine import identity
from .remote import remote_status
from .worker import auth_status, sandbox_check


def doctor(store):
    checks = []
    try:
        identity(os.getpid())
        checks.append({"name": "process_ownership", "ok": True})
    except Exception as exc:
        checks.append({"name": "process_ownership", "ok": False, "reason": str(exc)})
    try:
        result = subprocess.run(
            sandbox_check([sys.executable, "-c", "print('sandbox ready')"], store.home),
            capture_output=True,
            text=True,
            timeout=10,
        )
        checks.append(
            {
                "name": "verification_sandbox",
                "ok": result.returncode == 0,
                "reason": (result.stdout + result.stderr).strip(),
            }
        )
    except Exception as exc:
        checks.append({"name": "verification_sandbox", "ok": False, "reason": str(exc)})
    for account in store.rows("accounts"):
        try:
            ok, _ = auth_status(account)
            checks.append({"name": account["name"], "ok": ok, "provider": account["provider"]})
        except Exception as exc:
            checks.append({"name": account["name"], "ok": False, "reason": str(exc)})
    return {
        "execution_ready": bool(store.rows("accounts")) and all(c["ok"] for c in checks),
        "checks": checks,
        "remote": remote_status(),
    }


CLAUDE_PROBES = (
    ("claude-haiku-5-5", "low"),
    ("sonnet", "medium"),
    ("claude-fable-5-1", "high"),
    ("claude-fable-5-1", "max"),
)


def probe_claude_capabilities(account, env, models=CLAUDE_PROBES):
    """One tiny tool-free invocation per model/effort; records only what the CLI confirms."""
    validated = {}
    for model, effort in models:
        argv = [
            account["executable"],
            "-p",
            "--model",
            model,
            "--effort",
            effort,
            "--max-turns",
            "1",
            "--tools",
            "",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--settings",
            '{"disableAllHooks":true}',
            "--disable-slash-commands",
            "Reply with exactly the word OK.",
        ]
        try:
            result = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=120)
            data = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
        usage = data.get("modelUsage")
        # Aliases such as "sonnet" resolve to a dated model id; any non-error completion
        # under this model/effort request proves the CLI accepts the pair.
        accepted = result.returncode == 0 and not data.get("is_error")
        if accepted and isinstance(usage, dict) and usage:
            validated.setdefault(model, []).append(effort)
    return validated
