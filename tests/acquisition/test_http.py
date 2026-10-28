"""HTTP parser/handler tests using in-memory bytes, not a listener or browser."""

import io
from urllib.parse import urlencode

import pytest

from agent_arena.acquisition.core import Store
from agent_arena.acquisition.data import frozen_records
from agent_arena.acquisition.web import make_handler


class MemoryRequest:
    def __init__(self, raw):
        self.raw = raw
        self.output = bytearray()

    def makefile(self, mode, *args):
        assert mode == "rb"
        return io.BytesIO(self.raw)

    def sendall(self, data):
        self.output.extend(data)


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "http.sqlite")
    store.ingest(frozen_records())
    yield store
    store.close()


def request(store, action="/", fields=None, host="localhost:8765", origin=None):
    body = urlencode(fields or {}).encode()
    verb = "POST" if fields is not None else "GET"
    headers = [
        f"{verb} {action} HTTP/1.1",
        f"Host: {host}",
        "Connection: close",
        f"Content-Length: {len(body)}",
        "Content-Type: application/x-www-form-urlencoded",
    ]
    if origin:
        headers.append(f"Origin: {origin}")
    connection = MemoryRequest(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
    handler = make_handler(store, "test-token", 8765)
    handler(connection, ("127.0.0.1", 1234), object())
    return connection.output.decode()


def test_http_rejects_foreign_hosts_and_csrf_without_side_effects(store):
    assert "403 Forbidden" in request(store, host="attacker.invalid:8765")
    fields = {"token": "wrong", "event": "web", "budget": "2400", "method": "scripted-agent"}
    assert "400 Bad Request" in request(store, "/propose", fields)
    fields["token"] = "test-token"
    assert "400 Bad Request" in request(
        store, "/propose", fields, origin="https://attacker.invalid"
    )
    assert store.db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0] == 0


def test_http_serves_dashboard_and_all_action_forms(store):
    response = request(store)
    assert "200 OK" in response and "Content-Security-Policy:" in response
    assert "AcquisitionOps" in response and "test-token" in response
    assert "404 Not Found" in request(store, "/missing")
    assert "200 OK" in request(
        store,
        "/propose",
        {"token": "test-token", "event": "http", "budget": "2400", "method": "scripted-agent"},
    )
    cid = store.db.execute("SELECT id FROM campaigns").fetchone()[0]
    for action, extra in [
        ("/review", {"actor": "Human"}),
        ("/approve", {"actor": "Human", "confirmation": "APPROVE"}),
        ("/execute", {"failure": "none"}),
        ("/advance", {"days": "7"}),
    ]:
        assert "200 OK" in request(store, action, {"token": "test-token", "campaign": cid, **extra})
    assert store.campaign(cid)["status"] == "SETTLED"
