"""Round 2 of the usable Arena: the phone experience, slice by slice."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent_arena.control import conversation, livetail
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client
from tests.control.test_conversation import FakeRunner, fake_gh_run, ready_accounts, wait_idle
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


# -- slice C: director turn visibility ----------------------------------------------------


def codex_line(kind, **item):
    return json.dumps({"type": kind, "item": item}) + "\n"


def set_accounts(store, **fields):
    with store.transaction() as db:
        for a in store.rows("accounts", db):
            a.update(fields)
            db.execute(
                "UPDATE accounts SET data=? WHERE id=?",
                (support.encode({k: v for k, v in a.items() if k not in {"id", "rev"}}), a["id"]),
            )


def chat_in_fixture(store, engine, project, tmp_path, runner):
    director = conversation.Director(
        store, engine, run=fake_gh_run, runner=runner, projects_dir=tmp_path / "none"
    )
    conv = conversation.new_conversation(store)
    with store.transaction() as db:
        live = store.get("conversations", conv["id"], db)
        live["project_id"] = project["id"]
        conversation._save(store, db, live)
    return director, conv["id"]


def last_message(store, conv_id):
    return conversation.messages(store, conv_id)[-1]


def test_director_turn_records_steps_while_it_reads_the_repository(tmp_path, monkeypatch):
    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    observed = []

    def seen():
        live = store.get("conversations", conv_id)
        observed.append(
            (live["status"], live["step"], live["step_detail"], live["commands"], live["turn_repo"])
        )

    go = threading.Event()

    class StreamingRunner(FakeRunner):
        """Streams Codex events while it 'works', as the real CLI does, then answers."""

        def run(self, argv, *, on_output=None, **kw):
            assert go.wait(5)
            seen()  # the CLI has started in the selected repository
            on_output("stdout", json.dumps({"type": "thread.started"}).encode() + b"\n")
            started = codex_line(
                "item.started", type="command_execution", command="/bin/zsh -lc 'git log -3'"
            )
            on_output("stdout", started[:25].encode())  # a line split across two reads
            on_output("stderr", b"warning: noise on stderr is ignored\n")
            on_output("stdout", started[25:].encode())
            seen()
            on_output(
                "stdout",
                (
                    codex_line("item.completed", type="command_execution", command="git log -3")
                    + codex_line(
                        "item.started",
                        type="command_execution",
                        command="/bin/zsh -lc 'rg -n \"bell\" src/'",
                    )
                ).encode(),
            )
            seen()
            on_output("stdout", b"{not json\n")  # a corrupt line never breaks the reader
            seen()
            return super().run(argv, **kw)

    answer = {"reply": "Three commits, all green.", "project_hint": "Fixture", "action": "chat"}
    director, conv_id = chat_in_fixture(
        store, engine, project, tmp_path, StreamingRunner([{**answer, "spec": None}])
    )
    director.submit(conv_id, "What happened in Fixture lately?")
    # Submitted: the turn is open and the phone can say so at once.
    live = conversation.view(store.get("conversations", conv_id))
    assert live["status"] == "thinking" and live["turn_started"] and live["step_since"]
    assert live["step"] in {"starting", "reading Arena status", "reading repository"}
    assert conv_id in director.busy
    go.set()
    wait_idle(store, conv_id)
    assert observed == [
        ("thinking", "reading repository", "", 0, "source"),
        ("thinking", "reading repository", "git log -3", 1, "source"),
        ("thinking", "reading repository", 'rg -n "bell" src/', 2, "source"),
        ("thinking", "reading repository", 'rg -n "bell" src/', 2, "source"),
    ]
    # Answered: idle, no step left behind, the count kept for the record.
    live = conversation.view(store.get("conversations", conv_id))
    assert live["status"] == "idle" and live["step"] is None and live["step_detail"] == ""
    assert live["commands"] == 2 and conv_id not in director.busy
    assert last_message(store, conv_id)["text"] == "Three commits, all green."
    # A late write from a reader thread after the turn closed changes nothing.
    director._step(conv_id, "reading repository", "late")
    assert store.get("conversations", conv_id)["step"] is None
    # Long details are clipped to one phone line.
    on_output = director._progress(conv_id, str(source))
    with store.transaction() as db:
        live = store.get("conversations", conv_id, db)
        live["status"] = "thinking"
        conversation._save(store, db, live)
    long = codex_line("item.started", type="command_execution", command="cat " + "x" * 400)
    on_output("stdout", long.encode())
    detail = store.get("conversations", conv_id)["step_detail"]
    assert len(detail) == conversation.STEP_DETAIL_CHARS and detail.endswith("…")


def test_director_errors_are_plain_language_with_retry(tmp_path, monkeypatch):
    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    ready_accounts(store)
    signed_in = {"ok": True}
    monkeypatch.setattr(conversation, "auth_status", lambda account: (signed_in["ok"], {}))
    director, conv_id = chat_in_fixture(store, engine, project, tmp_path, FakeRunner([]))

    def turn(runner, text):
        director.runner = runner
        director.submit(conv_id, text)
        wait_idle(store, conv_id)
        message = last_message(store, conv_id)
        assert message["error"] is True and message["retry_text"] == text
        assert "tap retry" in message["text"].lower()
        assert "Traceback" not in message["text"] and "Error" not in message["text"]
        live = store.get("conversations", conv_id)
        assert live["status"] == "idle" and live["step"] is None
        return message

    class Exits(FakeRunner):
        def run(self, argv, **kw):
            token = "sk-live-0123456789abcdef"
            return SimpleNamespace(stdout="", stderr=f"boom: {token}", exit_code=1, timed_out=False)

    message = turn(Exits([]), "Build it")
    assert message["error_kind"] == "cli"
    assert message["text"].startswith("The director's CLI returned an error (exit 1)")
    assert "sk-live-0123456789abcdef" not in message["detail"]
    assert "[REDACTED]" in message["detail"]
    assert not store.rows("tasks")  # "build it" in a failed turn starts nothing

    class Silent(FakeRunner):
        def run(self, argv, **kw):
            return SimpleNamespace(stdout="", stderr="", exit_code=0, timed_out=False)

    message = turn(Silent([]), "Anything?")
    assert message["error_kind"] == "cli" and "finished without an answer" in message["text"]

    class TimesOut(FakeRunner):
        def run(self, argv, **kw):
            assert kw["timeout_seconds"] == conversation.TURN_TIMEOUT_SECONDS
            return SimpleNamespace(stdout="", stderr="", exit_code=-15, timed_out=True)

    message = turn(TimesOut([]), "Still there?")
    assert message["error_kind"] == "timeout" and "longer than 4 minutes" in message["text"]

    class Crashes(FakeRunner):
        def run(self, argv, **kw):
            raise RuntimeError("pipe closed")

    message = turn(Crashes([]), "And now?")
    assert message["error_kind"] == "error" and message["detail"] == "pipe closed"
    assert message["text"].startswith("Something went wrong while answering")

    signed_in["ok"] = False
    message = turn(FakeRunner([]), "Signed in?")
    assert message["error_kind"] == "auth" and "not signed in on the Mac" in message["text"]
    signed_in["ok"] = True

    # No slot to answer: switched off in Agents, or not signed in on the Mac.
    set_accounts(store, enabled=False)
    message = turn(FakeRunner([]), "Hello?")
    assert message["error_kind"] == "no_account" and "Turn one on in Agents" in message["text"]
    set_accounts(store, health="needs_auth")
    message = turn(FakeRunner([]), "Hello again?")
    assert message["error_kind"] == "no_account" and "Sign in on the Mac" in message["text"]
    # Retry is the same send path: once a slot answers, the retried text gets its reply.
    ready_accounts(store)
    answer = {"reply": "Hello.", "project_hint": None, "action": "chat", "spec": None}
    director.runner = FakeRunner([answer])
    director.submit(conv_id, message["retry_text"])
    wait_idle(store, conv_id)
    assert [m["text"] for m in conversation.messages(store, conv_id)[-2:]] == [
        "Hello again?",
        "Hello.",
    ]


def thinking_since(store, conv_id, seconds_ago, **fields):
    with store.transaction() as db:
        live = store.get("conversations", conv_id, db)
        live.update(
            {
                "status": "thinking",
                "step": "reading repository",
                "step_detail": "git status",
                "step_since": time.time() - seconds_ago,
                "turn_started": time.time() - seconds_ago,
                **fields,
            }
        )
        conversation._save(store, db, live)


def test_a_turn_orphaned_by_a_restart_is_closed_with_retry(setup, monkeypatch):
    store, engine, project, _ = setup
    stale = conversation.STALE_TURN_SECONDS + 5
    old, recent = conversation.new_conversation(store), conversation.new_conversation(store)
    for conv in (old, recent):
        conversation.add_message(store, conv["id"], "user", "What is running right now?")
    thinking_since(store, old["id"], stale)
    thinking_since(store, recent["id"], 30)  # Arena restarted 30 s into this turn
    # The controller starts: only one ever runs, so every turn the previous one was
    # answering is closed at once, however recent, with Retry.
    client, headers = paired_client(store)
    for conv in (old, recent):
        closed = last_message(store, conv["id"])
        assert closed["text"] == conversation.INTERRUPTED and closed["error_kind"] == "interrupted"
        assert closed["retry_text"] == "What is running right now?" and closed["error"] is True
        assert store.get("conversations", conv["id"])["status"] == "idle"
    director = client.app.state.director
    assert director.recover() == []  # idempotent
    # A turn this controller started with no thread answering it (it has been up a while)
    # is left alone while recent; past STALE_TURN_SECONDS the phone reading it closes it.
    director.started = time.time() - 2 * stale
    thinking_since(store, recent["id"], 30)
    assert client.get(f"/api/v1/conversations/{recent['id']}").json()["status"] == "thinking"
    thinking_since(store, recent["id"], stale)
    body = client.get(f"/api/v1/conversations/{recent['id']}").json()
    assert body["status"] == "idle" and body["step"] is None
    assert body["messages"][-1]["text"] == conversation.INTERRUPTED
    # A turn this process is answering is never closed under it, however long it runs.
    thinking_since(store, old["id"], stale)
    director.busy[old["id"]] = object()
    assert director.recover() == [] and director.recover(old["id"]) == []
    del director.busy[old["id"]]
    # A new message never waits for a dead turn: it is closed first, then answered.
    ready_accounts(store)
    director.runner = FakeRunner(
        [{"reply": "Nothing is running.", "project_hint": None, "action": "chat", "spec": None}]
    )
    director.catalog.run = fake_gh_run
    director.catalog.projects_dir = store.home / "none"
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    r = client.post(
        f"/api/v1/conversations/{old['id']}/messages",
        json={"text": "Anything now?", "revision": 0},
        headers={**headers, "Idempotency-Key": "after-restart"},
    )
    assert r.status_code == 200, r.text
    wait_idle(store, old["id"])
    texts = [m["text"] for m in conversation.messages(store, old["id"])[-3:]]
    assert texts == [conversation.INTERRUPTED, "Anything now?", "Nothing is running."]


def test_conversation_records_carry_the_director_progress(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    idle = conversation.new_conversation(store)
    busy = conversation.new_conversation(store)
    thinking_since(store, busy["id"], 12, step_detail="rg -n " + "q" * 300, commands=4)
    client.app.state.director.busy[busy["id"]] = object()  # this controller is answering it
    with store.transaction() as db:
        live = store.get("conversations", busy["id"], db)
        live["turn_repo"] = "arena-smoke-20261028"
        conversation._save(store, db, live)
    keys = {"step", "step_detail", "step_since", "turn_started", "turn_repo", "commands"}
    listed = {c["id"]: c for c in client.get("/api/v1/overview").json()["conversations"]}
    for source in (
        listed,
        {c["id"]: c for c in client.get("/api/v1/conversations").json()},
        {i: client.get(f"/api/v1/conversations/{i}").json() for i in (idle["id"], busy["id"])},
    ):
        assert keys <= set(source[idle["id"]]) and keys <= set(source[busy["id"]])
        assert source[idle["id"]]["step"] is None and source[idle["id"]]["commands"] == 0
        progress = source[busy["id"]]
        assert progress["step"] == "reading repository" and progress["commands"] == 4
        assert progress["turn_repo"] == "arena-smoke-20261028"
        assert len(progress["step_detail"]) == conversation.STEP_DETAIL_CHARS
        assert 11 <= time.time() - progress["turn_started"] <= 20


# -- slice D: chat as the front door ------------------------------------------------------


def add_project(store, ident, name, **fields):
    """A second enrolled project (a copy of the fixture's record under another name)."""
    with store.transaction() as db:
        base = store.rows("projects", db)[0]
        data = {k: v for k, v in base.items() if k not in {"id", "rev"}}
        data.update(name=name, **fields)
        db.execute("INSERT INTO projects(id,data) VALUES(?,?)", (ident, support.encode(data)))


def change_project(store, ident, **fields):
    with store.transaction() as db:
        p = store.get("projects", ident, db)
        p.update(fields)
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (support.encode({k: v for k, v in p.items() if k not in {"id", "rev"}}), ident),
        )


