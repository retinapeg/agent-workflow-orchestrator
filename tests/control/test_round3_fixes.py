"""Round 3 review fixes (backend): credentials never reach the director or stored chat cards,
archive guards that match the story, archived projects coming back when work runs again,
discovery that fails closed and never hangs, and the director's view of Arena and the Mac.
No provider CLI is invoked and no real process is inspected."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import psutil
import pytest

from agent_arena.control import conversation, discovery, story
from agent_arena.control.status import arena_status
from agent_arena.control.store import encode
from agent_arena.control.worker import redact, redact_args
from tests.control import test_control as support
from tests.control import test_director
from tests.control.test_control import goal, paired_client
from tests.control.test_conversation import FakeRunner, fake_gh_run, ready_accounts
from tests.control.test_round3 import FakeProcess, add_part, git_repo, post, set_state
from tests.control.test_usability_api import insert_attempt


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


def director(store, engine, tmp_path, monkeypatch, answers, mac_activity):
    """A director whose turns are answered by a fake CLI and that reads the Mac through
    ``mac_activity`` (a function, as the app's Discovery.get)."""
    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    runner = FakeRunner(answers)
    chat = conversation.Director(
        store,
        engine,
        run=fake_gh_run,
        runner=runner,
        projects_dir=tmp_path / "none",
        mac_activity=mac_activity,
    )
    return chat, runner


def chat(reply, action="chat"):
    return {"reply": reply, "project_hint": None, "action": action, "spec": None}


def unarchived(store, project_id):
    return not store.get("projects", project_id).get("archived")


# -- 1 and 9: credentials in command lines -------------------------------------------------

SECRETS = {
    "ghp": "ghp_1234567890abcdefghijklmnopqrstuvwxyzAB",
    "pat": "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ",
    "slack": "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
    "aws_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "google": "AIzaSyD-1234567890abcdefghijklmnopqrstu",  # AIza + 35, as Google issues
    "hf": "hf_abcdefghijklmnopqrstuvwxyzABCDEF",
    "basic": "dXNlcjpwYXNzd29yZA==",
    "anthropic": "sk-ant-api03-abcdefghijklmnop",
    "url_password": "hunter2pass",
    "db_password": "s3cretpw",
}


def test_redact_masks_every_credential_pattern_in_free_text():
    cases = {
        # URL credentials
        "clone https://leo:hunter2pass@github.com/leo/x.git": (
            "clone https://[REDACTED]@github.com/leo/x.git"
        ),
        "--db=postgres://admin:s3cretpw@localhost:5432/app": (
            "--db=postgres://[REDACTED]@localhost:5432/app"
        ),
        # Authorization schemes
        "Authorization: token " + SECRETS["ghp"]: "Authorization: token [REDACTED]",
        "Authorization: Basic " + SECRETS["basic"]: "Authorization: Basic [REDACTED]",
        "token abcdef0123456789 here": "token [REDACTED] here",
        "Authorization: Bearer abc+def/ghi==": "Authorization: Bearer [REDACTED]",
        # Known token formats
        "push with " + SECRETS["ghp"]: "push with [REDACTED]",
        "gho_abcdefghijklmnopqrstuvwxyz, ghs_ABCDEFGHIJKLMNOPQRSTUV": "[REDACTED], [REDACTED]",
        "use " + SECRETS["pat"]: "use [REDACTED]",
        "slack " + SECRETS["slack"]: "slack [REDACTED]",
        f"aws {SECRETS['aws_id']} {SECRETS['aws_secret']}": "aws [REDACTED] [REDACTED]",
        "key id ASIAIOSFODNN7EXAMPLE only": "key id [REDACTED] only",
        "google " + SECRETS["google"]: "google [REDACTED]",
        "my hf token " + SECRETS["hf"]: "my hf token [REDACTED]",
        # Assignments in free text whose value looks like a secret
        "$ PGPASSWORD=hunter2 psql -h db": "$ PGPASSWORD=[REDACTED] psql -h db",
        'API_TOKEN="abc123def456"': 'API_TOKEN="[REDACTED]"',
        "https://x.example/cb?state=1&access_token=abc123def": (
            "https://x.example/cb?state=1&access_token=[REDACTED]"
        ),
    }
    for text, expected in cases.items():
        assert redact(text) == expected, text
    # What redact() already did keeps working.
    assert redact("Token sk-abcdefghijklmnopqrstu leaked") == "Token [REDACTED] leaked"
    assert redact("Authorization: Bearer abc.def.ghi") == "Authorization: Bearer [REDACTED]"
    assert (
        redact("jwt eyJhbGciOiJIUzI1NiIsInR5cCI6.eyJzdWIiOiIxMjM0NTY3ODkwIn0") == "jwt [REDACTED]"
    )
    assert redact(None) == "" and redact("") == ""
    # Words and code that only look related stay as written.
    for text in (
        "Add a basic step-by-step guide",
        "Show the token JavaScript reads",
        "Basic authentication is enough",
        "max_tokens=128000 and auth=None",
        "token=get_token(1) if key==other else None",
        "self.cache_key=v2",
        "A git@github.com:leo/x.git remote",
    ):
        assert redact(text) == text, text
    # Idempotent: masking a masked text changes nothing.
    for expected in cases.values():
        assert redact(expected) == expected


def test_command_lines_mask_secret_flags_assignments_and_passwords():
    home = Path("/Users/leo")
    cases = [
        (["aider", "--api-key", SECRETS["anthropic"]], "aider --api-key [REDACTED]"),
        (["aider", "--api-key=" + SECRETS["anthropic"]], "aider --api-key=[REDACTED]"),
        (["aider", "--openai-api-key", "plainvalue"], "aider --openai-api-key [REDACTED]"),
        (
            ["env", f"ANTHROPIC_API_KEY={SECRETS['anthropic']}", "claude"],
            "env ANTHROPIC_API_KEY=[REDACTED] claude",
        ),
        (
            ["claude", "-p", f"push using {SECRETS['ghp']} please"],
            "claude -p push using [REDACTED] please",
        ),
        (
            ["codex", "exec", "clone https://leo:hunter2pass@github.com/leo/x.git and fix"],
            "codex exec clone https://[REDACTED]@github.com/leo/x.git and fix",
        ),
        (
            ["npm", "run", "dev", "--", "--db=postgres://admin:s3cretpw@localhost:5432/app"],
            "npm run dev -- --db=postgres://[REDACTED]@localhost:5432/app",
        ),
        (
            ["codex", "-c", f"model_providers.gemini.api_key={SECRETS['google']}", "exec"],
            "codex -c model_providers.gemini.api_key=[REDACTED] exec",
        ),
        (
            ["uvicorn", "app:app", "--header", f"Authorization: token {SECRETS['ghp']}"],
            "uvicorn app:app --header Authorization: token [REDACTED]",
        ),
        (["claude", "-p", "use " + SECRETS["pat"]], "claude -p use [REDACTED]"),
        (["claude", "-p", "slack " + SECRETS["slack"]], "claude -p slack [REDACTED]"),
        (
            ["claude", "-p", f"aws {SECRETS['aws_id']} {SECRETS['aws_secret']}"],
            "claude -p aws [REDACTED] [REDACTED]",
        ),
        (
            ["jupyter-lab", "--ServerApp.token=abcdef0123456789"],
            "jupyter-lab --ServerApp.token=[REDACTED]",
        ),
        (
            ["streamlit", "run", "app.py", "--server.cookieSecret", "abc"],
            "streamlit run app.py --server.cookieSecret [REDACTED]",
        ),
        (
            ["claude", "-p", "Authorization: Basic " + SECRETS["basic"]],
            "claude -p Authorization: Basic [REDACTED]",
        ),
        (["gemini", "--key", SECRETS["google"]], "gemini --key [REDACTED]"),
        (
            ["node", "/Users/leo/x/server.js", "--password", "hunter2"],
            "node ~/x/server.js --password [REDACTED]",
        ),
        (["claude", "-p", "my hf token " + SECRETS["hf"]], "claude -p my hf token [REDACTED]"),
        # A secret split over two arguments.
        (["tool", "--header", "token", "abcdef0123456789"], "tool --header token [REDACTED]"),
        # -p: a password only when it looks like one (a prompt, port or plugin stays).
        (["mysql", "-u", "root", "-p", "hunter2pass"], "mysql -u root -p [REDACTED]"),
        (["mysql", "-phunter2pass"], "mysql -p[REDACTED]"),
        (["claude", "-p", "Fix the login bug"], "claude -p Fix the login bug"),
        (["python3", "-m", "http.server", "-p", "8000"], "python3 -m http.server -p 8000"),
        (["pytest", "-p", "no:cacheprovider"], "pytest -p no:cacheprovider"),
        (["gcc", "-pthread", "main.c"], "gcc -pthread main.c"),
        # -u/--user user:password
        (
            ["curl", "-u", "leo:hunter2", "https://x.example"],
            "curl -u leo:[REDACTED] https://x.example",
        ),
        (["curl", "--user", "leo:s3cret", "x"], "curl --user leo:[REDACTED] x"),
        # Only the first six arguments are shown at all.
        (["a", "b", "c", "d", "e", "f", "--password", "hunter2"], "a b c d e f"),
    ]
    for argv, expected in cases:
        assert discovery.command_line(argv, home) == expected, argv
    assert redact_args(["--token", "x", "plain"]) == ["--token", "[REDACTED]", "plain"]
    long = discovery.command_line(["node", "x" * 400], home)
    assert len(long) == 200 and long.endswith("…")


def secret_mac(tmp_path, store, now):
    """A Mac whose agents carry credentials on their command lines."""
    home = tmp_path / "mac-home"
    repo = git_repo(home / "Projects" / "LeoOS")
    table = [
        FakeProcess(100, 1, ["-zsh"], cwd=home),
        FakeProcess(
            200, 100, ["claude", "-p", f"push using {SECRETS['ghp']}"], cwd=repo, started=now - 60
        ),
        FakeProcess(
            300,
            100,
            ["codex", "-c", f"model_providers.gemini.api_key={SECRETS['google']}", "exec"],
            cwd=repo,
        ),
        FakeProcess(
            400,
            100,
            ["npm", "run", "dev", "--", "--db=postgres://admin:s3cretpw@localhost:5432/app"],
            cwd=repo,
            ports=[5173],
        ),
    ]
    return home, table


def test_command_lines_never_reach_the_director_or_stored_chat_cards(setup, tmp_path, monkeypatch):
    store, engine, project, _ = setup
    now = time.time()
    home, table = secret_mac(tmp_path, store, now)
    found = discovery.discover(
        store, processes=table, home=home, user="leo", controller_pid=1, now=now
    )
    secrets = ("ghp_", "AIza", "s3cretpw", "admin:")
    live = json.dumps(found)
    # The live endpoint shows the command lines, redacted.
    assert "Command: claude -p push using [REDACTED]" in live
    assert not any(s in live for s in secrets)
    # The director's view and a stored card carry none: no command, evidence or pid.
    for view in (discovery.compact(found), discovery.capped(found)):
        text = json.dumps(view)
        assert "Command" not in text and "evidence" not in text and '"pid"' not in text
        assert "(pid" not in text and not any(s in text for s in secrets)
    # Through a real director turn: the prompt and the stored message.
    chat_director, runner = director(
        store, engine, tmp_path, monkeypatch, [chat("Claude is working in LeoOS.")], lambda: found
    )
    conv = conversation.new_conversation(store)
    chat_director._answer(conv["id"], "What's running on my Mac?")
    prompt = runner.prompts[0]
    assert '"mac_activity"' in prompt and "Command" not in prompt and "pid" not in prompt
    assert not any(s in prompt for s in secrets)
    stored = conversation.messages(store, conv["id"])[-1]
    card = json.dumps(stored["mac_activity"])
    assert "Command" not in card and "evidence" not in card and not any(s in card for s in secrets)
    for activity in (a for g in stored["mac_activity"]["groups"].values() for a in g):
        assert set(activity) == set(discovery.CARD_KEYS)


# -- 6, 11 and 12: session folders, "working" and zombies -------------------------------------


def session_folder(home, folder):
    path = home / ".claude" / "projects" / str(folder).replace("/", "-").replace(".", "-")
    path.mkdir(parents=True, exist_ok=True)
    return path


def touch(path, age, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    os.utime(path, (now - age, now - age))


def test_a_session_folder_too_large_to_read_reports_unknown_activity(setup, tmp_path, monkeypatch):
    store, engine, project, _ = setup
    now = time.time()
    home = tmp_path / "mac-home"
    repo = git_repo(home / "Projects" / "LeoOS")
    sessions = session_folder(home, repo)
    for n in range(7):  # more entries than the (lowered) cap; the fresh one may be unread
        touch(sessions / f"{n:04d}.jsonl", 3 * 86400, now)
    touch(sessions / "fresh.jsonl", 30, now)
    monkeypatch.setattr(discovery, "SCAN_LIMIT", 5)
    scans = []
    real = discovery.session_files
    monkeypatch.setattr(discovery, "session_files", lambda *a: scans.append(a[1]) or real(*a))
    table = [FakeProcess(100, 1, ["-zsh"], cwd=home)] + [
        FakeProcess(200 + n, 100, ["claude"], cwd=repo, started=now - 3600) for n in range(3)
    ]
    found = discovery.discover(
        store, processes=table, home=home, user="leo", controller_pid=1, now=now
    )
    sessions_seen = found["groups"]["external"]
    assert len(sessions_seen) == 3 and scans == [str(repo)]  # one read for the folder
    for activity in sessions_seen:
        assert activity["last_activity"] is None and activity["subagents_recent"] is None
        assert any("more than 5 Claude Code session files" in e for e in activity["evidence"])
    assert (
        found["summary"]
        == ["A Claude Code session is open in LeoOS; Arena could not tell when it was last active."]
        * 3
    )
    assert (
        "Some Claude Code session folders hold more than 5 files, so Arena stopped reading them "
        "and their last activity is unknown." in found["limitations"]
    )


def test_working_means_fresh_evidence_and_subagents_count_once_per_folder(setup, tmp_path):
    store, engine, project, _ = setup
    now = time.time()
    home = tmp_path / "mac-home"
    fresh = git_repo(home / "Projects" / "Fresh")
    quiet = git_repo(home / "Projects" / "Quiet")
    touch(session_folder(home, fresh) / "s1.jsonl", 60, now)
    touch(session_folder(home, fresh) / "s1" / "subagents" / "agent-a.jsonl", 90, now)
    touch(session_folder(home, fresh) / "agent-b.jsonl", 200, now)
    touch(session_folder(home, quiet) / "s2.jsonl", 300, now)
    table = [
        FakeProcess(100, 1, ["-zsh"], cwd=home),
        FakeProcess(200, 100, ["claude"], cwd=fresh),
        FakeProcess(201, 100, ["claude"], cwd=fresh),
        FakeProcess(300, 100, ["claude"], cwd=quiet),
        # A zombie and an exited process are not work, and not a limitation either.
        FakeProcess(400, 100, ["claude"], cwd=fresh, status=psutil.STATUS_ZOMBIE),
        FakeProcess(401, 100, ["codex"], cwd=fresh, status=psutil.STATUS_DEAD),
    ]
    found = discovery.discover(
        store, processes=table, home=home, user="leo", controller_pid=1, now=now
    )
    by_pid = {a["pid"]: a for a in found["groups"]["external"]}
    assert set(by_pid) == {200, 201, 300}
    assert [by_pid[p]["subagents_recent"] for p in (200, 201, 300)] == [2, 0, 0]
    assert sum(1 for e in by_pid[201]["evidence"] if "subagent" in e) == 0
    assert found["summary"] == [
        "Claude is working in Fresh (active 1 min ago).",
        "Claude is working in Fresh (active 1 min ago).",
        "A Claude Code session is open in Quiet (last active 5 min ago).",
    ]
    assert not any("ended or could not be read" in x for x in found["limitations"])


# -- 7: protected folders and a hung file system ----------------------------------------------


REAL_EXISTS = Path.exists


def record_stats(monkeypatch, slow=None, seconds=0.0):
    """Every Path.exists call (discovery's .git checks), sleeping under ``slow``."""
    seen = []

    def exists(self, *a, **k):
        seen.append(str(self))
        if slow and str(self).startswith(str(slow)):
            time.sleep(seconds)
        return REAL_EXISTS(self, *a, **k)

    monkeypatch.setattr(Path, "exists", exists)
    return seen


def test_privacy_folders_and_enrolled_projects_need_no_disk_access(setup, tmp_path, monkeypatch):
    store, engine, project, _ = setup
    now = time.time()
    home = tmp_path / "mac-home"
    client_app = git_repo(home / "Desktop" / "Client")
    (client_app / "src").mkdir()
    notes = git_repo(home / "Documents" / "Notes")
    volume = git_repo(tmp_path / "Volumes" / "NAS" / "Work")
    leo = git_repo(home / "Projects" / "LeoOS")
    (leo / "src").mkdir()
    with store.transaction() as db:  # a project enrolled from the Desktop
        db.execute(
            "INSERT INTO projects(id,data) VALUES(?,?)",
            ("project_desk", encode({"name": "Client", "repo": str(client_app), "scope": ["src"]})),
        )
    table = [
        FakeProcess(100, 1, ["-zsh"], cwd=home),
        FakeProcess(200, 100, ["claude"], cwd=client_app / "src"),
        FakeProcess(300, 100, ["codex"], cwd=notes),
        FakeProcess(400, 100, ["claude"], cwd=volume),
        FakeProcess(500, 100, ["npm", "run", "dev"], cwd=home / "Downloads"),
        FakeProcess(600, 100, ["claude"], cwd=leo / "src"),
    ]
    (home / "Downloads").mkdir()
    seen = record_stats(monkeypatch)
    look = discovery.Discovery(
        store, processes=lambda: table, home=home, user="leo", controller_pid=1, clock=lambda: now
    )
    found = look.get()
    stats = [s for s in seen if s.endswith(".git")]
    for private in ("Desktop", "Documents", "Downloads", "Library"):
        assert not any(s.startswith(str(home / private)) for s in stats), private
    assert not any(s.startswith(str(tmp_path / "Volumes")) for s in stats)
    assert stats == [str(leo / "src" / ".git"), str(leo / ".git")]
    by_pid = {a["pid"]: (g, a) for g, items in found["groups"].items() for a in items}
    assert by_pid[200][0] == "external" and by_pid[200][1]["project"] == "Client"
    assert by_pid[300][0] == "unknown" and by_pid[300][1]["repo"] is None
    assert by_pid[400][0] == "unknown" and by_pid[600][1]["repo"] == "~/Projects/LeoOS"
    assert found["summary"] == [
        "A Claude Code session is open in Client; Arena found no session activity.",
        "A Claude Code session is open in LeoOS; Arena found no session activity.",
        "A development server is running in Downloads.",
        "A Claude Code session is open in Work, a folder Arena does not inspect, so its "
        "repository is unknown.",
        "A Codex session is open in Notes, a folder Arena does not inspect, so its repository "
        "is unknown.",
    ]
    assert discovery.PRIVACY_NOTE in found["limitations"]
    assert str(tmp_path) not in json.dumps(found["summary"])
    # The repository found is remembered: the next look needs no disk access for it.
    seen.clear()
    now += 60
    look.get()
    assert [s for s in seen if s.endswith(".git")] == []


def test_a_hung_file_system_never_holds_up_the_endpoint_or_the_director(
    setup, tmp_path, monkeypatch
):
    store, engine, project, _ = setup
    now = [time.time()]
    home = tmp_path / "mac-home"
    slow = home / "Projects" / "Slow"
    slow.mkdir(parents=True)
    table = [FakeProcess(100, 1, ["-zsh"], cwd=home), FakeProcess(101, 100, ["claude"], cwd=slow)]
    record_stats(monkeypatch, slow=slow, seconds=3.0)

    def look(**extra):
        return discovery.Discovery(
            store,
            processes=lambda: table,
            home=home,
            user="leo",
            controller_pid=1,
            clock=lambda: now[0],
            deadline=0.5,
            lock_seconds=0.3,
            **extra,
        )

    client, headers = paired_client(store)
    client.app.state.discovery = look()
    started = time.monotonic()
    r = client.get("/api/v1/mac-activity")
    assert time.monotonic() - started < 2.0
    assert r.status_code == 200
    body = r.json()
    assert body["partial"] is True and discovery.PARTIAL_NOTE in body["limitations"]
    assert body["summary"] == [
        "A Claude Code session is open in Slow; Arena ran out of time checking it."
    ]
    # A director turn waits no longer than the deadline either.
    chat_director, runner = director(
        store, engine, tmp_path, monkeypatch, [chat("Claude is open in Slow.")], look().get
    )
    conv = conversation.new_conversation(store)
    started = time.monotonic()
    chat_director._answer(conv["id"], "What's running on my Mac?")
    assert time.monotonic() - started < 2.0
    reply = conversation.messages(store, conv["id"])[-1]
    assert reply["mac_activity"]["partial"] is True and "error" not in reply
    # A look still running elsewhere: the previous look, marked stale, within lock_seconds;
    # with no previous look the endpoint says to try again.
    record_stats(monkeypatch)  # fast first look
    shared = look()
    first = shared.get()
    assert first["partial"] is False and first["stale"] is False
    now[0] += 60
    record_stats(monkeypatch, slow=slow, seconds=3.0)
    holder = threading.Thread(target=shared.get)
    holder.start()
    time.sleep(0.1)
    started = time.monotonic()
    served = shared.get()
    assert time.monotonic() - started < 1.0
    assert served["stale"] is True and served["groups"] == first["groups"]
    assert served["limitations"][0].startswith("A fresh look at the Mac is still running")
    holder.join()
    empty = look()
    holder = threading.Thread(target=empty.get)
    holder.start()
    time.sleep(0.1)
    client.app.state.discovery = empty
    r = client.get("/api/v1/mac-activity")
    holder.join()
    assert r.status_code == 503 and "still looking" in r.json()["detail"]


# -- 8 and 10: what discovery reads ------------------------------------------------------------


def test_undecodable_arguments_never_break_the_endpoint_or_a_conversation(
    setup, tmp_path, monkeypatch
):
    store, engine, project, _ = setup
    home = tmp_path / "mac-home"
    repo = git_repo(home / "Projects" / "X")
    weird = b"-k \xff\xfe".decode("utf-8", "surrogateescape")  # psutil's non-UTF-8 argv
    table = [
        FakeProcess(100, 1, ["-zsh"], cwd=home),
        FakeProcess(
            101,
            100,
            ["pytest", weird],
            cwd=repo,
            name=b"py\xfftest".decode("utf-8", "surrogateescape"),
        ),
    ]
    client, headers = paired_client(store)
    client.app.state.discovery = discovery.Discovery(
        store, processes=lambda: table, home=home, user="leo", controller_pid=1
    )
    r = client.get("/api/v1/mac-activity")
    assert r.status_code == 200 and "Command: pytest -k ??" in json.dumps(r.json())
    chat_director, runner = director(
        store,
        engine,
        tmp_path,
        monkeypatch,
        [chat("Tests are running in X.")],
        client.app.state.discovery.get,
    )
    conv = conversation.new_conversation(store)
    chat_director._answer(conv["id"], "What's running on my Mac?")
    r = client.get(f"/api/v1/conversations/{conv['id']}")
    assert r.status_code == 200 and r.json()["messages"][-1]["mac_activity"]


class LazyProcess:
    """A psutil.Process as process_iter(ATTRS) yields it: owner and state prefetched, the
    rest read on request (recorded)."""

    def __init__(self, pid, user, argv, *, cwd=None, gone=False):
        self.info = {"pid": pid, "username": user, "status": psutil.STATUS_RUNNING}
        self.argv, self._cwd, self.gone, self.asked = argv, cwd, gone, []

    def as_dict(self, attrs):
        self.asked.append(list(attrs))
        if self.gone:
            raise psutil.NoSuchProcess(self.info["pid"])
        values = {"name": self.argv[0], "cmdline": self.argv, "create_time": 1.0, "ppid": 1}
        return {k: values[k] for k in attrs}

    def cwd(self):
        return str(self._cwd) if self._cwd else ""

    def __getattr__(self, name):
        raise AssertionError(f"discovery must not call Process.{name}")


def test_discovery_fails_closed_and_never_reads_other_users_command_lines(
    setup, tmp_path, monkeypatch
):
    store, engine, project, _ = setup
    home = tmp_path / "mac-home"
    repo = git_repo(home / "Projects" / "X")
    mine = LazyProcess(200, "leo", ["claude"], cwd=repo)
    theirs = LazyProcess(300, "root", ["claude", "--api-key", "sk-root-0123456789abcdef"], cwd=repo)
    ended = LazyProcess(400, "leo", ["codex"], gone=True)
    found = discovery.discover(
        store, processes=[mine, theirs, ended], home=home, user="leo", controller_pid=1
    )
    assert mine.asked == [discovery.DETAILS] and theirs.asked == [] and ended.asked
    assert [a["pid"] for a in found["groups"]["external"]] == [200]
    assert not any("ended or could not be read" in x for x in found["limitations"])
    assert discovery.ATTRS == ["pid", "username", "status"]
    # No user known: nothing is listed, Arena's own records are not guessed at, and it says so.
    with store.transaction() as db:
        created = engine.add_goal(db, project["id"], "Live", "Live", ["src"], (), True, 300)
    insert_attempt(store, created["task_id"], state="running")
    monkeypatch.setattr(discovery, "current_user", lambda: None)
    blind = discovery.discover(store, processes=[mine, theirs], home=home, controller_pid=1)
    assert blind["groups"] == {"managed": [], "external": [], "unknown": []}
    assert blind["limitations"][0] == discovery.NO_USER
    assert blind["summary"] == ["No coding agents, development servers or test runs were found."]


# -- 13 and 19: no pids or paths for the director and chat cards -------------------------------


def test_summaries_name_folders_home_relative_and_only_on_the_live_endpoint(setup, tmp_path):
    store, engine, project, _ = setup
    now = time.time()
    home = tmp_path / "mac-home"
    scratch = home / "scratch"
    scratch.mkdir(parents=True)
    elsewhere = tmp_path / "opt" / "tool"
    elsewhere.mkdir(parents=True)
    with store.transaction() as db:
        created = engine.add_goal(db, project["id"], "Ghost", "Ghost", ["src"], (), True, 300)
    insert_attempt(
        store, created["task_id"], state="running", identity={"pid": 7777, "created": 7.0}
    )
    table = [
        FakeProcess(100, 1, ["-zsh"], cwd=home),
        FakeProcess(200, 100, ["claude"], cwd=scratch),
        FakeProcess(300, 100, ["python3", "-m", "http.server"], cwd=elsewhere),
    ]
    found = discovery.discover(
        store, processes=table, home=home, user="leo", controller_pid=1, now=now
    )
    assert (
        "A Claude Code session is open in a folder outside any project (~/scratch)."
        in (found["summary"])
    )
    assert "A development server is running in tool." in found["summary"]
    assert str(tmp_path) not in json.dumps(found["summary"])
    ghost = found["groups"]["managed"][0]
    assert "No running process has its recorded id 7777" in ghost["evidence"]  # live only
    for view in (discovery.compact(found), discovery.capped(found)):
        text = json.dumps(view)
        assert "(~" not in text and str(tmp_path) not in text and "7777" not in text
        assert "pid" not in text
        assert "A Claude Code session is open in a folder outside any project." in view["summary"]
    assert discovery.repo_name("/Volumes/Work/client-app") == "client-app"
    assert discovery.repo_name("~/Projects/LeoOS") == "~/Projects/LeoOS"


# -- 2 and 3: archive guards that match the story ----------------------------------------------


def chain(store, engine, project, a_state, *, a_reason="Checks did not all pass"):
    """A director-planned build: plan done, part A in ``a_state``, B waits on A and C on B."""
    with store.transaction() as db:
        created = engine.add_goal(
            db, project["id"], "Chain", "OUTCOME: chain.", ["src"], planning=True, director=True
        )
    goal_id, plan = created["goal_id"], created["task_id"]
    a = add_part(store, goal_id, project["id"], "Part A", a_state, reason=a_reason)
    b = add_part(store, goal_id, project["id"], "Part B", "waiting_dependency", deps=[a])
    c = add_part(store, goal_id, project["id"], "Part C", "waiting_dependency", deps=[b])
    set_state(store, plan, "completed", planned_tasks=[a, b, c])
    return goal_id, a, b, c


def archive_project(client, headers, store, project_id, key):
    p = store.get("projects", project_id)
    return post(
        client, headers, f"/api/v1/projects/{project_id}/archive", {"revision": p["rev"]}, key
    )


IN_PROGRESS = "This project has a build in progress"
WAITING_FOR_YOU = "This project has something waiting for you — answer or decide it first."


def test_work_waiting_on_a_stopped_part_does_not_keep_a_build_or_project(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    stopped_states = ("blocked", "interrupted", "cancelled", "needs_auth", "needs_permission")
    for stopped in stopped_states:
        tasks = [
            {"id": "a", "state": stopped, "dependencies": []},
            {"id": "b", "state": "waiting_dependency", "dependencies": ["a"]},
            {"id": "c", "state": "waiting_dependency", "dependencies": ["b"]},
        ]
        assert story.stuck_waits(tasks) == {"b", "c"}, stopped
        assert story.still_going(tasks, owner_waits=True) == set(), stopped
    for n, stopped in enumerate(stopped_states[:3]):
        goal_id, a, b, c = chain(store, engine, project, stopped)
        assert client.get(f"/api/v1/goals/{goal_id}/story").json()["status"] in {
            "blocked",
            "queued",
        }
        r = post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, f"g{n}")
        assert r.json() == {"archived": [goal_id], "skipped": []}, stopped
    # Nothing runs or waits for the owner: the project can be archived too.
    r = archive_project(client, headers, store, project["id"], "p1")
    assert r.status_code == 200 and r.json()["archived"] is True
    post(
        client,
        headers,
        f"/api/v1/projects/{project['id']}/unarchive",
        {"revision": store.get("projects", project["id"])["rev"]},
        "u1",
    )
    # A part stopped for a sign-in or permission waits for the owner: its parts behind it do
    # not keep its build, but the project says something is waiting for you.
    for n, stopped in enumerate(stopped_states[3:]):
        goal_id, a, b, c = chain(store, engine, project, stopped)
        r = archive_project(client, headers, store, project["id"], f"pw{n}")
        assert r.status_code == 409 and r.json()["detail"] == WAITING_FOR_YOU, stopped
        r = post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, f"gw{n}")
        assert r.json() == {"archived": [goal_id], "skipped": []}, stopped
    # Waiting on a part that is still running is still going: refused.
    goal_id, a, b, c = chain(store, engine, project, "running")
    r = post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, "g9")
    assert r.json()["skipped"] == [{"id": goal_id, "reason": "still running"}]
    r = archive_project(client, headers, store, project["id"], "p2")
    assert r.status_code == 409 and r.json()["detail"] == IN_PROGRESS


