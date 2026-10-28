#!/bin/sh
# Switch the login service to the private Tailscale HTTPS origin and print a pairing code.
# Prerequisites (owner, one time): `tailscale login` on this Mac; Serve enabled for the
# tailnet (link printed by `tailscale serve`); Tailscale app signed in on the phone.
# Side effects: Tailscale Serve proxies https://<mac>.<tailnet>.ts.net -> 127.0.0.1:8791
# inside the tailnet only (never Funnel); the launchd service restarts with that origin;
# running workers keep going and are reconciled. Nothing is exposed to the public internet.
set -eu
cd "$(dirname "$0")/.."
PY=.venv/bin/python
HOME_DIR=.arena-control
PORT=8791

echo "1/5 Tailscale status"
$PY -m agent_arena.control --home $HOME_DIR remote-status
echo "2/5 Configure private HTTPS proxy (Serve, not Funnel)"
$PY -m agent_arena.control --home $HOME_DIR configure-remote --port $PORT
ORIGIN=$($PY -c "import json;print(json.load(open('$HOME_DIR/connection.json'))['origin'])")
echo "3/5 Restart the login service with origin $ORIGIN"
$PY -m agent_arena.control --home $HOME_DIR uninstall-service || true
i=0
while [ $i -lt 90 ]; do
  if ! lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then break; fi
  i=$((i+1)); sleep 1
done
$PY -m agent_arena.control --home $HOME_DIR install-service --port $PORT --origin "$ORIGIN"
echo "4/5 Verify TLS and that unauthenticated access is denied"
i=0
while [ $i -lt 60 ]; do
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$ORIGIN/api/v1/overview" || true)
  if [ "$code" = "401" ]; then break; fi
  i=$((i+1)); sleep 1
done
[ "$code" = "401" ] || { echo "Private HTTPS transport check failed (got $code)"; exit 1; }
echo "    $ORIGIN serves over TLS and denies unpaired devices (401)"
echo "5/5 One-time pairing code (10 minutes)"
CODE=$($PY -m agent_arena.control --home $HOME_DIR pair)
echo
echo "Phone URL : $ORIGIN"
echo "Pair code : $CODE"
echo "On the phone: Tailscale connected (Wi-Fi off to prove cellular), open the URL, enter the code."
