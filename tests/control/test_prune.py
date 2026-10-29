"""Round 2 E: ``prune-workspaces`` and the workspace disk usage shown in the Device dialog.

Built on the small fake-CLI project from test_control. Attempts, task states and ages are
written straight into the ledger; each workspace is a directory under ``<home>/workspaces``
and each attempt keeps an evidence directory under ``<home>/attempts`` that must survive
every prune."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

from agent_arena.control import workspaces
from agent_arena.control.__main__ import main
from agent_arena.control.engine import TERMINAL, git
from agent_arena.control.store import Store, encode
from tests.control import test_control as support
from tests.control.test_control import goal, paired_client

DAY = 86400
TRANSCRIPT = '{"type":"item.completed","item":{"type":"agent_message","text":"Implemented it"}}\n'


@pytest.fixture
def setup(tmp_path):
    return support.setup.__wrapped__(tmp_path)


def task_in(store, engine, project, state, idle_days, path="src/a.txt", **fields):
    """A task in ``state`` whose last change was ``idle_days`` ago."""
    ident = goal(store, engine, project, path)
    with store.transaction() as db:
        task = store.get("tasks", ident, db)
        data = {
            k: v
            for k, v in task.items()
            if k not in {"id", "rev", "state", "token", "project_id", "goal_id"}
        }
        data.update(fields, updated=time.time() - idle_days * DAY)
        db.execute("UPDATE tasks SET state=?,data=? WHERE id=?", (state, encode(data), ident))
    return ident


def attempt_in(store, task_id, ident, state, idle_days, workspace=True, kb=64):
    """An attempt that started and ended ``idle_days`` ago, with its evidence directory and
    (unless ``workspace`` is False) a workspace whose modification time is as old."""
    then = time.time() - idle_days * DAY
    artifact = store.home / "attempts" / ident
    artifact.mkdir(parents=True)
    (artifact / "transcript.jsonl").write_text(TRANSCRIPT)
    (artifact / "result.json").write_text('{"status": "' + state + '"}')
    data = dict(
        created=then - 60,
        phase="build",
        base="0" * 40,
        artifact_dir=str(artifact),
        workspace=str(store.home / "workspaces" / ident),
        identity=None,
        reason="fixture",
        model="test-only",
        effort="medium",
        routing={"provider": "codex", "model": "test-only"},
        instruction_count=0,
    )
    if state not in {"leased", "running", "verifying", "reviewing", "recovering"}:
        data["ended"] = then
    with store.transaction() as db:
        task = store.get("tasks", task_id, db)
        db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?)",
            (ident, task_id, "slot0", task["token"], state, encode(data)),
        )
    if workspace:
        path = store.home / "workspaces" / ident
        (path / "src").mkdir(parents=True)
        (path / "src" / "a.txt").write_bytes(b"x" * kb * 1024)
        age(path, idle_days)
    return ident


def age(path, idle_days):
    then = time.time() - idle_days * DAY
    os.utime(path, (then, then))


def cli(capsys, store, *args):
    code = main(["--home", str(store.home), "prune-workspaces", *args])
    out = capsys.readouterr().out
    return code, (json.loads(out) if "--json" in args else out)


def names(report, verdict):
    return [w["name"] for w in report["workspaces"] if w["verdict"] == verdict]


def after_survey(monkeypatch, action):
    """Run ``action`` between the survey and the removals: the window in which a retry, a
    swapped directory or another process can change things under the prune."""
    real = workspaces._survey

    def survey_then(*args, **kwargs):
        out = real(*args, **kwargs)
        action()
        return out

    monkeypatch.setattr(workspaces, "_survey", survey_then)


def theirs(where, name):
    """A directory named like a planned workspace somewhere a link could lead, aged so that
    a prune following the link would take it."""
    (where / name / "src").mkdir(parents=True)
    (where / name / "src" / "theirs.txt").write_text("not Arena's")
    age(where / name, 10)
    return where / name / "src" / "theirs.txt"


@pytest.fixture
def scene(setup, tmp_path):
    """A live attempt, a completed old one, a completed recent one, a symbolic link, a
    stray directory and an attempt directory the ledger does not know."""
    store, engine, project, _ = setup
    done_old = task_in(store, engine, project, "completed", 10)
    done_new = task_in(store, engine, project, "completed", 1)
    # Live wins even when the task already looks finished (cancel requested, worker running).
    stopping = task_in(store, engine, project, "cancelled", 30, cancel_requested=True)
    s = dict(
        store=store,
        engine=engine,
        project=project,
        old=attempt_in(store, done_old, "attempt_00000000000000a1", "passed", 10),
        recent=attempt_in(store, done_new, "attempt_00000000000000a2", "passed", 1),
        live=attempt_in(store, stopping, "attempt_00000000000000a3", "running", 30),
    )
    root = store.home / "workspaces"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    # A link inside a workspace (like a node_modules link back into the real repository).
    (root / s["old"] / "node_modules").symlink_to(outside, target_is_directory=True)
    age(root / s["old"], 10)
    (root / "attempt_00000000000000a4").symlink_to(outside, target_is_directory=True)
    (root / "notes").mkdir()
    (root / "notes" / "todo.txt").write_text("mine")
    (root / "attempt_00000000000000ff").mkdir()
    age(root / "attempt_00000000000000ff", 90)
    s.update(root=root, outside=outside)
    return s


def test_dry_run_lists_only_the_finished_old_workspace_and_changes_nothing(scene, capsys):
    store, root = scene["store"], scene["root"]
    before = sorted(p.name for p in root.iterdir())
    events = len(store_events(store))
    code, report = cli(capsys, store, "--json")
    assert code == 0 and report["applied"] is False and report["days"] == 7
    assert names(report, "prune") == [scene["old"]]
    why = {w["name"]: w["why"] for w in report["workspaces"]}
    assert why == {
        scene["old"]: "prunable",
        scene["recent"]: "recent",
        scene["live"]: "live",
        "attempt_00000000000000ff": "unknown_attempt",
    }
    assert {s["name"]: s["why"] for s in report["skipped"]} == {
        "attempt_00000000000000a4": "symlink",
        "notes": "other",
    }
    planned = report["workspaces"][[w["name"] for w in report["workspaces"]].index(scene["old"])]
    assert planned["method"] == "delete" and planned["task_state"] == "completed"
    assert planned["bytes"] == workspaces.disk_bytes(root / scene["old"]) >= 64 * 1024
    assert report["prunable_count"] == 1 and report["prunable_bytes"] == planned["bytes"]
    assert report["count"] == 4  # real attempt_* directories; the link and notes/ excluded
    # Nothing changed: same entries, no event, no log.
    assert sorted(p.name for p in root.iterdir()) == before
    assert len(store_events(store)) == events
    assert not (store.home / workspaces.LOG).exists()
    # The plain-text dry run says what it would do and how to do it.
    code, text = cli(capsys, store)
    assert code == 0
    assert "Would remove 1 (finished builds idle for at least 7 days)" in text
    assert scene["old"] in text and "Dry run: nothing was removed. Add --apply" in text
    assert "Left alone:\n" in text
    assert "attempt_00000000000000a4  symbolic link; not followed" in text
    assert "notes  not an attempt workspace" in text
    assert "attempt_00000000000000ff  not in the ledger" in text
    assert "Keeping 1 live, 1 idle for less than 7 days." in text
    assert sorted(p.name for p in root.iterdir()) == before


def test_apply_removes_exactly_the_finished_old_workspace(scene, capsys):
    store, root, outside = scene["store"], scene["root"], scene["outside"]
    size = workspaces.disk_bytes(root / scene["old"])
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 0 and report["applied"] is True and report["errors"] == []
    assert report["removed"] == [scene["old"]] and report["removed_bytes"] == size
    assert report["already_gone"] == [] and report["warnings"] == []
    assert not (root / scene["old"]).exists()
    for kept in (scene["recent"], scene["live"], "attempt_00000000000000ff", "notes"):
        assert (root / kept).is_dir()
    # The link is still a link, and what it (and the link inside the removed workspace)
    # pointed at is untouched.
    assert (root / "attempt_00000000000000a4").is_symlink()
    assert (outside / "precious.txt").read_text() == "keep me"
    assert (root / "notes" / "todo.txt").read_text() == "mine"
    # Evidence is never removed, and the Transcript view still reads it.
    for ident in (scene["old"], scene["recent"], scene["live"]):
        artifact = store.home / "attempts" / ident
        assert (artifact / "transcript.jsonl").read_text() == TRANSCRIPT
        assert (artifact / "result.json").is_file()
    client, _ = paired_client(store)
    live = client.get(f"/api/v1/attempts/{scene['old']}/live").json()
    assert live["lines"] and "Implemented it" in live["lines"][-1]["text"]
    # One event and one log line record it.
    pruned = [e for e in store_events(store) if e[0] == "workspaces.pruned"]
    assert len(pruned) == 1
    data = pruned[0][1]
    assert (data["count"], data["bytes"], data["days"], data["errors"]) == (1, size, 7.0, 0)
    assert data["reason"].startswith("Removed 1 finished build workspace (")
    log = (store.home / workspaces.LOG).read_text()
    assert "prune-workspaces: Removed 1 finished build workspace" in log and scene["old"] in log
    # A second run finds nothing more to do and records no further event.
    code, again = cli(capsys, store, "--apply", "--json")
    assert code == 0 and again["removed"] == [] and again["prunable_count"] == 0
    assert len([e for e in store_events(store) if e[0] == "workspaces.pruned"]) == 1
    code, text = cli(capsys, store, "--apply")
    assert code == 0 and "Nothing to remove: no finished build's workspace has been idle" in text
    assert "Removed 0" not in text and "Dry run" not in text


def test_days_threshold_and_validation(scene, capsys):
    store = scene["store"]
    code, report = cli(capsys, store, "--days", "0", "--json")
    assert code == 0 and sorted(names(report, "prune")) == sorted([scene["old"], scene["recent"]])
    code, report = cli(capsys, store, "--days", "30", "--json")
    assert names(report, "prune") == []
    for bad in ("-1", "nan", "inf"):
        with pytest.raises(SystemExit) as stop:
            main(["--home", str(store.home), "prune-workspaces", "--days", bad])
        assert stop.value.code == 2
    capsys.readouterr()


def test_a_home_without_a_ledger_is_refused_and_nothing_is_created(tmp_path, capsys, monkeypatch):
    """The phone's hint uses a relative --home. Run from a folder other than the Arena
    folder, it must say so and exit 1, not create an empty ledger and report nothing to do."""
    monkeypatch.chdir(tmp_path)
    expected = Path(".arena-control").absolute() / "control.sqlite3"
    for extra in ([], ["--apply"], ["--json"], ["--days", "0", "--apply"]):
        code = main(["--home", ".arena-control", "prune-workspaces", *extra])
        out, err = capsys.readouterr()
        assert code == 1 and out == ""
        assert f"No Arena control state at {expected}; nothing was changed." in err
    assert list(tmp_path.iterdir()) == []
    # An existing control home without a ledger is refused the same way and left as it is.
    (tmp_path / "home" / "workspaces").mkdir(parents=True)
    assert main(["--home", str(tmp_path / "home"), "prune-workspaces", "--apply"]) == 1
    assert sorted(p.name for p in (tmp_path / "home").iterdir()) == ["workspaces"]
    capsys.readouterr()


def test_unfinished_waiting_and_retryable_review_workspaces_are_kept(setup, capsys):
    store, engine, project, _ = setup
    keep = {}
    # A staged change waiting for the owner, and the scheduler's other terminal states that
    # wait for the owner (an interrupted stop may not even be confirmed).
    for n, state in enumerate(["ready_for_approval", "interrupted", "needs_auth", "integrating"]):
        task = task_in(store, engine, project, state, 40)
        keep[attempt_in(store, task, f"attempt_000000000000001{n}", "failed", 40)] = "unfinished"
    # A blocked review: a retry fetches the build attempt's workspace again, so that one
    # stays; the rejected review attempt's own workspace can go.
    review = task_in(store, engine, project, "blocked", 40, phase="review", repairs=1)
    build = attempt_in(store, review, "attempt_0000000000000020", "passed", 40)
    rejected = attempt_in(store, review, "attempt_0000000000000021", "failed", 40)
    with store.transaction() as db:
        task = store.get("tasks", review, db)
        data = {
            k: v
            for k, v in task.items()
            if k not in {"id", "rev", "state", "token", "project_id", "goal_id"}
        }
        data.update(build_attempt=build)
        db.execute("UPDATE tasks SET data=? WHERE id=?", (encode(data), review))
    keep[build] = "retry"
    # Once its repair budget is spent the retry is refused, so nothing will read it again.
    spent = task_in(store, engine, project, "blocked", 40, phase="direct", repairs=2)
    spent_build = attempt_in(store, spent, "attempt_0000000000000030", "passed", 40)
    with store.transaction() as db:
        task = store.get("tasks", spent, db)
        data = {
            k: v
            for k, v in task.items()
            if k not in {"id", "rev", "state", "token", "project_id", "goal_id"}
        }
        data.update(build_attempt=spent_build)
        db.execute("UPDATE tasks SET data=? WHERE id=?", (encode(data), spent))
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 0
    why = {w["name"]: w["why"] for w in report["workspaces"]}
    for ident, reason in keep.items():
        assert why[ident] == reason and (store.home / "workspaces" / ident).is_dir()
    assert sorted(report["removed"]) == sorted([rejected, spent_build])
    assert workspaces.PRUNABLE_TASK_STATES <= TERMINAL


def test_an_unconfirmed_stop_keeps_the_workspace_after_the_task_is_cancelled(setup, capsys):
    """Stop could not confirm the agent's process group ended, so the task is interrupted;
    pressing Stop again cancels it. The agent may still be running in the workspace."""
    store, engine, project, _ = setup
    task_id = goal(store, engine, project)
    attempt = engine.claim()
    workspace = Path(attempt["workspace"])
    (workspace / "src").mkdir(parents=True)
    with store.transaction() as db:
        store.update_task(
            db, store.get("tasks", task_id, db), "running", "fixture", cancel_requested=True
        )
    assert engine.finish(
        attempt,
        {
            "attempt_id": attempt["id"],
            "token": attempt["token"],
            "status": "failed",
            "reason": "Termination unconfirmed for owned pid/pgid=4242",
            "termination_unconfirmed": True,
            "checks": [],
        },
    )
    assert store.get("tasks", task_id)["state"] == "interrupted"
    client, headers = paired_client(store)
    response = client.post(
        f"/api/v1/tasks/{task_id}/cancel",
        json={"revision": store.get("tasks", task_id)["rev"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert store.get("tasks", task_id)["state"] == "cancelled"
    # Old enough for any threshold: only the unconfirmed stop keeps it.
    code, report = cli(capsys, store, "--days", "0", "--apply", "--json")
    assert code == 0 and report["removed"] == []
    [item] = report["workspaces"]
    assert (item["name"], item["verdict"], item["why"]) == (attempt["id"], "keep", "unconfirmed")
    assert item["reason"] == "stop not confirmed" and item["task_state"] == "cancelled"
    assert workspace.is_dir()
    code, text = cli(capsys, store, "--days", "0")
    assert "Keeping 1 whose stop was not confirmed." in text


def test_an_attempt_whose_task_is_missing_is_kept(setup, capsys):
    store, engine, project, _ = setup
    task = task_in(store, engine, project, "completed", 30)
    ident = attempt_in(store, task, "attempt_00000000000000d1", "passed", 30)
    # A ledger edited by hand (foreign keys are off on a plain connection).
    with contextlib.closing(sqlite3.connect(store.path)) as db:
        db.execute("DELETE FROM tasks WHERE id=?", (task,))
        db.commit()
    code, report = cli(capsys, store, "--days", "0", "--apply", "--json")
    assert code == 0 and report["removed"] == [] and report["errors"] == []
    [item] = report["workspaces"]
    assert (item["name"], item["verdict"], item["why"]) == (ident, "keep", "unknown_task")
    assert (store.home / "workspaces" / ident / "src" / "a.txt").is_file()
    code, text = cli(capsys, store)
    assert f"Left alone:\n  {ident}  its task is not in the ledger" in text


def test_a_registered_git_worktree_is_only_deleted_and_no_git_command_runs(
    setup, capsys, monkeypatch, tmp_path
):
    """Workspaces are plain clones (``git clone --no-hardlinks``), never worktrees. Even one
    registered as a worktree is just deleted: no git command runs in any enrolled repository,
    so a locked worktree is never forced and the owner's own registrations are never pruned
    (git later reports ours as prunable, which is harmless)."""
    store, engine, project, _ = setup
    repo = Path(project["staging"])
    task = task_in(store, engine, project, "completed", 12)
    ident = attempt_in(store, task, "attempt_00000000000000b1", "passed", 12, workspace=False)
    path = store.home / "workspaces" / ident
    path.parent.mkdir(exist_ok=True)
    git(repo, "worktree", "add", "--detach", str(path), "HEAD")
    (path / "src" / "scratch.txt").write_text("work in progress")
    age(path, 12)
    # The owner's own worktree, temporarily missing (an unplugged drive, say).
    mine = tmp_path / "owner-worktree"
    git(repo, "worktree", "add", "--detach", str(mine), "HEAD")
    mine.rename(tmp_path / "owner-worktree-away")
    registered = sorted(p.name for p in (repo / ".git" / "worktrees").iterdir())
    assert registered == sorted([ident, "owner-worktree"])

    def no_commands(*args, **kwargs):
        raise AssertionError(f"prune-workspaces ran a command: {args!r}")

    with monkeypatch.context() as m:
        m.setattr(subprocess, "run", no_commands)
        m.setattr(subprocess, "Popen", no_commands)
        code, report = cli(capsys, store, "--apply", "--json")
    assert code == 0 and report["removed"] == [ident] and report["errors"] == []
    [item] = [w for w in report["workspaces"] if w["name"] == ident]
    assert item["method"] == "delete" and "repository" not in item
    assert not path.exists()
    assert sorted(p.name for p in (repo / ".git" / "worktrees").iterdir()) == registered
    assert "prunable" in git(repo, "worktree", "list", "--porcelain")
    assert (store.home / "attempts" / ident / "transcript.jsonl").is_file()
    assert git(repo, "status", "--porcelain") == ""


def test_recently_touched_workspace_is_kept(scene, capsys):
    """Old ledger timestamps, but someone worked in the directory today."""
    store = scene["store"]
    age(scene["root"] / scene["old"], 0)
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 0 and report["removed"] == []
    assert {w["name"]: w["why"] for w in report["workspaces"]}[scene["old"]] == "recent"
    assert (scene["root"] / scene["old"]).is_dir()


def test_a_retry_during_the_prune_keeps_the_workspace(scene, capsys, monkeypatch):
    """The ledger is read again right before each removal: a task retried after the survey
    is no longer finished, so it stays."""
    store = scene["store"]
    task_id = store.get("attempts", scene["old"])["task_id"]

    def retry():
        with store.transaction() as db:
            store.update_task(db, store.get("tasks", task_id, db), "ready", "Explicit retry")

    after_survey(monkeypatch, retry)
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 0 and report["removed"] == []
    [item] = [w for w in report["workspaces"] if w["name"] == scene["old"]]
    assert item["why"] == "unfinished" and item["reason"].endswith("(changed since the survey)")
    assert (scene["root"] / scene["old"]).is_dir()
    assert not [e for e in store_events(store) if e[0] == "workspaces.pruned"]


def test_a_workspace_swapped_for_a_link_during_the_prune_is_not_followed(
    scene, capsys, monkeypatch
):
    store, root, outside = scene["store"], scene["root"], scene["outside"]
    target = root / scene["old"]

    def swap():
        shutil.rmtree(target)
        target.symlink_to(outside, target_is_directory=True)

    after_survey(monkeypatch, swap)
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 1 and report["removed"] == [] and report["already_gone"] == []
    assert report["errors"] == [
        dict(name=scene["old"], error="no longer a plain directory; left alone")
    ]
    assert target.is_symlink() and (outside / "precious.txt").read_text() == "keep me"


@pytest.mark.parametrize("swap", ["link", "loop", "home"])
def test_a_workspaces_directory_swapped_after_the_survey_refuses_the_whole_apply(
    scene, capsys, monkeypatch, tmp_path, swap
):
    """The survey pins the real path of <home>/workspaces. If that directory becomes a link
    (to a tree with the same attempt names, or to itself), or the control home above it is
    replaced by a link to a copy, nothing is removed anywhere."""
    store, root, old = scene["store"], scene["root"], scene["old"]
    home = store.home
    moved = root.with_name("workspaces.moved")
    watched = [moved / old / "src" / "a.txt"]
    homes = [home]
    if swap == "link":
        watched.append(theirs(tmp_path / "elsewhere", old))
    elif swap == "home":
        moved = home.with_name("home.moved")
        watched = [moved / "workspaces" / old / "src" / "a.txt"]
        watched.append(tmp_path / "copy" / "workspaces" / old / "src" / "a.txt")
        homes = [moved, tmp_path / "copy"]

    def replace():
        if swap == "home":
            # The ledger by online backup: its WAL may be checkpointed away mid-copy.
            ledger = shutil.ignore_patterns("control.sqlite3*")
            shutil.copytree(home, tmp_path / "copy", symlinks=True, ignore=ledger)
            store.backup(tmp_path / "copy" / "control.sqlite3")
            home.rename(moved)
            home.symlink_to(tmp_path / "copy", target_is_directory=True)
            return
        root.rename(moved)
        if swap == "link":
            root.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        else:
            os.symlink("workspaces", root)

    after_survey(monkeypatch, replace)
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 1 and report["refused"] and report["applied"] is True
    expected = "no longer resolves" if swap == "home" else "became a symbolic link"
    assert expected in report["refused"] and "nothing was removed" in report["refused"]
    assert report["removed"] == report["errors"] == report["already_gone"] == []
    for path in watched:
        assert path.is_file(), path
    assert not any((h / workspaces.LOG).exists() for h in homes)


def test_a_workspaces_directory_swapped_during_the_removals_is_not_followed(
    scene, capsys, monkeypatch, tmp_path
):
    """Each workspace is checked against the pinned path again right before its removal,
    after the ledger re-check."""
    store, root, old = scene["store"], scene["root"], scene["old"]
    precious = theirs(tmp_path / "elsewhere", old)
    real = workspaces._recheck

    def recheck_then_swap(*args):
        fresh = real(*args)
        root.rename(root.with_name("workspaces.moved"))
        root.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        return fresh

    monkeypatch.setattr(workspaces, "_recheck", recheck_then_swap)
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 1 and report["refused"] is None and report["removed"] == []
    assert report["errors"] == [
        dict(name=old, error="resolves outside the workspaces directory; left alone")
    ]
    assert precious.read_text() == "not Arena's"
    assert (root.with_name("workspaces.moved") / old / "src" / "a.txt").is_file()


def test_permission_fallback_never_changes_what_a_link_points_at(
    scene, capsys, monkeypatch, tmp_path
):
    """A check left a read-only directory, so the first rmtree fails. The retry makes the
    workspace's own directories writable but must not chmod through web/node_modules, a link
    to a read-only directory outside."""
    store, root, old = scene["store"], scene["root"], scene["old"]
    shared = tmp_path / "shared-node-modules"
    (shared / "pkg").mkdir(parents=True)
    (shared / "pkg" / "index.js").write_text("module.exports = 1\n")
    shared.chmod(0o555)
    (root / old / "web").mkdir()
    (root / old / "web" / "node_modules").symlink_to(shared, target_is_directory=True)
    age(root / old, 10)
    real = shutil.rmtree
    calls = []

    def read_only_first(path, *args, **kwargs):
        calls.append(str(path))
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(workspaces.shutil, "rmtree", read_only_first)
    code, report = cli(capsys, store, "--apply", "--json")
    monkeypatch.undo()
    assert code == 0 and report["removed"] == [old] and report["errors"] == []
    assert calls == [str(root / old)] * 2 and not (root / old).exists()
    assert stat.S_IMODE(shared.stat().st_mode) == 0o555
    assert (shared / "pkg" / "index.js").read_text() == "module.exports = 1\n"
    assert (scene["outside"] / "precious.txt").read_text() == "keep me"
    shared.chmod(0o755)


def test_a_workspace_removed_by_someone_else_is_already_gone_not_an_error(
    scene, capsys, monkeypatch
):
    store, root, old = scene["store"], scene["root"], scene["old"]
    after_survey(monkeypatch, lambda: shutil.rmtree(root / old))
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 0 and report["errors"] == [] and report["removed"] == []
    assert report["already_gone"] == [old]
    text = workspaces.render(report)
    assert "Removed 0 of 1 prunable" in text and "; 1 already gone:" in text
    assert f"{old}" in text and "[already gone]" in text and "[not removed]" not in text
    log = (store.home / workspaces.LOG).read_text()
    assert "prune-workspaces: Removed 0 finished build workspaces (0 B)" in log
    assert "1 already gone" in log


def test_a_busy_ledger_costs_only_the_event_never_the_log_or_the_summary(
    scene, capsys, monkeypatch
):
    """Another writer holds the ledger past the busy timeout when the event is written."""
    store, root, old = scene["store"], scene["root"], scene["old"]
    connect = Store.connect

    def impatient(self, timeout=10.0):
        db = connect(self, timeout)
        db.execute("PRAGMA busy_timeout=100")
        return db

    monkeypatch.setattr(Store, "connect", impatient)
    with contextlib.closing(sqlite3.connect(store.path, isolation_level=None)) as writer:
        after_survey(monkeypatch, lambda: writer.execute("BEGIN IMMEDIATE"))
        code, text = cli(capsys, store, "--apply")
        writer.execute("ROLLBACK")
    assert code == 0 and not (root / old).exists()
    assert "Removed 1 of 1 prunable" in text
    assert "Note: ledger event not recorded (database busy)." in text
    log = (store.home / workspaces.LOG).read_text().splitlines()
    assert "prune-workspaces: Removed 1 finished build workspace" in log[0] and old in log[0]
    assert log[1].endswith("prune-workspaces: ledger event not recorded (database busy)")
    assert not [e for e in store_events(store) if e[0] == "workspaces.pruned"]


def test_symlinked_workspaces_directory_is_refused(setup, tmp_path, capsys):
    store, engine, project, _ = setup
    task = task_in(store, engine, project, "completed", 20)
    ident = attempt_in(store, task, "attempt_00000000000000c1", "passed", 20, workspace=False)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ident).mkdir(parents=True)
    (store.home / "workspaces").symlink_to(elsewhere, target_is_directory=True)
    code, report = cli(capsys, store, "--apply", "--json")
    assert code == 1 and report["refused"] and report["removed"] == []
    assert (elsewhere / ident).is_dir()
    code, text = cli(capsys, store, "--apply")
    assert code == 1 and text.startswith("Refused:")


def test_disk_usage_endpoint_matches_the_survey_and_is_cached(scene):
    store, root = scene["store"], scene["root"]
    client, _ = paired_client(store)
    cache = client.app.state.workspace_usage
    clock = [1000.0]
    cache.clock = lambda: clock[0]
    r = client.get("/api/v1/system")
    assert r.status_code == 200, r.text
    disk = r.json()["workspaces"]
    survey = workspaces.survey(store)
    real = [root / n for n in (scene["old"], scene["recent"], scene["live"])]
    real.append(root / "attempt_00000000000000ff")
    assert disk["count"] == survey["count"] == 4
    assert disk["bytes"] == survey["bytes"] == sum(workspaces.disk_bytes(p) for p in real)
    assert disk["prunable_count"] == survey["prunable_count"] == 1
    assert disk["prunable_bytes"] == workspaces.disk_bytes(root / scene["old"])
    assert disk["days"] == 7
    # The oldest is the unknown directory left 90 days ago (its own modification time).
    assert abs(disk["oldest"] - (time.time() - 90 * DAY)) < 60
    assert abs(disk["measured"] - time.time()) < 60
    assert not any(isinstance(v, str) and "/" in v for v in disk.values())  # no host paths
    assert cache.walks == 1
    # Another finished build ages past the threshold: the cache keeps the earlier walk...
    later = task_in(store, scene["engine"], scene["project"], "cancelled", 9)
    attempt_in(store, later, "attempt_00000000000000a9", "cancelled", 9)
    clock[0] += workspaces.USAGE_TTL_SECONDS - 1
    assert client.get("/api/v1/system").json()["workspaces"] == disk
    # ...the polled overview never walks...
    assert client.get("/api/v1/overview").status_code == 200
    assert cache.walks == 1
    # ...and five minutes later the next request walks again.
    clock[0] += 1
    fresh = client.get("/api/v1/system").json()["workspaces"]
    assert (fresh["count"], fresh["prunable_count"]) == (5, 2) and cache.walks == 2
    # Pairing is required, like every other host fact.
    anonymous = client.__class__(client.app, base_url="http://127.0.0.1:8790")
    assert anonymous.get("/api/v1/system").status_code == 401


def test_disk_bytes_counts_allocation_once_and_never_follows_links(tmp_path):
    tree = tmp_path / "tree"
    (tree / "d").mkdir(parents=True)
    (tree / "d" / "f").write_bytes(b"x" * 100_000)
    os.link(tree / "d" / "f", tree / "hard")
    outside = tmp_path / "big"
    outside.mkdir()
    (outside / "blob").write_bytes(b"y" * 1_000_000)
    (tree / "link").symlink_to(outside, target_is_directory=True)
    total = workspaces.disk_bytes(tree)
    assert 100_000 <= total < 1_000_000
    assert workspaces.disk_bytes(tree / "link") < 4096 * 4  # the link itself only
    assert workspaces.disk_bytes(tmp_path / "missing") == 0


def store_events(store):
    with contextlib.closing(store.connect()) as db:
        return [(r["kind"], json.loads(r["data"])) for r in db.execute("SELECT * FROM events")]
