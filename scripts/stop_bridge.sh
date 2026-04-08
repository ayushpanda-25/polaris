#!/usr/bin/env bash
# Cleanly stop the flow-terminal bridge.
# Uses pkill -f (not kill -9) per MEMORY.md warning.
set -euo pipefail

if ! pgrep -f "local_bridge.py" >/dev/null; then
    echo "No local_bridge.py process running."
    exit 0
fi

echo "Stopping local_bridge.py..."
pkill -f "local_bridge.py" || true
sleep 1

if pgrep -f "local_bridge.py" >/dev/null; then
    echo "⚠️  Still running after SIGTERM — sending SIGKILL"
    pkill -9 -f "local_bridge.py" || true
    sleep 1
fi

if pgrep -f "local_bridge.py" >/dev/null; then
    echo "❌ Could not stop bridge"
    exit 1
else
    echo "✅ Bridge stopped"
fi
