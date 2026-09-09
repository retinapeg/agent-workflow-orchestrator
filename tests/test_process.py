from __future__ import annotations

import os
import subprocess
import sys
import time
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
