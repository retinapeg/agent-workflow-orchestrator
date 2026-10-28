"""Round 2 of the usable Arena: the phone experience, slice by slice."""

from __future__ import annotations

import time

import pytest

from tests.control import test_control as support
from tests.control.test_control import paired_client


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
