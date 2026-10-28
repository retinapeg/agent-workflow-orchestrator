"""Round 2 of the usable Arena: the phone experience, slice by slice."""

from __future__ import annotations

import json
import time

import pytest

from agent_arena.control import livetail
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client
from tests.control.test_usability_api import insert_attempt


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


# -- slice A: since you were away ---------------------------------------------------------


def test_session_reports_absence_until_marked_seen(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    assert client.get("/api/v1/session").json()["away_since"] is None
    # Not seen for ten minutes: the next open reports the absence and remembers it.
    with store.transaction() as db:
        db.execute("UPDATE sessions SET last_seen=?", (time.time() - 600,))
    first = client.get("/api/v1/session").json()["away_since"]
    assert first is not None and time.time() - 605 <= first <= time.time() - 595
    # Polling keeps last_seen fresh; the absence reported is still the original one.
    assert client.get("/api/v1/session").json()["away_since"] == first
    # A second absence before the first was viewed keeps the older point.
    with store.transaction() as db:
        db.execute("UPDATE sessions SET last_seen=?", (time.time() - 300,))
    assert client.get("/api/v1/session").json()["away_since"] == first
    # Viewing clears it; a short gap (< 3 min) is not an absence.
    r = client.post(
        "/api/v1/session/seen",
        json={"revision": 0},
        headers={**headers, "Idempotency-Key": "seen-1"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get("/api/v1/session").json()["away_since"] is None
    with store.transaction() as db:
        db.execute("UPDATE sessions SET last_seen=?", (time.time() - 120,))
    assert client.get("/api/v1/session").json()["away_since"] is None
    # CSRF still guards the bookkeeping route.
    r = client.post(
        "/api/v1/session/seen",
        json={"revision": 0},
        headers={"Origin": "http://127.0.0.1:8790"},
    )
    assert r.status_code == 403


# -- slice B: task detail and transcript on demand ----------------------------------------


def test_live_tail_accepts_bounded_lines_and_window_for_the_transcript_view(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project, path="src/long.txt")
    ident = insert_attempt(store, task_id, state="passed", ended=time.time())
    artifact = store.home / "attempts" / ident
    artifact.mkdir(parents=True)
    rows = [
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": f"L{n:03d}" * 250}}
        )
        for n in range(120)  # ~120 KB, twice the default window
    ]
    (artifact / "transcript.jsonl").write_text("\n".join(rows) + "\n")
    first = lambda body: min(int(x["text"][1:4]) for x in body["lines"])  # noqa: E731
    default = client.get(f"/api/v1/attempts/{ident}/live").json()
    assert len(default["lines"]) == livetail.LINES and first(default) == 60
    # The Transcript view: the whole file, up to 400 lines.
    full = client.get(f"/api/v1/attempts/{ident}/live?lines=400&window=1048576").json()
    assert len(full["lines"]) == 120 and first(full) == 0 and full["done"] is True
    short = client.get(f"/api/v1/attempts/{ident}/live?lines=5").json()
    assert [x["text"][:4] for x in short["lines"]] == ["L115", "L116", "L117", "L118", "L119"]
    # Bounds: never more than LINES_MAX lines or WINDOW_MAX bytes, never fewer than one line.
    capped = client.get(f"/api/v1/attempts/{ident}/live?lines=100000&window=10000000000").json()
    assert len(capped["lines"]) == 120  # the file has 120 lines; the caps are 400 / 1 MB
    assert livetail.tail({"artifact_dir": str(artifact)}, provider=None, done=True, lines=0)[
        "lines"
    ] == default["lines"][-1:]
    assert client.get(f"/api/v1/attempts/{ident}/live?lines=abc").status_code == 422
