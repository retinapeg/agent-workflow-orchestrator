"""Private Tailscale transport; application device pairing remains mandatory."""

import json
import shutil
import subprocess

from .configuration import validate_origin
from .engine import atomic_json


def tailscale(*args):
    executable = shutil.which("tailscale")
    if not executable:
        raise ValueError("Install and sign in to Tailscale on this Mac first")
    result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout)[-2000:])
    return result.stdout


def remote_status():
    try:
        output = tailscale("status", "--json")
        try:
            status = json.loads(output)
        except ValueError:
            raise ValueError(output.strip() or "Tailscale returned no status") from None
        name = status.get("Self", {}).get("DNSName", "").rstrip(".")
        if status.get("BackendState") != "Running" or not name.endswith(".ts.net"):
            return {"ready": False, "reason": "Sign in to Tailscale on the Mac and phone"}
        return {"ready": True, "origin": validate_origin("https://" + name)}
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "reason": str(exc)}


def configure_remote(store, port):
    status = remote_status()
    if not status["ready"]:
        raise ValueError(status["reason"])
    config = json.loads(tailscale("serve", "status", "--json") or "{}")
    host = status["origin"].removeprefix("https://") + ":443"
    if config.get("AllowFunnel", {}).get(host):
        raise ValueError("This Tailscale endpoint has public Funnel enabled; disable it first")
    target = f"http://127.0.0.1:{port}"
    handlers = config.get("Web", {}).get(host, {}).get("Handlers", {})
    if handlers and handlers.get("/", {}).get("Proxy") != target:
        raise ValueError("Tailscale HTTPS already serves another application; nothing replaced")
    tailscale("serve", "--bg", "--https=443", target)
    settings = {"origin": status["origin"], "port": port, "transport": "tailscale-private"}
    atomic_json(store.home / "connection.json", settings)
    with store.transaction() as db:
        store.event(db, "remote.configured", **settings)
    return settings