def test_a_new_chat_starts_on_the_current_project(tmp_path):
    def creator(store):
        client, headers = paired_client(store)
        keys = iter(range(1000))

        def new(**body):
            r = client.post(
                "/api/v1/conversations",
                json={"revision": 0, "text": "", **body},
                headers={**headers, "Idempotency-Key": f"new-{next(keys)}"},
            )
            return r.json()["project_id"] if r.status_code == 200 else r.status_code

        return new

    # Nothing enrolled: the chat starts without a project.
    assert creator(support.Store(tmp_path / "empty"))() is None

    store, engine, project, _ = support.setup.__wrapped__(tmp_path / "fixture")
    fixture = project["id"]
    add_project(store, "project_second", "Second")
    new = creator(store)
    assert new() is None  # enrolled, but never talked about nor built
    # No conversation names a project yet: the project with the most recent build.
    goal(store, engine, project, path="src/old.txt")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO goals(id,project_id,data) VALUES(?,?,?)",
            (
                "goal_second",
                "project_second",
                support.encode({"created": time.time() + 5, "title": "Newest build"}),
            ),
        )
    assert new() == "project_second"
    # The project of the most recent conversation wins over a newer build; a more recent
    # chat without a project does not reset it.
    talked = conversation.new_conversation(store, project_id=fixture)
    conversation.add_message(store, talked["id"], "user", "How is Fixture doing?")
    conversation.add_message(store, conversation.new_conversation(store)["id"], "user", "Hi")
    assert new() == fixture
    # Archived and paused projects are never a default.
    change_project(store, fixture, archived=True)
    assert new() == "project_second"
    change_project(store, "project_second", enabled=False)
    assert new() is None
    change_project(store, fixture, archived=False)
    assert new() == fixture
    # The owner's explicit choice (or explicit none) is kept; an archived project is refused.
    assert new(project_id="project_second") == "project_second"
    assert new(project_id=None) is None
    change_project(store, "project_second", archived=True)
    assert new(project_id="project_second") == 409
    assert new(project_id="project_missing") == 404


