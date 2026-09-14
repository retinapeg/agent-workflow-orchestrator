from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .audit import AuditStore
from .config import ArenaConfig
from .errors import DeadlineExceeded, RepositoryError
from .models import CandidateSnapshot
from .process import ProcessRunner

SAFE_LABEL = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _run_git(
    repo: Path,
    *args: str,
    timeout: float = 180,
    check: bool = True,
    env: dict[str, str] | None = None,
    max_output_bytes: int = 8_000_000,
    deadline: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded("the total run deadline was exhausted during a Git operation")
        timeout = min(timeout, remaining)
    command = ["git", "--literal-pathspecs", "-C", str(repo), *args]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
            env=env,
        )
    except OSError as exc:
        raise RepositoryError(
            f"git command failed to start: {' '.join(command[:4])}: {exc}"
        ) from exc
    outputs = [bytearray(), bytearray()]
    overflow = threading.Event()

    def capture(stream: object, output: bytearray) -> None:
        try:
            while chunk := stream.read(65_536):  # type: ignore[attr-defined]
                remaining = max_output_bytes - len(output)
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
        finally:
            stream.close()  # type: ignore[attr-defined]

    readers = [
        threading.Thread(target=capture, args=(stream, output), daemon=True)
        for stream, output in zip((process.stdout, process.stderr), outputs, strict=True)
    ]
    cleanup = ProcessRunner(termination_grace_seconds=1)
    try:
        for reader in readers:
            reader.start()
        operation_deadline = time.monotonic() + timeout
        while process.poll() is None:
            if overflow.is_set():
                raise RepositoryError("git output exceeds the configured byte limit")
            if time.monotonic() >= operation_deadline:
                raise RepositoryError("git command exceeded its timeout")
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=min(0.05, max(0.001, operation_deadline - time.monotonic())))
    finally:
        cleanup._terminate_group(process)
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=2)
    if overflow.is_set():
        raise RepositoryError("git output exceeds the configured byte limit")
    if any(reader.is_alive() for reader in readers):
        raise RepositoryError("git output pipes remained open after process cleanup")
    result = subprocess.CompletedProcess(command, process.returncode, *map(bytes, outputs))
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RepositoryError(f"git {' '.join(args)} failed: {message[:2000]}")
    return result


def _git_text(repo: Path, *args: str, **kwargs: object) -> str:
    result = _run_git(repo, *args, **kwargs)  # type: ignore[arg-type]
    return result.stdout.decode("utf-8", errors="strict").strip()


def _redact_remote(url: str) -> str:
    if "://" not in url:
        if "@" in url and ":" in url:
            _, suffix = url.split("@", 1)
            return f"<redacted-user>@{suffix}"
        return url
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = hostname + port
    return urlunsplit((parts.scheme, netloc, parts.path, "<redacted>" if parts.query else "", ""))


def _path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


@dataclass(frozen=True)
class SourceEvidence:
    source_path: str
    git_common_dir: str
    branch: str | None
    head: str
    base_ref: str
    base_commit: str
    base_tree: str
    clean: bool
    status: str
    remotes: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class EngineerWorkspaceEvidence:
    """Coordinator-observed identity of one isolated engineer worktree."""

    engineer_id: str
    branch: str
    worktree_path: str
    base_commit: str
    baseline_tree: str


