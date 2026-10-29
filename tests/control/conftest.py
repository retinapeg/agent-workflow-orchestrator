import pytest

from agent_arena.control import discovery


@pytest.fixture(autouse=True)
def no_real_process_inspection(monkeypatch):
    """Mac activity discovery reads a fake process table in tests, never this machine's:
    a test that wants processes passes its own table (see test_round3.fake_mac)."""
    monkeypatch.setattr(discovery, "system_processes", lambda: [])
