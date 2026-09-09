from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from .models import ProcessResult


class _CappedCapture:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.head_limit = maximum // 2
        self.tail_limit = maximum - self.head_limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def append(self, chunk: bytes) -> None:
        self.total += len(chunk)
        head_room = self.head_limit - len(self.head)
        if head_room > 0:
            taken = min(head_room, len(chunk))
            self.head.extend(chunk[:taken])
            chunk = chunk[taken:]
        if chunk and self.tail_limit:
            self.tail.extend(chunk)
            if len(self.tail) > self.tail_limit:
                del self.tail[: len(self.tail) - self.tail_limit]

    @property
    def truncated(self) -> bool:
        return self.total > self.maximum

    def text(self) -> str:
        if self.truncated:
            removed = self.total - len(self.head) - len(self.tail)
            marker = f"\n... <{removed} bytes omitted by arena> ...\n".encode()
            data = bytes(self.head) + marker + bytes(self.tail)
        else:
            data = bytes(self.head) + bytes(self.tail)
        return data.decode("utf-8", errors="replace")


def sanitized_environment(
    passthrough: Sequence[str], additions: Mapping[str, str] | None = None
) -> dict[str, str]:
    env = {name: os.environ[name] for name in passthrough if name in os.environ}
    env.setdefault("PATH", os.defpath)
    env.setdefault("LANG", "C.UTF-8")
    env["PYTHONUNBUFFERED"] = "1"
    if additions:
        env.update(additions)
    return env


@dataclass(frozen=True)
class ProcessRunner:
    termination_grace_seconds: int = 5
    _cancelled: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False, compare=False
    )
    _active: dict[int, subprocess.Popen[bytes]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _active_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def reset_cancellation(self) -> None:
        self._cancelled.clear()

    def cancel_all(self) -> None:
        """Stop every process group currently owned by this runner."""

        self._cancelled.set()
        with self._active_lock:
            processes = list(self._active.values())
        for process in processes:
            self._terminate_group(process)

    def active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
        stdin_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        if not argv or any("\x00" in part for part in argv):
            raise ValueError("argv must contain non-NUL strings")
        started = time.monotonic()
        # Holding the registry lock across the cancellation check and spawn
        # closes the race where cancel_all() otherwise sees no child just before
        # this thread creates one.
        with self._active_lock:
            if self._cancelled.is_set():
                raise InterruptedError("the coordinator cancelled this process invocation")
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
            self._active[process.pid] = process
        stdout_capture = _CappedCapture(max_output_bytes)
        stderr_capture = _CappedCapture(max_output_bytes)

        def drain(stream: BinaryIO, capture: _CappedCapture) -> None:
            try:
                while True:
                    chunk = stream.read(65_536)
                    if not chunk:
                        break
                    capture.append(chunk)
            finally:
                stream.close()

        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            threading.Thread(target=drain, args=(process.stdout, stdout_capture), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr_capture), daemon=True),
        ]
        for reader in readers:
            reader.start()

        writer: threading.Thread | None = None
        if stdin_text is not None:
            stdin_stream = process.stdin
            assert stdin_stream is not None

            def write_input() -> None:
                try:
                    stdin_stream.write(stdin_text.encode("utf-8"))
                    stdin_stream.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    stdin_stream.close()

            writer = threading.Thread(target=write_input, daemon=True)
            writer.start()

        timed_out = False
        termination: str | None = None
        cancelled = False
        try:
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if self._cancelled.is_set():
                    cancelled = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=min(0.1, remaining))
            # Descendants may outlive a successful leader, including children that
            # have closed their output pipes. Always clean the owned session group.
            termination = self._terminate_group(process)
        except BaseException:
            self._terminate_group(process)
            raise
        finally:
            with self._active_lock:
                self._active.pop(process.pid, None)
            if writer:
                writer.join(timeout=1)
            for reader in readers:
                reader.join(timeout=max(1, self.termination_grace_seconds))
        if cancelled:
            raise InterruptedError("the coordinator cancelled this process invocation")
        duration = time.monotonic() - started
        return ProcessResult(
            argv=tuple(argv),
            cwd=str(cwd),
            exit_code=process.returncode,
            timed_out=timed_out,
            duration_seconds=duration,
            stdout=stdout_capture.text(),
            stderr=stderr_capture.text(),
            stdout_total_bytes=stdout_capture.total,
            stderr_total_bytes=stderr_capture.total,
            stdout_truncated=stdout_capture.truncated,
            stderr_truncated=stderr_capture.truncated,
            termination=termination,
        )

    def _terminate_group(self, process: subprocess.Popen[bytes]) -> str | None:
        if os.name != "posix":
            if process.poll() is not None:
                return None
            process.terminate()
            try:
                process.wait(timeout=self.termination_grace_seconds)
                return "terminated"
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.termination_grace_seconds)
                return "killed"

        # Popen created a new session, so this group belongs to this invocation.
        # Its lifetime is independent of its leader: do not gate killpg on poll().
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=max(1, self.termination_grace_seconds))
            return None
        deadline = time.monotonic() + self.termination_grace_seconds
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                process.wait(timeout=max(1, self.termination_grace_seconds))
                return "terminated"
            except PermissionError:
                # macOS can transiently report EPERM for a group whose last
                # member is exiting. Treat this probe as inconclusive and keep
                # waiting; actual termination failures must still propagate.
                pass
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=max(1, self.termination_grace_seconds))
        return "killed"