class RepositoryManager:
    def __init__(
        self,
        source: Path,
        base_ref: str,
        config: ArenaConfig,
        audit: AuditStore,
        deadline: float | None = None,
    ) -> None:
        self.requested_source = source.expanduser().resolve()
        self.base_ref = base_ref
        self.config = config
        self.audit = audit
        self.deadline = deadline
        self.source = self._top_level(self.requested_source)
        self.private_repo = audit.path("private/repo")
        self.worktrees_root = audit.path("private/worktrees")
        self._branches: dict[str, str] = {}
        self._engineer_workspaces: dict[str, EngineerWorkspaceEvidence] = {}

    def _run(self, repo: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return _run_git(
            repo,
            *args,
            deadline=self.deadline,
            **kwargs,  # type: ignore[arg-type]
        )

    def _text(self, repo: Path, *args: str, **kwargs: object) -> str:
        return self._run(repo, *args, **kwargs).stdout.decode("utf-8", errors="strict").strip()

    def _top_level(self, path: Path) -> Path:
        if not path.exists():
            raise RepositoryError(f"source repository does not exist: {path}")
        result = self._run(path, "rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            raise RepositoryError(f"not a Git repository: {path}")
        return Path(result.stdout.decode().strip()).resolve()

    def inspect_source(self) -> SourceEvidence:
        status_raw = self._run(
            self.source, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout.decode("utf-8", errors="replace")
        clean = not status_raw.strip()
        if self.config.run.require_clean_source and not clean:
            raise RepositoryError(
                "source repository is dirty; commit/stash the intended snapshot or set "
                "run.require_clean_source=false explicitly"
            )
        base_commit = self._text(
            self.source, "rev-parse", "--verify", f"{self.base_ref}^{{commit}}"
        )
        base_tree = self._text(self.source, "rev-parse", f"{base_commit}^{{tree}}")
        branch_result = self._run(
            self.source, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        branch = branch_result.stdout.decode().strip() if branch_result.returncode == 0 else None
        common = self._text(self.source, "rev-parse", "--git-common-dir")
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (self.source / common_path).resolve()
        remotes: list[dict[str, str]] = []
        remote_output = self._run(self.source, "remote", "-v").stdout.decode(
            "utf-8", errors="replace"
        )
        for line in remote_output.splitlines():
            fields = line.split()
            if len(fields) >= 3:
                remotes.append(
                    {"name": fields[0], "url": _redact_remote(fields[1]), "mode": fields[2]}
                )
        return SourceEvidence(
            source_path=str(self.source),
            git_common_dir=str(common_path),
            branch=branch,
            head=self._text(self.source, "rev-parse", "HEAD"),
            base_ref=self.base_ref,
            base_commit=base_commit,
            base_tree=base_tree,
            clean=clean,
            status=status_raw,
            remotes=tuple(remotes),
        )

    def create_private_clone(self, evidence: SourceEvidence) -> None:
        self.private_repo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._run(
            self.source,
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(self.source),
            str(self.private_repo),
            timeout=600,
        )
        self._run(self.private_repo, "remote", "remove", "origin")
        self._run(self.private_repo, "checkout", "--detach", evidence.base_commit)
        alternates = self.private_repo / ".git/objects/info/alternates"
        if alternates.exists():
            raise RepositoryError("private clone unexpectedly uses shared object alternates")
        private_tree = self._text(self.private_repo, "rev-parse", "HEAD^{tree}")
        if private_tree != evidence.base_tree:
            raise RepositoryError("private clone tree does not match the frozen source tree")
        git_dir = Path(self._text(self.private_repo, "rev-parse", "--absolute-git-dir")).resolve()
        if self.private_repo.resolve() not in git_dir.parents:
            raise RepositoryError("private clone Git directory escaped the private repository")
        exclude_path = git_dir / "info/exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        configured = "\n".join(self.config.run.untracked_artifact_excludes)
        exclude_path.write_text(
            existing.rstrip()
            + "\n\n# Agent Arena untracked runtime artifacts\n"
            + configured
            + "\n",
            encoding="utf-8",
        )
        os.chmod(exclude_path, 0o600)
        self.worktrees_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def create_engineer_worktree(self, engineer_id: str, base_commit: str) -> Path:
        if not SAFE_LABEL.fullmatch(engineer_id):
            raise RepositoryError(f"unsafe engineer id: {engineer_id}")
        branch = f"arena/{self.audit.run_id}/{engineer_id}"
        path = self.worktrees_root / "engineers" / engineer_id
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._run(self.private_repo, "worktree", "add", "-b", branch, str(path), base_commit)
        actual_path = Path(self._text(path, "rev-parse", "--show-toplevel")).resolve()
        actual_branch = self._text(path, "symbolic-ref", "--quiet", "--short", "HEAD")
        actual_commit = self._text(path, "rev-parse", "HEAD")
        actual_tree = self._text(path, "rev-parse", "HEAD^{tree}")
        expected_tree = self._text(self.private_repo, "rev-parse", f"{base_commit}^{{tree}}")
        if actual_path != path.resolve():
            raise RepositoryError(
                f"worktree path for {engineer_id} does not match its Git identity"
            )
        if actual_branch != branch:
            raise RepositoryError(
                f"worktree branch for {engineer_id} does not match its allocation"
            )
        if actual_commit != base_commit:
            raise RepositoryError(
                f"worktree for {engineer_id} does not point at the frozen baseline"
            )
        if actual_tree != expected_tree:
            raise RepositoryError(f"worktree for {engineer_id} does not match the common baseline")
        self._branches[engineer_id] = actual_branch
        self._engineer_workspaces[engineer_id] = EngineerWorkspaceEvidence(
            engineer_id=engineer_id,
            branch=actual_branch,
            worktree_path=str(actual_path),
            base_commit=actual_commit,
            baseline_tree=actual_tree,
        )
        return actual_path

    def engineer_workspace_records(self) -> tuple[EngineerWorkspaceEvidence, ...]:
        """Return coordinator-observed worktree records in deterministic engineer-ID order."""

        return tuple(
            self._engineer_workspaces[engineer_id]
            for engineer_id in sorted(self._engineer_workspaces)
        )

    def advance_last_green(self, commit: str, previous: str | None = None) -> str:
        """Advance this run's private last-green ref, optionally as a compare-and-swap."""

        ref = f"refs/heads/arena/{self.audit.run_id}/last-green"
        arguments = ["update-ref", ref, commit]
        if previous is not None:
            arguments.append(previous)
        self._run(self.private_repo, *arguments)
        return ref

    def reset_engineer_worktree(self, engineer_id: str, workspace: Path, commit: str) -> None:
        """Discard a rejected private candidate while its committed evidence remains reachable."""

        expected_branch = self._branches.get(engineer_id)
        actual_top = Path(self._text(workspace, "rev-parse", "--show-toplevel")).resolve()
        actual_branch = self._text(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
        if (
            expected_branch is None
            or actual_top != workspace.resolve()
            or actual_branch != expected_branch
        ):
            raise RepositoryError(f"refusing to reset an unverified worktree for {engineer_id}")
        self._run(workspace, "reset", "--hard", commit)
        self._run(workspace, "clean", "-fd")

    def create_detached_worktree(self, label: str, commit: str) -> Path:
        if not SAFE_LABEL.fullmatch(label):
            raise RepositoryError(f"unsafe worktree label: {label}")
        path = self.worktrees_root / "ephemeral" / label
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            self.remove_worktree(path)
        self._run(self.private_repo, "worktree", "add", "--detach", str(path), commit)
        return path.resolve()

    def remove_worktree(self, path: Path) -> None:
        resolved = path.resolve()
        if self.worktrees_root not in resolved.parents:
            raise RepositoryError(
                f"refusing to remove worktree outside the private run: {resolved}"
            )
        # Cleanup must remain possible after the contest deadline expires.
        _run_git(
            self.private_repo,
            "worktree",
            "remove",
            "--force",
            str(resolved),
            check=False,
            timeout=30,
        )
        if resolved.exists():
            shutil.rmtree(resolved)
        _run_git(self.private_repo, "worktree", "prune", check=False, timeout=30)

    def freeze_candidate(
        self,
        engineer_id: str,
        generation: str,
        workspace: Path,
        baseline_commit: str,
        required_ancestor: str,
    ) -> CandidateSnapshot:
        expected_branch = self._branches.get(engineer_id)
        if expected_branch is None:
            raise RepositoryError(f"no registered worktree for {engineer_id}")
        actual_top = Path(self._text(workspace, "rev-parse", "--show-toplevel")).resolve()
        if actual_top != workspace.resolve():
            raise RepositoryError(f"{engineer_id} worktree identity changed")
        branch = self._text(workspace, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch != expected_branch:
            raise RepositoryError(
                f"{engineer_id} changed branch from {expected_branch!r} to {branch!r}"
            )
        head = self._text(workspace, "rev-parse", "HEAD")
        ancestry = self._run(
            workspace, "merge-base", "--is-ancestor", required_ancestor, head, check=False
        )
        if ancestry.returncode != 0:
            message = (
                f"{engineer_id} candidate no longer descends from required commit "
                f"{required_ancestor}"
            )
            raise RepositoryError(message)

        changed_before = self._run(
            workspace, "ls-files", "-m", "-d", "-o", "--exclude-standard", "-z"
        ).stdout.split(b"\x00")
        committed_or_staged = self._run(
            workspace, "diff", "--no-renames", "--name-only", "-z", baseline_commit
        ).stdout.split(b"\x00")
        paths_before = sorted(
            {
                item.decode("utf-8", errors="strict")
                for item in changed_before + committed_or_staged
                if item
            }
        )
        if len(paths_before) > self.config.limits.max_changed_files:
            raise RepositoryError(
                f"{engineer_id} changed {len(paths_before)} files; limit is "
                f"{self.config.limits.max_changed_files}"
            )
        total_existing_bytes = 0
        for relative in paths_before:
            candidate = workspace / relative
            if candidate.exists() and not candidate.is_dir():
                total_existing_bytes += candidate.lstat().st_size
        if total_existing_bytes > self.config.limits.max_diff_bytes * 2:
            raise RepositoryError(
                f"{engineer_id} changed-file bytes exceed the pre-freeze safety limit"
            )

        self._run(workspace, "add", "-A", "--", ".")
        changed_raw = self._run(
            workspace, "diff", "--cached", "--no-renames", "--name-only", "-z", baseline_commit
        ).stdout
        changed_files = tuple(
            sorted(
                item.decode("utf-8", errors="strict") for item in changed_raw.split(b"\x00") if item
            )
        )
        if len(changed_files) > self.config.limits.max_changed_files:
            raise RepositoryError(
                f"{engineer_id} candidate contains too many changed files after staging"
            )
        # Inspect object sizes from both complete trees, including agent-created
        # commits and deleted baseline files, before asking Git to render a patch.
        staged_tree = self._text(workspace, "write-tree")
        blob_bytes = 0
        if changed_files:
            for ref in (baseline_commit, staged_tree):
                entries = self._run(
                    workspace, "ls-tree", "-r", "-l", "-z", ref, "--", *changed_files
                ).stdout.split(b"\x00")
                for entry in entries:
                    if not entry:
                        continue
                    metadata = entry.split(b"\t", 1)[0].split()
                    if metadata[1] == b"blob":
                        size = int(metadata[3])
                        blob_bytes += size
                        if size > self.config.limits.max_diff_bytes:
                            raise RepositoryError("candidate diff contains an oversized blob")
                if blob_bytes > self.config.limits.max_diff_bytes * 2:
                    raise RepositoryError("candidate diff object bytes exceed the safety limit")
        violations: list[str] = []
        for path in changed_files:
            if _path_matches(path, self.config.run.protected_paths):
                violations.append(f"protected path changed: {path}")
            if self.config.run.allowed_paths and not _path_matches(
                path, self.config.run.allowed_paths
            ):
                violations.append(f"path outside allowed scope: {path}")

        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "Agent Arena",
                "GIT_AUTHOR_EMAIL": "agent-arena@localhost",
                "GIT_COMMITTER_NAME": "Agent Arena",
                "GIT_COMMITTER_EMAIL": "agent-arena@localhost",
            }
        )
        self._run(
            workspace,
            "commit",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            f"arena: freeze {engineer_id} {generation}",
            env=env,
        )
        commit = self._text(workspace, "rev-parse", "HEAD")
        tree = self._text(workspace, "rev-parse", "HEAD^{tree}")
        patch = self._run(
            workspace,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            baseline_commit,
            commit,
            max_output_bytes=self.config.limits.max_diff_bytes,
        ).stdout
        numstat = self._run(
            workspace,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            baseline_commit,
            commit,
        ).stdout.decode("utf-8", errors="replace")
        changed_lines = 0
        for line in numstat.splitlines():
            fields = line.split("\t", 2)
            if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
                changed_lines += int(fields[0]) + int(fields[1])
        patch_relative = f"engineers/{engineer_id}/{generation}/candidate.patch"
        self.audit.write_bytes(patch_relative, patch)
        snapshot = CandidateSnapshot(
            engineer_id=engineer_id,
            generation=generation,
            commit=commit,
            tree=tree,
            parent_commit=required_ancestor,
            changed_files=changed_files,
            changed_lines=changed_lines,
            diff_bytes=len(patch),
            patch_path=patch_relative,
            policy_violations=tuple(sorted(set(violations))),
            valid=not violations,
            error=None,
        )
        self.audit.write_json(f"engineers/{engineer_id}/{generation}/candidate.json", snapshot)
        return snapshot

    def export_bundle(self, commit: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_ref = f"refs/arena-export/{self.audit.run_id}"
        self._run(self.private_repo, "update-ref", temporary_ref, commit)
        try:
            self._run(
                self.private_repo,
                "bundle",
                "create",
                str(destination),
                temporary_ref,
            )
            os.chmod(destination, 0o600)
        finally:
            self._run(self.private_repo, "update-ref", "-d", temporary_ref, check=False)

    def export_patch(self, baseline: str, commit: str, destination: Path) -> None:
        patch = self._run(
            self.private_repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            baseline,
            commit,
            max_output_bytes=self.config.limits.max_diff_bytes,
        ).stdout
        self.audit.write_bytes(destination.relative_to(self.audit.run_dir), patch)

    def tree_for(self, commit: str) -> str:
        return self._text(self.private_repo, "rev-parse", f"{commit}^{{tree}}")