def staged(store, engine, project, path, approval_id, state, **data):
    task_id = goal(store, engine, project, path=path)
    set_state(store, task_id, "ready_for_approval")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",
            (approval_id, task_id, state, encode({"candidate": "c" * 40, "created": 2.0, **data})),
        )
    return task_id, store.get("tasks", task_id)["goal_id"]


def set_policy(store, project_id, policy):
    with store.transaction() as db:
        p = store.get("projects", project_id, db)
        p["integration_policy"] = policy
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (encode({k: v for k, v in p.items() if k not in {"id", "rev"}}), project_id),
        )


def set_publication(store, approval_id, state):
    with store.transaction() as db:
        approval = store.get("approvals", approval_id, db)
        data = {k: v for k, v in approval.items() if k not in {"id", "rev", "state", "task_id"}}
        data["publication"] = {"state": state, "branch": "main"}
        db.execute("UPDATE approvals SET data=? WHERE id=?", (encode(data), approval_id))


def test_changes_awaiting_approval_or_being_merged_keep_their_build(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)

    def archive(goal_id, key):
        return post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, key).json()

    # Manual policy: a verified change waiting for the owner's approval (story: Needs you).
    waiting, waiting_goal = staged(store, engine, project, "src/wait.txt", "approval_w", "pending")
    assert client.get(f"/api/v1/goals/{waiting_goal}/story").json()["status"] == "needs_you"
    assert archive(waiting_goal, "m1")["skipped"] == [
        {"id": waiting_goal, "reason": "still running"}
    ]
    # Nor its project: the decision would leave the needs-you lists with it.
    r = archive_project(client, headers, store, project["id"], "pm1")
    assert r.status_code == 409 and r.json()["detail"] == WAITING_FOR_YOU
    # Manual policy, accepted and authorised: the change is being merged into main while its
    # task already reads completed. Neither the build nor the project can be archived.
    set_state(store, waiting, "completed")
    with store.transaction() as db:
        db.execute("UPDATE approvals SET state='accept' WHERE id='approval_w'")
    set_publication(store, "approval_w", "queued")
    assert archive(waiting_goal, "m2")["skipped"] == [
        {"id": waiting_goal, "reason": "still running"}
    ]
    r = archive_project(client, headers, store, project["id"], "pm2")
    assert r.status_code == 409 and r.json()["detail"] == IN_PROGRESS
    set_publication(store, "approval_w", "running")
    assert archive(waiting_goal, "m3")["skipped"] == [
        {"id": waiting_goal, "reason": "still running"}
    ]
    set_publication(store, "approval_w", "integrated")
    assert archive(waiting_goal, "m4") == {"archived": [waiting_goal], "skipped": []}
    # auto_local_main: accepted by policy and merging (story: Integrating).
    set_policy(store, project["id"], "auto_local_main")
    merging, merging_goal = staged(
        store,
        engine,
        project,
        "src/merge.txt",
        "approval_m",
        "accept",
        publication={"state": "queued"},
    )
    s = client.get(f"/api/v1/goals/{merging_goal}/story").json()
    assert s["status"] == "running" and s["blocks"][0]["state_label"] == "Integrating"
    assert archive(merging_goal, "a1")["skipped"] == [
        {"id": merging_goal, "reason": "still running"}
    ]
    r = archive_project(client, headers, store, project["id"], "pa1")
    assert r.status_code == 409 and r.json()["detail"] == IN_PROGRESS
    # The merge was refused: the build is stopped (story: Blocked) and can be archived.
    set_publication(store, "approval_m", "failed")
    assert client.get(f"/api/v1/goals/{merging_goal}/story").json()["status"] == "blocked"
    assert archive(merging_goal, "a2") == {"archived": [merging_goal], "skipped": []}
    assert archive_project(client, headers, store, project["id"], "pa2").status_code == 200


