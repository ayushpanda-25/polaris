#!/usr/bin/env bash
# Start the flow-terminal LSEG bridge in the background.
# Checks that LSEG Workspace is up first, then launches local_bridge.py.
set -euo pipefail

BRIDGE_DIR="$HOME/flow-terminal"
LOG="/tmp/polaris_bridge.log"

if [[ ! -d "$BRIDGE_DIR" ]]; then
    echo "❌ $BRIDGE_DIR not found"
    exit 1
fi

# Check if LSEG Workspace is running (it listens on port 9000)
if ! lsof -i :9000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "❌ LSEG Workspace doesn't appear to be running (no listener on :9000)"
    echo "   → Launch LSEG Workspace manually and sign in, then re-run this script"
    exit 2
fi

# Check if bridge is already running
if pgrep -f "local_bridge.py" >/dev/null; then
    echo "⚠️  local_bridge.py already running — not starting a second instance"
    echo "   → To restart: ./scripts/stop_bridge.sh && ./scripts/start_bridge.sh"
    ps aux | grep -v grep | grep local_bridge.py
    exit 0
fi

APP_KEY="${EIKON_APP_KEY:-}"
if [[ -z "$APP_KEY" ]]; then
    echo "⚠️  EIKON_APP_KEY env var not set — bridge may fail to authenticate"
fi

cd "$BRIDGE_DIR"
echo "🚀 Starting local_bridge.py (log: $LOG)"
nohup /usr/local/bin/python3 local_bridge.py ${APP_KEY:+"$APP_KEY"} \
    > "$LOG" 2>&1 &
PID=$!
echo "   PID: $PID"

# Give it a few seconds to either succeed or fail
sleep 3
if kill -0 "$PID" 2>/dev/null; then
    echo "✅ Bridge running (PID $PID). Tail logs with: tail -f $LOG"
else
    echo "❌ Bridge died within 3s. Last log lines:"
    strings "$LOG" 2>/dev/null | tail -20 || tail -20 "$LOG"
    exit 3
fi
