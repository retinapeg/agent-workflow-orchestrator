"""Real HTTP/API browser test fixture, with dispatch deliberately disabled."""

import hashlib
import os
import time
from pathlib import Path

from agent_arena.control.api import make_app
from agent_arena.control.engine import Engine, atomic_json, git
from agent_arena.control.store import Store


def create_app():
    home = Path(os.environ["ARENA_UI_STATE"]).resolve()
    store = Store(home)
    source = home / "source"
    if not source.exists():
        source.mkdir()
        (source / "src").mkdir()
        (source / "acceptance").mkdir()
        (source / "src/example.txt").write_text("Browser API test fixture\n")
        (source / "acceptance/check.py").write_text('print("UI fixture only")\n')
        git(source, "init", "-b", "main")
        git(source, "add", ".")
        git(source, "commit", "-qm", "Browser fixture")
        Engine(store).enroll(
            "Browser API fixture",
            str(source),
            ["src"],
            [{"name": "Fixture", "argv": ["{python}", "acceptance/check.py"]}],
            ["acceptance"],
            True,
        )
    atomic_json(
        home / "pairing.json",
        {
            "hash": hashlib.sha256(b"ui-fixture-one-use-code").hexdigest(),
            "expires": time.time() + 600,
        },
    )
    with store.transaction() as db:
        db.execute("UPDATE meta SET value='true' WHERE key='paused'")
    return make_app(
        home,
        origin="http://127.0.0.1:" + os.environ.get("ARENA_UI_PORT", "8792"),
        run_scheduler=False,
    )