def test_a_project_with_a_decision_waiting_for_the_owner_cannot_be_archived(setup):
    """Lead decision (Round 3 review): archived projects leave the needs-you lists, so a
    question, sign-in, permission or approval must be dealt with before its project is
    archived. Running work keeps its own message."""
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    task_id = goal(store, engine, project, path="src/decide.txt")
    goal_id = store.get("tasks", task_id)["goal_id"]
    for n, (state, extra) in enumerate(
        [
            ("awaiting_input", {"question": "Which colour?"}),
            ("needs_auth", {}),
            ("needs_permission", {}),
            ("ready_for_approval", {}),  # no approval recorded yet under manual: Needs you
        ]
    ):
        set_state(store, task_id, state, **extra)
        r = archive_project(client, headers, store, project["id"], f"w{n}")
        assert r.status_code == 409 and r.json()["detail"] == WAITING_FOR_YOU, state
        assert unarchived(store, project["id"])
    # A verified change waiting for approval.
    with store.transaction() as db:
        db.execute(
            "INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",
            ("approval_d", task_id, "pending", encode({"candidate": "d" * 40, "created": 3.0})),
        )
    r = archive_project(client, headers, store, project["id"], "w9")
    assert r.status_code == 409 and r.json()["detail"] == WAITING_FOR_YOU
    # Running work as well: "in progress" is what the owner hears first.
    busy = goal(store, engine, project, path="src/busy.txt")
    set_state(store, busy, "running")
    r = archive_project(client, headers, store, project["id"], "w10")
    assert r.status_code == 409 and r.json()["detail"] == IN_PROGRESS
    set_state(store, busy, "completed")
    # Decided (the change rejected): the build stops and the project can be archived.
    with store.transaction() as db:
        db.execute("UPDATE approvals SET state='reject' WHERE id='approval_d'")
    set_state(store, task_id, "blocked", "Change rejected; staging and evidence preserved")
    r = archive_project(client, headers, store, project["id"], "w11")
    assert r.status_code == 200 and r.json()["archived"] is True
    post(
        client,
        headers,
        f"/api/v1/projects/{project['id']}/unarchive",
        {"revision": store.get("projects", project["id"])["rev"]},
        "u",
    )
    # A sign-in inside a build the owner already archived is out of the needs-you lists
    # already: it does not hold the project back.
    set_state(store, task_id, "needs_auth", "Subscription login missing or unsupported")
    assert post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, "ga").json() == {
        "archived": [goal_id],
        "skipped": [],
    }
    assert archive_project(client, headers, store, project["id"], "w12").status_code == 200
    assert story.waiting_for_owner([{"id": "x", "state": "blocked"}]) == set()


