+++
# Small real coding task for trying the harness (and comparing Claude vs Codex, or harness vs
# prompting). Set up the practice repo with the snippet in docs/RUN_MODE.md or from the chat.
agent = "claude"
model = "claude-sonnet-5"
allowed_tools = ["Read", "Glob", "Grep", "Edit", "Bash(python3 -m unittest:*)"]
repo = "~/agent-reliability-workspace/arena-test"
max_turns = 15
timeout_seconds = 600

[[checks]]
name = "all tests pass"
argv = ["python3", "-m", "unittest", "-q"]

[[checks]]
name = "tests were not edited (no cheating)"
argv = ["git", "--no-optional-locks", "diff", "--quiet", "HEAD", "--", "test_stats.py"]
+++

The tests in test_stats.py fail. Fix the bugs in stats.py so all tests pass.
Do not edit test_stats.py. Run the tests with: python3 -m unittest
