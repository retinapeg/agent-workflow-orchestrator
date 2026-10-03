+++
# Cheapest possible live run: proves real CLI flags, stream-json parsing and the report.
#   mkdir -p /tmp/arena-hello && git -C /tmp/arena-hello init -q
#   python -m agent_arena run tasks/live-hello.md --repo /tmp/arena-hello
agent = "claude"
model = "claude-sonnet-5"
allowed_tools = ["Read", "Write"]
max_turns = 4
timeout_seconds = 180
next_on_pass = "run tasks/codex-smoke.md"

[[checks]]
name = "hello.txt exists"
argv = ["test", "-f", "hello.txt"]

[[checks]]
name = "hello.txt says hello from run mode"
argv = ["grep", "-qx", "hello from run mode", "hello.txt"]
+++

Create a file named `hello.txt` in the current directory containing exactly one line:

hello from run mode

Do nothing else.
