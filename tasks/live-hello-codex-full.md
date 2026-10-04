+++
# 2-minute smoke test of unattended Codex: full access, internet, sub-agents allowed.
#   mkdir -p /tmp/arena-full && git -C /tmp/arena-full init -q
agent = "codex"
model = "gpt-6-sol"
network = true
full_access = true
subagents = true
max_turns = 10
timeout_seconds = 300

[[checks]]
name = "hello.txt exists"
argv = ["grep", "-qx", "hello from run mode", "hello.txt"]

[[checks]]
name = "internet worked (pypi answered)"
argv = ["grep", "-q", "200", "net.txt"]
+++

1. Create hello.txt containing exactly one line: hello from run mode
2. Run: curl -s -o /dev/null -w "%{http_code}" https://pypi.org/simple/ > net.txt
Do nothing else.
