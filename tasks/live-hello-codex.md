+++
# 2-minute smoke test of run mode's Codex path (never run live before). Run before any long Codex job:
#   mkdir -p /tmp/arena-hello-codex && git -C /tmp/arena-hello-codex init -q
#   arena run tasks/live-hello-codex.md --repo /tmp/arena-hello-codex
agent = "codex"
model = "gpt-6-sol"
max_turns = 4
timeout_seconds = 300
next_on_pass = "run tasks/physics-v3-build.md"

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
