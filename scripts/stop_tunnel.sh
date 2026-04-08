#!/usr/bin/env bash
# Cleanly stop the Cloudflare tunnel.
set -euo pipefail

if ! pgrep -f "cloudflared.*8050" >/dev/null; then
    echo "No tunnel running."
    exit 0
fi

echo "Stopping cloudflared tunnel..."
pkill -f "cloudflared.*8050" || true
sleep 1

if pgrep -f "cloudflared.*8050" >/dev/null; then
    echo "⚠️  Still running — sending SIGKILL"
    pkill -9 -f "cloudflared.*8050" || true
fi

rm -f /tmp/polaris_tunnel_url.txt
echo "✅ Tunnel stopped"
