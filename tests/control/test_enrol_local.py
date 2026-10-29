"""Enrol a local repository from the conversation (owner-authorised): the one-tap endpoint,
the owner-tapped checkpoint of unsaved work, and the director's offer. Repositories are
temporary fixtures; no provider CLI runs."""

import contextlib
import json

import pytest

from agent_arena.control import conversation, enrolment
from agent_arena.control.engine import git
from agent_arena.control.store import encode
from tests.control import test_control as support
from tests.control.test_control import paired_client
from tests.control.test_conversation import FakeRunner, fake_gh_run, ready_accounts, wait_idle

DIRTY = (
    "Dirty Lab has 2 unsaved changes on your Mac. Arena only builds from committed work, so it "
    "never overwrites yours. Commit or stash them on the Mac, then try again."
)
ON_DEV = "Feature Repo is on branch dev. Switch it to main on the Mac first."
SPEC = {
    "outcome": "Add a CSV export to Partnerflow Lab",
    "acceptance": ["Exporting writes export.csv"],
    "constraints": ["Only src"],
    "notes": "",
}


def make_repo(root, name, files=None, branch="main"):
    repo = root / name
    repo.mkdir(parents=True)
    for rel, text in (files or {"README.md": "# demo\n", "src/app.py": "x = 1\n"}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    if branch != "main":
        git(repo, "switch", "-q", "-c", branch)
    return repo


def state(repo):
    """What a refusal must leave alone: every ref, the index and the working tree."""
    return (
        git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
        git(repo, "symbolic-ref", "-q", "HEAD"),
        git(repo, "diff", "--cached", "--name-status"),
        git(repo, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"),
    )


def events(store, kind):
    with contextlib.closing(store.connect()) as db:
        rows = db.execute("SELECT data FROM events WHERE kind=? ORDER BY id", (kind,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


def enrol(client, headers, key, **body):
    return client.post(
        "/api/v1/projects/enroll-local", json=body, headers={**headers, "Idempotency-Key": key}
    )


@pytest.fixture
def arena(tmp_path):
    store, engine, fixture, _ = support.setup.__wrapped__(tmp_path)
    projects = tmp_path / "Projects"
    projects.mkdir()
    client, headers = paired_client(store)
    client.app.state.director.catalog.projects_dir = projects  # the owner's ~/Projects
    return store, client, headers, projects, fixture


def test_enrols_a_clean_repository_once_and_moves_the_chat_onto_it(arena):
    store, client, headers, projects, fixture = arena
    repo = make_repo(
        projects,
        "partnerflow-lab",
        {"README.md": "# PF\n", "src/app.py": "x = 1\n", "CLAUDE.md": "assistant notes\n"},
    )
    before = state(repo)
    conv = conversation.new_conversation(store, project_id=fixture["id"])
    with store.transaction() as db:
        live = store.get("conversations", conv["id"], db)
        live["spec"] = SPEC
        conversation._save(store, db, live)
    r = enrol(client, headers, "e1", name="Partnerflow-LAB", conversation_id=conv["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "ok": True,
        "project_id": body["project_id"],
        "name": "Partnerflow Lab",
        "already": False,
        "checkpoint": None,
    }
    project = store.get("projects", body["project_id"])
    assert project["repo"] == str(repo.resolve()) and project["name"] == "Partnerflow Lab"
    assert project["integration_policy"] == "auto_local_main" and project["enabled"] is True
    assert "partnerflow-lab" in project["keywords"]
    # Workers may change the tree, never the coding assistant's own instructions.
    assert set(project["scope"]) == {"README.md", "src"}
    assert [e["project_id"] for e in events(store, "project.enrolled")][-1] == project["id"]
    # The chat that asked now builds there, with the agreed specification kept.
    live = store.get("conversations", conv["id"])
    assert live["project_id"] == project["id"] and live["spec"] == SPEC
    assert state(repo) == before  # enrolment reads the repository; it changes nothing in it
    # A transport retry replays the first answer; a later tap says Arena is already there.
    assert (
        enrol(client, headers, "e1", name="Partnerflow-LAB", conversation_id=conv["id"]).json()
        == body
    )
    again = enrol(client, headers, "e2", name="partnerflow-lab")
    assert again.status_code == 200, again.text
    assert again.json() == {**body, "already": True}
    assert [p["id"] for p in store.rows("projects") if p["repo"] == project["repo"]] == [
        project["id"]
    ]
    # A tap for a repository whose project was archived brings it back (history kept).
    with store.transaction() as db:
        archived = {**store.get("projects", project["id"], db), "archived": True}
        db.execute(
            "UPDATE projects SET data=? WHERE id=?",
            (encode({k: v for k, v in archived.items() if k not in {"id", "rev"}}), project["id"]),
        )
    back = enrol(client, headers, "e3", name="partnerflow-lab")
    assert back.json()["already"] is True and not store.get("projects", project["id"])["archived"]


def test_refusals_are_plain_and_leave_every_repository_untouched(arena, tmp_path):
    store, client, headers, projects, _ = arena
    dirty = make_repo(projects, "dirty-lab")
    (dirty / "README.md").write_text("changed\n")
    (dirty / "notes.txt").write_text("new, untracked\n")
    feature = make_repo(projects, "feature-repo", branch="dev")
    outside = make_repo(tmp_path, "outside")
    (projects / "plain-folder").mkdir()
    before = {repo: state(repo) for repo in (dirty, feature, outside)}
    count = len(store.rows("projects"))
    r = enrol(client, headers, "d1", name="dirty-lab")
    assert (r.status_code, r.json()["detail"]) == (409, DIRTY)
    r = enrol(client, headers, "b1", name="feature-repo")
    assert (r.status_code, r.json()["detail"]) == (409, ON_DEV)
    # Saving unsaved work never switches branches: a repository off main is still refused.
    (feature / "src/app.py").write_text("x = 2\n")
    before[feature] = state(feature)
    r = enrol(client, headers, "b2", name="feature-repo", checkpoint=True)
    assert (r.status_code, r.json()["detail"]) == (409, ON_DEV)
    # Only a repository the catalog lists, by folder name: never a path or a plain folder.
    for i, name in enumerate(
        [
            "nothing-here",
            "../outside",
            str(outside),
            "dirty-lab/../../outside",
            "..",
            "plain-folder",
        ]
    ):
        r = enrol(client, headers, f"n{i}", name=name)
        assert r.status_code == 404, (name, r.text)
        assert r.json()["detail"].startswith("There is no repository called ")
    no_key = {k: v for k, v in headers.items() if k != "Idempotency-Key"}
    r = client.post("/api/v1/projects/enroll-local", json={"name": "dirty-lab"}, headers=no_key)
    assert r.status_code == 400
    assert len(store.rows("projects")) == count and not events(store, "project.checkpointed")
    assert {repo: state(repo) for repo in before} == before


def test_checkpoint_commits_exactly_the_unsaved_work_on_main_then_enrols(arena):
    store, client, headers, projects, _ = arena
    repo = make_repo(
        projects,
        "save-lab",
        {
            "README.md": "# save\n",
            "src/app.py": "x = 1\n",
            "old.txt": "old\n",
            "gone.txt": "gone\n",
            ".gitignore": "*.log\n",
        },
    )
    git(repo, "branch", "keep")
    keep, base = git(repo, "rev-parse", "keep"), git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("# save\nstaged\n")
    git(repo, "add", "README.md")  # already staged
    git(repo, "rm", "-q", "gone.txt")  # a deletion already staged
    (repo / "src/app.py").write_text("x = 2\n")  # modified
    (repo / "old.txt").unlink()  # deleted
    (repo / "docs").mkdir()
    (repo / "docs/new.md").write_text("new\n")  # untracked, in a new folder
    (repo / "debug.log").write_text("ignored by .gitignore\n")
    r = enrol(client, headers, "c1", name="save-lab", checkpoint=True)
    assert r.status_code == 200, r.text
    body = r.json()
    sha = git(repo, "rev-parse", "HEAD")
    assert body["checkpoint"] == {"sha": sha, "files": 5} and body["already"] is False
    assert body["name"] == "Save Lab"
    assert git(repo, "log", "-1", "--format=%an <%ae>|%s|%P") == (
        f"Arena <arena@local>|Checkpoint by Arena before enrolment (5 files)|{base}"
    )
    assert git(repo, "show", "--name-status", "--format=", "HEAD").splitlines() == [
        "M\tREADME.md",
        "A\tdocs/new.md",
        "D\tgone.txt",
        "D\told.txt",
        "M\tsrc/app.py",
    ]
    # On main, nothing else moved, nothing stashed; the ignored file stays only on disk.
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "rev-parse", "keep") == keep and git(repo, "stash", "list") == ""
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert (repo / "debug.log").exists() and git(repo, "ls-files", "debug.log") == ""
    assert events(store, "project.checkpointed")[-1] == {
        "schema": 1,
        "repo": str(repo.resolve()),
        "name": "Save Lab",
        "sha": sha,
        "files": 5,
    }
    project = store.get("projects", body["project_id"])
    assert project["base"] == project["head"] == sha
    # The commit is the owner's work saved, not a second change on replay or later taps.
    assert enrol(client, headers, "c1", name="save-lab", checkpoint=True).json() == body
    assert enrol(client, headers, "c2", name="save-lab", checkpoint=True).json()["already"] is True
    assert git(repo, "rev-parse", "HEAD") == sha and len(events(store, "project.checkpointed")) == 1


def test_checkpoint_refuses_secret_looking_or_large_files_and_commits_nothing(arena):
    store, client, headers, projects, _ = arena
    secret = make_repo(projects, "secret-lab")
    (secret / "src/app.py").write_text("x = 2\n")  # ordinary work alongside
    for rel in (".env", "config/client_secret.json", "keys/server.pem", "id_rsa", "auth/token.txt"):
        (secret / rel).parent.mkdir(parents=True, exist_ok=True)
        (secret / rel).write_text("s3cr3t\n")
    big = make_repo(projects, "big-lab")
    with open(big / "data.bin", "wb") as f:
        f.truncate(enrolment.FILE_LIMIT + 1)  # sparse: nothing is really written
    with open(big / "cache.sqlite3", "wb") as f:
        f.truncate(enrolment.SQLITE_LIMIT + 1)
    (big / "small.sqlite3").write_bytes(b"a small database is fine")
    before = {repo: state(repo) for repo in (secret, big)}
    count = len(store.rows("projects"))
    tail = "; Arena won't commit those. Move them out or ignore them on the Mac, then try again."
    r = enrol(client, headers, "s1", name="secret-lab", checkpoint=True)
    assert (r.status_code, r.json()["detail"]) == (
        409,
        "Secret Lab has files that look like secrets or large data (.env, auth/token.txt, "
        "config/client_secret.json, id_rsa, keys/server.pem)" + tail,
    )
    r = enrol(client, headers, "s2", name="big-lab", checkpoint=True)
    assert (r.status_code, r.json()["detail"]) == (
        409,
        "Big Lab has files that look like secrets or large data (cache.sqlite3, data.bin)" + tail,
    )
    assert {repo: state(repo) for repo in before} == before
    assert len(store.rows("projects")) == count and not events(store, "project.checkpointed")


def test_director_offers_enrolment_and_only_the_owners_tap_lets_it_build(arena, monkeypatch):
    store, client, headers, projects, fixture = arena
    ready_accounts(store)
    monkeypatch.setattr(conversation, "auth_status", lambda account: (True, {}))
    make_repo(projects, "partnerflow-lab")
    dirty = make_repo(projects, "dirty-lab")
    (dirty / "README.md").write_text("changed\n")
    (dirty / "notes.txt").write_text("new\n")
    make_repo(projects, "feature-repo", branch="dev")
    asks = {"reply": "", "project_hint": None, "enroll_repo": None, "action": "chat", "spec": None}
    runner = FakeRunner(
        [
            {**asks, "reply": "Partnerflow Lab is a small Python app."},
            {
                **asks,
                "reply": "Happy to. Arena needs your permission to build in Partnerflow Lab: "
                "tap the button below.",
                "enroll_repo": "partnerflow-lab",
                "spec": SPEC,
            },
            # "Go ahead" before the tap, from a director that forgot enroll_repo: the offer
            # stands and nothing launches.
            {**asks, "reply": "On it.", "action": "build"},
            # A stray hint naming the unenrolled repository never picks the one enrolled
            # project instead, and nothing launches before the tap.
            {**asks, "reply": "Starting.", "project_hint": "partnerflow-lab", "action": "build"},
            # A director that wants to build on a question launches nothing either.
            {**asks, "reply": "Shall I?", "enroll_repo": "partnerflow-lab", "action": "build"},
            {**asks, "reply": "Building.", "project_hint": "Partnerflow Lab", "action": "build"},
        ]
    )
    director = conversation.Director(
        store, client.app.state.engine, run=fake_gh_run, runner=runner, projects_dir=projects
    )
    conv = conversation.new_conversation(store, project_id=fixture["id"])

    def say(text):
        director.submit(conv["id"], text)
        wait_idle(store, conv["id"])
        return conversation.messages(store, conv["id"])[-1]

    offer = {
        "name": "Partnerflow Lab",
        "repo_name": "partnerflow-lab",
        "clean": True,
        "branch": "main",
        "reason": None,
        "can_checkpoint": False,
    }
    reply = say("What does partnerflow-lab do?")
    assert "offer_enroll" not in reply
    assert store.get("conversations", conv["id"])["project_id"] == fixture["id"]
    reply = say("Let's add a CSV export to partnerflow-lab.")
    assert reply["offer_enroll"] == offer
    assert reply["text"].startswith("Happy to. Arena needs your permission")
    live = store.get("conversations", conv["id"])
    assert live["project_id"] is None and live["spec"] == SPEC  # never "build it" elsewhere
    reply = say("Go ahead.")
    assert reply["offer_enroll"] == offer and not reply.get("launch_error")
    assert reply["text"] == "On it.\n\nArena needs your permission to build in Partnerflow Lab."
    reply = say("Build it in partnerflow-lab.")
    assert reply["offer_enroll"] == offer and not reply.get("build_started")
    assert reply["text"] == "Starting.\n\nArena needs your permission to build in Partnerflow Lab."
    assert store.get("conversations", conv["id"])["project_id"] is None
    reply = say("Is partnerflow-lab using Flask?")
    assert reply["offer_enroll"] == offer
    assert not reply.get("build_started") and not reply.get("offer_build")
    assert store.rows("tasks") == [] and store.rows("goals") == []
    # The offer says plainly what is in the way, and when the owner may save unsaved work.
    local = director.catalog.local_repos()
    assert director._enroll_offer({"enroll_repo": "Dirty Lab"}, "", local) == {
        "name": "Dirty Lab",
        "repo_name": "dirty-lab",
        "clean": False,
        "branch": "main",
        "reason": DIRTY,
        "can_checkpoint": True,
    }
    assert director._enroll_offer({"enroll_repo": "feature-repo"}, "", local) == {
        "name": "Feature Repo",
        "repo_name": "feature-repo",
        "clean": True,
        "branch": "dev",
        "reason": ON_DEV,
        "can_checkpoint": False,
    }
    # The owner's own words name it when they ask to build; an enrolled name never does.
    assert director._enroll_offer({}, "Build it in partnerflow lab", local) == offer
    assert director._enroll_offer({}, "Build it in Fixture", local) is None
    # The previous offer carries over to a bare "build it", unless another project is named
    # or was chosen since.
    offered = [{"role": "assistant", "text": "", "offer_enroll": offer}]
    unset, chosen = {"project_id": None}, {"project_id": fixture["id"]}
    assert director._enroll_offer({}, "Build it", local, unset, offered) == offer
    assert director._enroll_offer({}, "Build it in Fixture", local, unset, offered) is None
    assert director._enroll_offer({}, "Build it", local, chosen, offered) is None
    assert director._enroll_offer({}, "Build it", local, unset, []) is None
    # The tap: enrolled, selected for this chat, the agreed spec kept; "build it" builds there.
    r = enrol(client, headers, "tap", name="partnerflow-lab", conversation_id=conv["id"])
    assert r.status_code == 200, r.text
    project_id = r.json()["project_id"]
    assert store.get("conversations", conv["id"])["project_id"] == project_id
    reply = say("Right, build it.")
    assert "offer_enroll" not in reply
    assert reply["build_started"] is True and reply["project"] == "Partnerflow Lab"
    tasks = store.rows("tasks")
    assert [t["project_id"] for t in tasks] == [project_id]
    assert "Add a CSV export to Partnerflow Lab" in tasks[0]["instructions"]
