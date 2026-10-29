"""Round 2 C/D review fixes: each test fails without its fix."""

from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from agent_arena.control import conversation
from tests.control import test_control as support
from tests.control.test_control import paired_client
from tests.control.test_conversation import FakeRunner, fake_gh_run, ready_accounts, wait_idle
from tests.control.test_round2 import (
    change_project,
    chat_in_fixture,
    codex_line,
    last_message,
    thinking_since,
)


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


@pytest.fixture
def director_chat(tmp_path, monkeypatch):
    """The fixture project with ready slots and a signed-in CLI; the runner is set per test."""
    store, engine, project, _ = support.setup.__wrapped__(tmp_path)
    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))

    def chat(runner):
        return chat_in_fixture(store, engine, project, tmp_path, runner)

    return store, engine, project, chat


def answers(*replies):
    return [{"reply": r, "project_hint": None, "action": "chat", "spec": None} for r in replies]


def until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition never held")
        time.sleep(0.01)


# -- 1. the technical detail is redacted before it is cut ----------------------------------


def test_technical_detail_never_keeps_part_of_a_secret(director_chat):
    store, engine, project, chat = director_chat
    director, conv_id = chat(FakeRunner([]))
    key = "sk-proj-Q7mZ4xK9vT2wR8nB5yH3jL6pF1cD0sGa"
    token = "Zm9vYmFyYmF6cXV4cXV1eHF1dXg0NTY3ODkwYWJj"
    # The detail keeps the last 400 characters; they begin 20 characters before the end of
    # each secret, so cutting first would drop the "sk-" or "Bearer " that marks it.
    filler = ("warning: retrying\n" * 30)[: 400 - 20 - 1]
    cases = [
        ("cli", key, f"codex: request failed for {key}\n{filler}", 1, False),
        ("timeout", token, f"Authorization: Bearer {token}\n{filler}", -15, True),
    ]
    for kind, secret, stderr, code, timed_out in cases:

        class Fails(FakeRunner):
            result = SimpleNamespace(stdout="", stderr=stderr, exit_code=code, timed_out=timed_out)

            def run(self, argv, **kw):
                return self.result

        director.runner = Fails([])
        director.submit(conv_id, "Is anything running?")
        wait_idle(store, conv_id)
        message = last_message(store, conv_id)
        assert message["error_kind"] == kind and len(message["detail"]) <= 400
        assert "[REDACTED]" in message["detail"]
        fragments = {secret[i : i + 8] for i in range(len(secret) - 7)}
        assert not [f for f in fragments if f in message["detail"]], message["detail"]


# -- 2. a turn the previous controller was answering is closed at once ---------------------


def test_a_turn_the_previous_controller_was_answering_is_closed_at_once(setup, monkeypatch):
    store, engine, project, _ = setup
    conv = conversation.new_conversation(store, project_id=project["id"])
    conversation.add_message(store, conv["id"], "user", "What is running right now?")
    thinking_since(store, conv["id"], 30)  # Arena restarted (self-build activation) 30 s in
    director = conversation.Director(store, engine)
    assert director.recover() == [conv["id"]]
    closed = last_message(store, conv["id"])
    assert closed["text"] == "The director was interrupted before it could answer. Tap Retry."
    assert closed["error"] is True and closed["error_kind"] == "interrupted"
    assert closed["retry_text"] == "What is running right now?"
    live = conversation.view(store.get("conversations", conv["id"]))
    assert live["status"] == "idle" and live["step"] is None
    # Through the controller: it closes such a turn as it starts, so the owner's next
    # message is answered at once instead of being refused as "still answering".
    thinking_since(store, conv["id"], 5)
    client, headers = paired_client(store)
    assert store.get("conversations", conv["id"])["status"] == "idle"
    app_director = client.app.state.director
    app_director.runner = FakeRunner(answers("Nothing is running."))
    app_director.catalog.run = fake_gh_run
    app_director.catalog.projects_dir = store.home / "none"
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    r = client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"text": "Anything now?", "revision": 0},
        headers={**headers, "Idempotency-Key": "after-activation"},
    )
    assert r.status_code == 200, r.text
    wait_idle(store, conv["id"])
    texts = [m["text"] for m in conversation.messages(store, conv["id"])[-3:]]
    assert texts == [conversation.INTERRUPTED, "Anything now?", "Nothing is running."]


