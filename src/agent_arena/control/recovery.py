"""Retain ownership until a crashed guardian's recorded child is stopped."""

import json
import os
import signal
import time
from pathlib import Path

import psutil

from .engine import atomic_json


def orphan_stopped(attempt):
    path = Path(attempt["artifact_dir"]) / "child.json"
    if not path.exists():
        # The guardian is already known to be dead. A CLI child registers itself within
        # seconds of spawning, so after a generous grace period with no registration there
        # is no owned child to stop; before that the window stays ambiguous.
        return not attempt.get("identity") or time.time() - attempt["created"] > 120
    try:
        child = json.loads(path.read_text())
        if child["attempt_id"] != attempt["id"] or child["token"] != attempt["token"]:
            return False
        process = psutil.Process(child["pid"])
        if (
            abs(process.create_time() - child["created"]) >= 0.01
            or abs(psutil.boot_time() - child["boot"]) >= 1
        ):
            return True
        if process.status() == psutil.STATUS_ZOMBIE:
            return True
        stopped = path.with_name("recovery-stop.json")
        if stopped.exists():
            requested = json.loads(stopped.read_text())
            if requested["child"] != child:
                return False
        else:
            requested = {"child": child, "time": time.time()}
            atomic_json(stopped, requested)
        os.killpg(
            child["pid"],
            signal.SIGKILL if time.time() - requested["time"] >= 3 else signal.SIGTERM,
        )
        return False
    except (psutil.NoSuchProcess, ProcessLookupError):
        return True
    except (psutil.AccessDenied, PermissionError, ValueError, KeyError):
        return False
