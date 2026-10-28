from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from agent_arena.process import ProcessRunner, sanitized_environment


def test_output_is_bounded_and_marked(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        [sys.executable, "-c", "print('x' * 10000)"],
        cwd=tmp_path,
        timeout_seconds=5,
        max_output_bytes=100,
        env=sanitized_environment(("PATH",)),
    )
    assert result.ok
    assert result.stdout_truncated
    assert "bytes omitted by arena" in result.stdout
    assert result.stdout_total_bytes > 100


def test_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    child_code = (
        f"import time, pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).write_text('bad')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(5)"
    )
    result = ProcessRunner(termination_grace_seconds=1).run(
        [sys.executable, "-c", parent_code],
        cwd=tmp_path,
        timeout_seconds=1,
        max_output_bytes=1000,
        env=sanitized_environment(("PATH",)),
    )
    assert result.timed_out
    time.sleep(1.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
@pytest.mark.parametrize("parent_exits", [False, True])
def test_ignoring_descendants_die_without_harming_unrelated_group(
    tmp_path: Path, parent_exits: bool
) -> None:
    marker = tmp_path / "escaped-child"
    ready = tmp_path / "ready"
    child_code = (
        "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"pathlib.Path({str(ready)!r}).touch(); time.sleep(3); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    parent_code = (
        "import subprocess,sys,time,pathlib; "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        f"ready=pathlib.Path({str(ready)!r})\n"
        "while not ready.exists(): time.sleep(.01)\n" + ("" if parent_exits else "time.sleep(30)\n")
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        result = ProcessRunner(termination_grace_seconds=1).run(
            [sys.executable, "-c", parent_code],
            cwd=tmp_path,
            timeout_seconds=1,
            max_output_bytes=1000,
        )
        assert ready.exists()
        assert result.timed_out is not parent_exits
        assert result.termination == "killed"
        time.sleep(3.2 - min(result.duration_seconds, 3))
        assert not marker.exists()
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_cancellation_cleans_spawned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_wait = subprocess.Popen.wait
    interrupted: list[subprocess.Popen[bytes]] = []

    def interrupt_once(self: subprocess.Popen[bytes], *args: object, **kwargs: object) -> int:
        if not interrupted:
            interrupted.append(self)
            raise KeyboardInterrupt
        return original_wait(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess.Popen, "wait", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        ProcessRunner(termination_grace_seconds=1).run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=10,
            max_output_bytes=1000,
        )
    assert interrupted[0].poll() is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
def test_concurrent_cancellation_signals_owned_group_once(tmp_path: Path, monkeypatch) -> None:
    import signal
    import threading
    from concurrent.futures import ThreadPoolExecutor

    runner = ProcessRunner(termination_grace_seconds=1)
    ready = threading.Event()
    owned = []
    signals = []
    killpg = os.killpg

    def record_start(pid):
        assert os.getpgid(pid) == pid
        assert os.getsid(pid) == pid
        owned.append(runner._active[pid])
        ready.set()

    def record_signal(pgid, sig):
        if sig:
            signals.append((pgid, sig))
        return killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", record_signal)
    with ThreadPoolExecutor(max_workers=3) as pool:
        worker = pool.submit(
            runner.run,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=10,
            max_output_bytes=1000,
            on_start=record_start,
        )
        assert ready.wait(2)
        cancellations = [pool.submit(runner.cancel_all) for _ in range(2)]
        for cancellation in cancellations:
            cancellation.result(timeout=3)
        try:
            result = worker.result(timeout=3)
            assert result.exit_code == -signal.SIGTERM
        except InterruptedError:
            pass
    assert signals == [(owned[0].pid, signal.SIGTERM)]
    assert runner.active_count() == 0
    assert runner.termination_evidence()[0]["confirmed"] is True
    # A stale cancellation snapshot must not re-signal a potentially reused PGID.
    runner._terminate_group(owned[0])
    assert signals == [(owned[0].pid, signal.SIGTERM)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
def test_exit_race_permission_error_requires_confirmed_disappearance(tmp_path, monkeypatch):
    import errno
    import signal

    runner = ProcessRunner(termination_grace_seconds=1)
    killpg = os.killpg
    denied = []

    def exiting_group(pgid, sig):
        result = killpg(pgid, sig)
        if sig == signal.SIGTERM:
            denied.append(pgid)
            raise PermissionError(errno.EPERM, "exiting group")
        return result

    monkeypatch.setattr(os, "killpg", exiting_group)
    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=0.1,
        max_output_bytes=1000,
    )
    assert result.timed_out
    assert denied
    assert result.exit_code == -signal.SIGTERM
    evidence = runner.termination_evidence()[0]
    assert evidence["confirmed"] is True
    assert any(
        "Operation not permitted" in item or "exiting group" in item
        for item in evidence["observations"]
    )
    assert evidence["observations"][-1] == "group probe: ESRCH"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
@pytest.mark.parametrize("deny_signals", [True, False])
def test_unconfirmed_termination_is_failure_and_retains_ownership(
    tmp_path, monkeypatch, deny_signals
):
    import errno
    import signal

    from agent_arena.process import ProcessTerminationError

    runner = ProcessRunner(termination_grace_seconds=0.01)
    killpg = os.killpg
    owned = []

    def denied_or_unconfirmed(pgid, sig):
        if deny_signals:
            raise PermissionError(errno.EPERM, "test permission denied")
        if sig == signal.SIGKILL:
            return killpg(pgid, sig)
        # Model a group that remains observable after the leader is killed.
        return None

    monkeypatch.setattr(os, "killpg", denied_or_unconfirmed)
    try:
        with pytest.raises(ProcessTerminationError, match="Termination unconfirmed"):
            runner.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                timeout_seconds=0.1,
                max_output_bytes=1000,
                on_start=lambda pid: owned.append(runner._active[pid]),
            )
        assert runner.active_count() == 1
        evidence = runner.termination_evidence()[0]
        assert evidence["confirmed"] is False
        assert evidence["result"] is None
        assert (
            "PermissionError" in evidence["error"]
            if deny_signals
            else ("still exists after SIGKILL" in evidence["error"])
        )
        with pytest.raises(ProcessTerminationError):
            runner.cancel_all()
        assert len(runner.termination_evidence()) == 1
    finally:
        for process in owned:
            with suppress(ProcessLookupError):
                killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
def test_cancel_all_attempts_other_groups_after_permission_failure(tmp_path, monkeypatch):
    import errno
    import signal

    from agent_arena.process import ProcessTerminationError

    runner = ProcessRunner(termination_grace_seconds=0.01)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            start_new_session=True,
        )
        for _ in range(2)
    ]
    runner._active.update({child.pid: child for child in children})
    killpg = os.killpg

    def deny_first(pgid, sig):
        if pgid == children[0].pid:
            raise PermissionError(errno.EPERM, "test permission denied")
        return killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", deny_first)
    try:
        with pytest.raises(ProcessTerminationError):
            runner.cancel_all()
        assert children[0].poll() is None
        assert children[1].poll() == -signal.SIGTERM
        assert [item["confirmed"] for item in runner.termination_evidence()] == [False, True]
    finally:
        for child in children:
            with suppress(ProcessLookupError):
                killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