# -- 4: answering or retrying work in an archived project ---------------------------------------


def test_answering_work_in_an_archived_project_brings_the_project_back(tmp_path):
    store, engine, project, _ = test_director.setup.__wrapped__(tmp_path)
    client, headers = paired_client(store)
    with store.transaction() as db:
        created = engine.add_goal(
            db,
            project["id"],
            "Colours",
            "Make the button nicer",
            ["src"],
            planning=True,
            director=True,
        )
    task_id = created["task_id"]
    set_state(store, task_id, "awaiting_input", question="Which colour?")
    # A question keeps its project out of the archive now; a project archived before that
    # rule existed can still hold one.
    r = archive_project(client, headers, store, project["id"], "pa")
    assert r.status_code == 409 and r.json()["detail"] == WAITING_FOR_YOU
    with store.transaction() as db:
        p = store.get("projects", project["id"], db)
        p.update(archived=True, archived_at=time.time())
        db.execute(
            "UPDATE projects SET rev=rev+1,data=? WHERE id=?",
            (encode({k: v for k, v in p.items() if k not in {"id", "rev"}}), p["id"]),
        )
    task = store.get("tasks", task_id)
    r = post(
        client,
        headers,
        f"/api/v1/tasks/{task_id}/answer",
        {"revision": task["rev"], "text": "Blue"},
        "an",
    )
    assert r.status_code == 200 and r.json() == {"ok": True, "restored_project": True}
    assert unarchived(store, project["id"])
    attempt = engine.claim()  # dispatchable again
    assert attempt is not None and attempt["task_id"] == task_id
    events = [e["kind"] for e in client.get("/api/v1/overview").json()["events"]]
    assert "project.changed" in events


