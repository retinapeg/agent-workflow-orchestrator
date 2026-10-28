from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from weakref import WeakKeyDictionary

from .models import ProcessResult


class ProcessTerminationError(RuntimeError):
    """The runner could not confirm that its owned process group stopped."""


@dataclass
class _Termination:
    lock: threading.Lock = field(default_factory=threading.Lock)
    confirmed: bool = False
    result: str | None = None
    error: ProcessTerminationError | None = None
    observations: list[str] = field(default_factory=list)


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

    _terminations: WeakKeyDictionary[subprocess.Popen[bytes], _Termination] = field(
        default_factory=WeakKeyDictionary, init=False, repr=False, compare=False
    )
    _termination_evidence: list[dict[str, object]] = field(
        default_factory=list, init=False, repr=False, compare=False
    )

    def termination_evidence(self) -> list[dict[str, object]]:
        with self._active_lock:
            return list(self._termination_evidence)

    def reset_cancellation(self) -> None:
        self._cancelled.clear()

    def cancel_all(self) -> None:
        """Stop every process group currently owned by this runner."""

        self._cancelled.set()
        with self._active_lock:
            processes = list(self._active.values())
        failures = []
        for process in processes:
            try:
                self._terminate_group(process)
            except ProcessTerminationError as exc:
                failures.append(str(exc))
        if failures:
            raise ProcessTerminationError("; ".join(failures))

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
        on_output: Callable[[str, bytes], None] | None = None,
        on_start: Callable[[int], None] | None = None,
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
            self._terminations[process] = _Termination()
        if on_start is not None:
            try:
                on_start(process.pid)
            except BaseException:
                self._terminate_group(process)
                with self._active_lock:
                    self._active.pop(process.pid, None)
                raise
        stdout_capture = _CappedCapture(max_output_bytes)
        stderr_capture = _CappedCapture(max_output_bytes)

        def drain(stream: BinaryIO, capture: _CappedCapture, channel: str) -> None:
            try:
                while True:
                    chunk = (
                        stream.read1(65_536) if hasattr(stream, "read1") else stream.read(65_536)
                    )
                    if not chunk:
                        break
                    capture.append(chunk)
                    if on_output is not None:
                        on_output(channel, chunk)
            finally:
                stream.close()

        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            threading.Thread(
                target=drain, args=(process.stdout, stdout_capture, "stdout"), daemon=True
            ),
            threading.Thread(
                target=drain, args=(process.stderr, stderr_capture, "stderr"), daemon=True
            ),
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
                    with suppress(BrokenPipeError, OSError):
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
        except ProcessTerminationError:
            raise
        except BaseException:
            self._terminate_group(process)
            raise
        finally:
            with self._active_lock:
                if self._terminations[process].confirmed:
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
        # Cancellation and the run thread share one cleanup result. Once the group
        # is confirmed gone, never signal its numeric ID again (it can be reused).
        with self._active_lock:
            # Git cleanup also supplies a Popen it created with start_new_session.
            state = self._terminations.setdefault(process, _Termination())
        with state.lock:
            if state.confirmed:
                return state.result
            if state.error is not None:
                raise state.error
            try:
                state.result = self._stop_owned_group(process, state)
                state.confirmed = True
                return state.result
            except (OSError, subprocess.TimeoutExpired) as exc:
                state.error = ProcessTerminationError(
                    f"Termination unconfirmed for owned pid/pgid={process.pid}; "
                    f"leader_returncode={process.returncode}; "
                    f"{type(exc).__name__}: {exc}; " + "; ".join(state.observations)
                )
                raise state.error from exc
            finally:
                if state.confirmed or state.error is not None:
                    with self._active_lock:
                        self._termination_evidence.append(
                            {
                                "pid": process.pid,
                                "pgid": process.pid if os.name == "posix" else None,
                                "new_session": os.name == "posix",
                                "leader_returncode": process.returncode,
                                "confirmed": state.confirmed,
                                "result": state.result,
                                "error": str(state.error) if state.error else None,
                                "observations": list(state.observations),
                            }
                        )

    def _stop_owned_group(
        self, process: subprocess.Popen[bytes], state: _Termination
    ) -> str | None:
        if os.name != "posix":
            if process.poll() is not None:
                return None
            process.terminate()
            try:
                process.wait(timeout=self.termination_grace_seconds)
                return "terminated"
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=max(1, self.termination_grace_seconds))
                return "killed"

        def observe(message: str) -> None:
            if not state.observations or state.observations[-1] != message:
                state.observations.append(message)

        def gone() -> bool:
            # Reap our child first. Darwin may return EPERM for an exiting group
            # with no signalable members; EPERM alone never proves disappearance.
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                observe("group probe: ESRCH")
                process.wait(timeout=max(1, self.termination_grace_seconds))
                return True
            except PermissionError as exc:
                observe(f"group probe: {exc}")
            return False

        def wait_gone(seconds: float) -> bool:
            deadline = time.monotonic() + seconds
            while True:
                if gone():
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(0.01, remaining))

        # Popen created this session. Leader exit alone says nothing about its
        # descendants, including children that closed stdout/stderr.
        if gone():
            return None
        result = None
        denied: PermissionError | None = None
        for sig, label, grace in (
            (signal.SIGTERM, "terminated", self.termination_grace_seconds),
            (signal.SIGKILL, "killed", max(1, self.termination_grace_seconds)),
        ):
            try:
                os.killpg(process.pid, sig)
                observe(f"{sig.name}: sent")
                result = label
            except ProcessLookupError:
                observe(f"{sig.name}: ESRCH")
                process.wait(timeout=max(1, self.termination_grace_seconds))
                return result
            except PermissionError as exc:
                observe(f"{sig.name}: {exc}")
                denied = exc
            if wait_gone(grace):
                return result
        if denied is not None:
            raise denied
        raise TimeoutError("process group still exists after SIGKILL confirmation deadline")
