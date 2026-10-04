"""Deterministic checks for the overnight repo queues. Run with cwd = the target repo.

Exit 0 = PASS, 1 = FAIL, 2 = UNKNOWN. The last stdout line is the reason. Reads only.

  tests-not-reduced REGEX GLOB [GLOB...]   tests matching REGEX now >= at HEAD (no deleting tests)
  backlog FILE MIN_ITEMS MIN_DONE          checklist exists with >= MIN_ITEMS items, >= MIN_DONE ticked
  no-secrets                               no key-looking strings in changed or new files
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"(ANTHROPIC|OPENAI|VISO|AWS)_[A-Z_]*KEY\s*=\s*['\"]?[A-Za-z0-9_\-]{12,})"
)


def done(code: int, reason: str) -> None:
    print(reason)
    raise SystemExit(code)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "--no-optional-locks", *args], capture_output=True, text=True, check=False
    ).stdout


def tests_not_reduced(regex: str, globs: list[str]) -> None:
    pattern = re.compile(regex, re.M)
    before = sum(len(pattern.findall(git("show", f"HEAD:{f}"))) for f in
                 git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
                 if any(Path(f).match(g) for g in globs))
    now = sum(len(pattern.findall(p.read_text(errors="ignore")))
              for g in globs for p in Path(".").glob(g) if p.is_file())
    if now < before:
        done(1, f"tests reduced: {before} at HEAD -> {now} now")
    done(0, f"tests: {before} at HEAD -> {now} now")


def backlog(file: str, min_items: int, min_done: int) -> None:
    path = Path(file)
    if not path.is_file():
        done(1, f"{file} missing")
    text = path.read_text(errors="ignore")
    items = re.findall(r"^\s*[-*] \[( |x|X)\]", text, re.M)
    ticked = sum(1 for i in items if i.lower() == "x")
    if len(items) < min_items:
        done(1, f"{len(items)} backlog items (< {min_items})")
    if ticked < min_done:
        done(1, f"{ticked} items done (< {min_done})")
    done(0, f"{ticked}/{len(items)} backlog items done")


def no_secrets() -> None:
    changed = set(git("diff", "--name-only", "HEAD").splitlines())
    changed |= set(git("ls-files", "--others", "--exclude-standard").splitlines())
    hits = []
    for name in sorted(changed):
        p = Path(name)
        if p.is_file() and p.stat().st_size < 2_000_000 and SECRET.search(p.read_text(errors="ignore")):
            hits.append(name)
    if hits:
        done(1, f"key-looking strings in: {', '.join(hits[:5])}")
    done(0, f"no key-looking strings in {len(changed)} changed/new files")


if __name__ == "__main__":
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if cmd == "tests-not-reduced" and len(args) >= 2:
        tests_not_reduced(args[0], args[1:])
    elif cmd == "backlog" and len(args) == 3:
        backlog(args[0], int(args[1]), int(args[2]))
    elif cmd == "no-secrets":
        no_secrets()
    else:
        done(2, __doc__ or "usage")