def test_retrying_or_instructing_work_in_an_archived_project_brings_it_back(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    goal_id, a, b, c = chain(store, engine, project, "blocked")
    assert post(client, headers, "/api/v1/goals/archive", {"ids": [goal_id]}, "g")
    assert archive_project(client, headers, store, project["id"], "p1").status_code == 200
    r = post(
        client, headers, f"/api/v1/tasks/{a}/retry", {"revision": store.get("tasks", a)["rev"]}, "r"
    )
    assert r.json() == {"ok": True, "restored_project": True}
    assert unarchived(store, project["id"]) and not store.get("goals", goal_id)["archived"]
    assert store.get("tasks", a)["state"] == "ready"
    # A retry in a project that was never archived says so too.
    set_state(store, a, "blocked")
    r = post(
        client,
        headers,
        f"/api/v1/tasks/{a}/retry",
        {"revision": store.get("tasks", a)["rev"]},
        "r2",
    )
    assert r.json() == {"ok": True, "restored_project": False}
    # Instructions for a stopped director task in an archived project.
    set_state(store, a, "blocked")
    assert archive_project(client, headers, store, project["id"], "p2").status_code == 200
    r = post(
        client,
        headers,
        f"/api/v1/tasks/{a}/instructions",
        {"revision": store.get("tasks", a)["rev"], "text": "Use the new colours."},
        "i",
    )
    assert r.json() == {"ok": True, "restored_project": True} and unarchived(store, project["id"])
    # Cancelling never brings anything back.
    set_state(store, b, "cancelled")
    assert (
        archive_project(client, headers, store, project["id"], "p3").status_code == 409
    )  # A ready
    set_state(store, a, "cancelled")
    assert archive_project(client, headers, store, project["id"], "p4").status_code == 200
    r = post(
        client,
        headers,
        f"/api/v1/tasks/{c}/cancel",
        {"revision": store.get("tasks", c)["rev"]},
        "c",
    )
    assert r.json() == {"ok": True} and not unarchived(store, project["id"])


# -- 5 and 16: what the director hears about Arena ---------------------------------------------


def test_arena_status_leaves_archived_builds_out_and_lists_the_newest_ten(setup):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    stopped = goal(store, engine, project, path="src/old.txt")
    set_state(store, stopped, "blocked", "Frozen checks did not all pass")
    done = goal(store, engine, project, path="src/done.txt")
    set_state(store, done, "completed")
    status = arena_status(store)
    assert [n["title"] for n in status["needs_you"]] == ["src/old.txt"]
    assert [r["title"] for r in status["recent"]] == ["src/done.txt"]
    ids = [store.get("tasks", t)["goal_id"] for t in (stopped, done)]
    assert (
        post(client, headers, "/api/v1/goals/archive", {"ids": ids}, "a").json()["archived"] == ids
    )
    status = arena_status(store)
    assert status["needs_you"] == [] and status["recent"] == [] and status["needs_you_total"] == 0
    assert [f["goal_id"] for f in story.outcomes(store)["failures"]] == []
    # Many builds waiting: the ten newest, and how many there are in all.
    asking = []
    for n in range(12):
        task_id = goal(store, engine, project, path=f"src/ask{n}.txt")
        set_state(store, task_id, "awaiting_input", question=f"Question {n}?")
        asking.append(task_id)
    status = arena_status(store)
    assert status["needs_you_total"] == 12 and len(status["needs_you"]) == 10
    assert [n["title"] for n in status["needs_you"]] == [
        f"src/ask{n}.txt" for n in range(11, 1, -1)
    ]


# -- 15: which questions carry the Mac card -----------------------------------------------------


def test_only_questions_about_current_activity_carry_the_mac_card():
    asks = [
        "What's running on my laptop?",
        "What's running on my Mac?",
        "What's running right now?",
        "What is Claude doing?",
        "Which agents are stuck?",
        "Is anything stuck?",
        "Is Codex still working?",
        "Is anything running?",
        "Anything running across the machine?",
        "What's going on on my MacBook?",
        "What is Codex up to?",
    ]
    for text in asks:
        assert conversation.MAC_QUESTION.search(text), text
    for text in (
        "Make the active tab bold",
        "Rename the Claude provider label",
        "Let's start improving the Pomodoro timer.",
        "Let's start improving LeoOS.",
        "Use Codex for the review step",
        "The machine learning model is slow",
        "What went wrong with that build?",
        "Is it active by default?",
        "Build it",
    ):
        assert not conversation.MAC_QUESTION.search(text), text


# -- 17: asking about another project's work drops the draft -----------------------------------


def test_asking_about_another_projects_work_never_launches_the_old_draft(
    setup, tmp_path, monkeypatch
):
    store, engine, project, _ = setup
    client, headers = paired_client(store)
    other = tmp_path / "other"
    other.mkdir()
    with store.transaction() as db:
        db.execute(
            "INSERT INTO projects(id,data) VALUES(?,?)",
            (
                "project_other",
                encode(
                    {
                        **{k: v for k, v in project.items() if k not in {"id", "rev"}},
                        "name": "Other",
                        "repo": str(other),
                    }
                ),
            ),
        )
        db.execute(
            "INSERT INTO goals VALUES(?,?,?)",
            ("goal_other", "project_other", encode({"title": "Other build", "created": 1.0})),
        )
    other_task = add_part(store, "goal_other", "project_other", "Other part", "blocked")
    conv = conversation.new_conversation(store, project_id=project["id"])
    with store.transaction() as db:
        live = store.get("conversations", conv["id"], db)
        live["spec"] = {
            "outcome": "Rewrite the Fixture importer",
            "acceptance": [],
            "constraints": [],
        }
        conversation._save(store, db, live)
    monkeypatch.setattr(conversation.Director, "submit", lambda self, ident, text, about=None: None)
    path = f"/api/v1/conversations/{conv['id']}/messages"
    # About work in the same project: the draft stays.
    same = goal(store, engine, project, path="src/same.txt")
    assert (
        post(client, headers, path, {"text": "Why?", "about": {"task_id": same}}, "m1").status_code
        == 200
    )
    assert (
        store.get("conversations", conv["id"])["spec"]["outcome"] == "Rewrite the Fixture importer"
    )
    # About another project's work: the conversation moves and the old draft is dropped.
    r = post(client, headers, path, {"text": "Why?", "about": {"task_id": other_task}}, "m2")
    assert r.status_code == 200
    moved = store.get("conversations", conv["id"])
    assert moved["project_id"] == "project_other" and moved["spec"] is None
    # "Build it" now cannot launch the Fixture draft on the other project.
    monkeypatch.undo()
    chat_director, runner = director(
        store, engine, tmp_path, monkeypatch, [chat("Building it.", action="build")], lambda: None
    )
    goals_before = len(store.rows("goals"))
    chat_director._answer(conv["id"], "Build it")
    reply = conversation.messages(store, conv["id"])[-1]
    assert reply["launch_error"] == "The specification has no agreed outcome yet."
    assert len(store.rows("goals")) == goals_before


# -- 18 and the product decision: agents working, work in progress ------------------------------


def test_agents_working_and_in_progress_count_only_live_work():
    now = 10_000.0

    def task(ident, state, **extra):
        return {
            "id": ident,
            "title": ident,
            "state": state,
            "phase": "build",
            "dependencies": [],
            "created": float(len(ident)),
            "updated": 3.0,
            "planner_attempt": "attempt_plan",
            **extra,
        }

    def attempt(ident, task_id, state):
        return {
            "id": ident,
            "task_id": task_id,
            "state": state,
            "phase": "build",
            "created": now - 60,
        }

    tasks = [
        task(
            "a",
            "recovering",
            reason="Guardian lost; retaining lease until its owned child is stopped",
        ),
        task("bb", "leased"),
        task("ccc", "running"),
        task("dddd", "verifying"),
        task("eeeee", "reviewing", phase="review"),
        task("ffffff", "ready"),
        task("ggggggg", "waiting_dependency", dependencies=["ccc"]),
        task("hhhhhhhh", "ready_for_approval"),
        task("iiiiiiiii", "integrating"),
    ]
    attempts = [
        attempt("attempt_a", "a", "running"),
        attempt("attempt_b", "bb", "leased"),
        attempt("attempt_c", "ccc", "running"),
        attempt("attempt_d", "dddd", "verifying"),
        attempt("attempt_e", "eeeee", "running"),
    ]
    merging = {
        "id": "approval_h",
        "task_id": "hhhhhhhh",
        "state": "accept",
        "created": 2.0,
        "publication": {"state": "queued"},
    }
    auto = {"id": "project_x", "name": "X", "integration_policy": "auto_local_main"}
    goal_record = {"id": "goal_x", "title": "Live", "created": 1.0}
    s = story.goal_story(goal_record, auto, tasks, attempts, [merging], now=now)
    # Agents: running c, checks of d, the reviewer on e; not the starting b, not a's agent
    # whose guardian was lost.
    assert s["agents_working"] == 3
    # In progress: a, b, c, d, e, the change merging (h) and the integration (i); not the
    # queued f or the waiting g.
    assert s["progress"]["in_progress"] == 7
    manual = {**auto, "integration_policy": "manual"}
    waiting = {**merging, "state": "pending", "publication": None}
    s = story.goal_story(goal_record, manual, tasks, attempts, [waiting], now=now)
    assert s["progress"]["in_progress"] == 6  # a change waiting for approval is not