# -- 3. progress writes never hold up the CLI's output reader ------------------------------


def hold_write_lock(store, seconds):
    """Another writer (the scheduler, an approval) holding the database for a while."""
    blocker = sqlite3.connect(store.path, isolation_level=None, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")

    def release():
        blocker.execute("ROLLBACK")
        blocker.close()

    timer = threading.Timer(seconds, release)
    timer.start()
    return timer


def test_progress_writes_never_wait_on_a_busy_database(director_chat):
    store, engine, project, chat = director_chat
    waited = []

    class StreamsWhileLocked(FakeRunner):
        """The CLI reports a command while another writer holds the lock for a second."""

        def run(self, argv, *, on_output=None, **kw):
            hold_write_lock(store, 1.0)
            began = time.monotonic()
            line = codex_line("item.started", type="command_execution", command="git log -3")
            on_output("stdout", line.encode())
            waited.append(time.monotonic() - began)
            return super().run(argv, **kw)

    director, conv_id = chat(StreamsWhileLocked(answers("Three commits, all green.")))
    # Directly: a step write gives up quickly, skips the update and never raises.
    thinking_since(store, conv_id, 1)
    held = hold_write_lock(store, 1.0)
    began = time.monotonic()
    director._step(conv_id, "reading repository", "rg -n bell src/")
    assert time.monotonic() - began < 0.5
    assert store.get("conversations", conv_id)["step_detail"] == "git status"  # skipped
    held.join()
    director._step(conv_id, "reading repository", "rg -n bell src/")
    assert store.get("conversations", conv_id)["step_detail"] == "rg -n bell src/"
    with store.transaction() as db:
        live = store.get("conversations", conv_id, db)
        live["status"] = "idle"
        conversation._save(store, db, live)
    # In a turn: the reader thread is never held up, and the answer still lands once the
    # other writer lets go (the final write waits for the lock as before).
    director.submit(conv_id, "What happened lately?")
    wait_idle(store, conv_id)
    assert waited and waited[0] < 0.5, waited
    assert last_message(store, conv_id)["text"] == "Three commits, all green."


# -- 6. one turn per conversation, released only by its own thread -------------------------


def test_two_sends_racing_open_exactly_one_turn(director_chat):
    store, engine, project, chat = director_chat
    release = threading.Event()

    class Holds(FakeRunner):
        def run(self, argv, **kw):
            assert release.wait(5)
            return super().run(argv, **kw)

    director, conv_id = chat(Holds(answers("One answer.", "Another answer.")))
    together = threading.Barrier(2)
    recover = director.recover

    def recover_then_race(*args, **kw):
        found = recover(*args, **kw)
        together.wait(5)  # both sends passed recovery: now they race to open the turn
        return found

    director.recover = recover_then_race
    outcomes = {}

    def send(device):
        try:
            director.submit(conv_id, f"Sent from the {device}")
            outcomes[device] = "accepted"
        except ValueError as exc:
            outcomes[device] = str(exc)

    senders = [threading.Thread(target=send, args=(d,)) for d in ("phone", "laptop")]
    for sender in senders:
        sender.start()
    for sender in senders:
        sender.join(10)
    accepted = [d for d, outcome in outcomes.items() if outcome == "accepted"]
    assert len(accepted) == 1, outcomes
    assert "Arena is still answering the previous message" in outcomes.values()
    release.set()
    wait_idle(store, conv_id)
    texts = [m["text"] for m in conversation.messages(store, conv_id)]
    assert texts == [f"Sent from the {accepted[0]}", "One answer."]
    assert len(director.runner.prompts) == 1
    until(lambda: not director.busy)


def test_a_finished_turn_never_releases_the_next_one(director_chat):
    store, engine, project, chat = director_chat
    second = threading.Event()

    class SecondHolds(FakeRunner):
        def run(self, argv, **kw):
            if self.prompts:  # the second turn keeps working until released
                assert second.wait(5)
            return super().run(argv, **kw)

    director, conv_id = chat(SecondHolds(answers("First answer.", "Second answer.")))
    answered, finish, finished = threading.Event(), threading.Event(), []
    answer, turn = director._answer, director._turn

    def answer_then_linger(conversation_id, text):
        answer(conversation_id, text)
        if text == "First":
            answered.set()  # the first answer has landed; its thread has not finished yet
            assert finish.wait(5)

    def tracked(*args):
        try:
            turn(*args)
        finally:
            finished.append(args[1])

    director._answer, director._turn = answer_then_linger, tracked
    director.submit(conv_id, "First")
    assert answered.wait(5)
    director.submit(conv_id, "Second")  # accepted: the first answer is in, the turn idle
    finish.set()
    until(lambda: "First" in finished)
    # The second turn is still running and still this process's own: recovery never closes
    # it under it, however long it runs.
    assert conv_id in director.busy
    stale = time.time() + conversation.STALE_TURN_SECONDS + 5
    assert director.recover(conv_id, now=stale) == []
    second.set()
    wait_idle(store, conv_id)
    texts = [m["text"] for m in conversation.messages(store, conv_id)]
    assert texts == ["First", "First answer.", "Second", "Second answer."]
    until(lambda: not director.busy)


# -- 7. a build the project refuses is explained, not retried -------------------------------


def test_a_build_the_project_refuses_is_explained_without_retry(director_chat):
    store, engine, project, chat = director_chat
    spec = {
        "outcome": "Add src/hello.txt containing a greeting",
        "acceptance": ["src/hello.txt exists"],
        "constraints": ["Only src"],
        "notes": "",
    }
    build = {"reply": "Starting the build.", "project_hint": None, "action": "build"}
    director, conv_id = chat(FakeRunner([{**build, "spec": spec}]))
    change_project(store, project["id"], archived=True)  # archived from another device
    director.submit(conv_id, "Great, build it.")
    wait_idle(store, conv_id)
    reply = last_message(store, conv_id)
    assert reply["text"] == "Starting the build."
    assert reply["launch_error"] == "The build was not started: Project is archived."
    assert not reply.get("error") and "retry_text" not in reply
    assert not reply.get("build_started") and not store.rows("goals")


# -- 8. the model's interim messages are not "writing the answer" ---------------------------


def test_interim_model_messages_do_not_flip_the_step_to_answering(director_chat):
    store, engine, project, chat = director_chat
    steps = []

    class Interim(FakeRunner):
        """Codex streams the model's interim messages between its commands."""

        def run(self, argv, *, on_output=None, **kw):
            for line in (
                codex_line("item.started", type="command_execution", command="git log -3"),
                codex_line("item.completed", type="agent_message", text="Checking tests too."),
                codex_line("item.started", type="command_execution", command="pytest -q"),
            ):
                on_output("stdout", line.encode())
                live = store.get("conversations", conv_id)
                steps.append((live["step"], live["step_detail"]))
            return super().run(argv, **kw)

    director, conv_id = chat(Interim(answers("All green.")))
    director.submit(conv_id, "Are the tests green?")
    wait_idle(store, conv_id)
    assert steps == [
        ("reading repository", "git log -3"),
        ("reading repository", "git log -3"),
        ("reading repository", "pytest -q"),
    ]
    assert last_message(store, conv_id)["text"] == "All green."


# -- 10. reading a conversation never fails on its recovery bookkeeping ---------------------


def test_reading_a_conversation_survives_a_failing_recovery(setup, monkeypatch):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    conv = conversation.new_conversation(store)

    def locked(*args, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(client.app.state.director, "recover", locked)
    r = client.get(f"/api/v1/conversations/{conv['id']}")
    assert r.status_code == 200 and r.json()["id"] == conv["id"]
    assert client.get("/api/v1/conversations/conv_missing").status_code == 404