def test_build_it_in_a_new_chat_builds_on_the_current_project(tmp_path, monkeypatch):
    store, engine, project, source = support.setup.__wrapped__(tmp_path)
    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    earlier = conversation.new_conversation(store, project_id=project["id"])
    conversation.add_message(store, earlier["id"], "user", "Fixture first")
    client, headers = paired_client(store)
    created = client.post(
        "/api/v1/conversations",
        json={"revision": 0, "text": ""},
        headers={**headers, "Idempotency-Key": "front-door"},
    ).json()
    assert created["project_id"] == project["id"]  # nobody had to pick it
    spec = {
        "outcome": "Add src/hello.txt containing a greeting",
        "acceptance": ["src/hello.txt exists"],
        "constraints": ["Only src"],
        "notes": "",
    }
    runner = FakeRunner(
        [
            {"reply": "Shall I add it?", "project_hint": None, "action": "build", "spec": spec},
            {"reply": "Starting the build.", "project_hint": None, "action": "build", "spec": spec},
        ]
    )
    director = conversation.Director(
        store, engine, run=fake_gh_run, runner=runner, projects_dir=tmp_path / "none"
    )
    # The director agreeing is not enough: no "build it" in the owner's words, no build.
    director.submit(created["id"], "I'd like a greeting file in src.")
    wait_idle(store, created["id"])
    assert last_message(store, created["id"]).get("offer_build") and not store.rows("goals")
    # "Build it" goes through the existing explicit-execution path on the default project.
    director.submit(created["id"], "Great, build it.")
    wait_idle(store, created["id"])
    reply = last_message(store, created["id"])
    assert reply["build_started"] is True and reply["project"] == "Fixture"
    goal_row = store.get("goals", reply["goal_id"])
    assert goal_row["project_id"] == project["id"] and reply["task_id"]
    detail = client.get(f"/api/v1/conversations/{created['id']}").json()
    assert detail["messages"][-1]["goal_id"] == reply["goal_id"]
