#!/bin/bash
# Double-click in Finder (macOS) or run ./"Agent Arena.command" to open the control panel.
# First run creates a local .venv and installs the harness (standard library only, no deps).
set -e
cd "$(dirname "$0")"
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "agent-arena needs Python 3.11 or newer: https://www.python.org/downloads/"; read -r -p "Press Enter to close."; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up .venv ..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q -e .
fi
for cli in claude codex; do
  command -v "$cli" >/dev/null 2>&1 || echo "note: '$cli' CLI not found on PATH; tasks using it will fail until it's installed and logged in."
done
exec .venv/bin/python -m agent_arena gui
