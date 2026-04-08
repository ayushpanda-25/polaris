#!/usr/bin/env bash
# Start a Cloudflare quick tunnel exposing the local Polaris dashboard.
#
# Workflow:
#   1. Auto-download cloudflared if not installed (single static binary)
#   2. Wait until port 8050 is listening (the dashboard)
#   3. Spawn cloudflared tunnel --url http://localhost:8050
#   4. Print the public URL (https://random-name.trycloudflare.com)
#
# The URL stays stable as long as this cloudflared process keeps running.
# When your Mac sleeps, cloudflared pauses; when you wake, it auto-reconnects
# to Cloudflare's edge and the SAME URL keeps working. The browser viewer's
# dcc.Interval polling resumes automatically once the tunnel is back.
#
# Stop with: ./scripts/stop_tunnel.sh   (or just Ctrl-C this script)

set -euo pipefail

CF_BIN="/tmp/cloudflared"
DASH_PORT=8050
TUNNEL_LOG="/tmp/polaris_tunnel.log"
URL_FILE="/tmp/polaris_tunnel_url.txt"

# ── 1. Auto-download cloudflared if missing ───────────────────────────
if [[ ! -x "$CF_BIN" ]]; then
    echo "📥 Downloading cloudflared (first time only)..."
    ARCH=$(uname -m)
    if [[ "$ARCH" == "arm64" ]]; then
        URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz"
    else
        URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz"
    fi
    curl -sSL -o /tmp/cloudflared.tgz "$URL"
    tar -xzf /tmp/cloudflared.tgz -C /tmp
    chmod +x "$CF_BIN"
    rm /tmp/cloudflared.tgz
    echo "   ✅ cloudflared installed at $CF_BIN"
fi

# ── 2. Wait for the dashboard to be listening ─────────────────────────
echo "⏳ Waiting for dashboard on port $DASH_PORT..."
WAITED=0
while ! lsof -i ":$DASH_PORT" -sTCP:LISTEN >/dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [[ $WAITED -ge 30 ]]; then
        echo "❌ Dashboard not listening on :$DASH_PORT after 30s"
        echo "   → Start it with: python3 -m src.dashboard --lseg"
        exit 1
    fi
done
echo "   ✅ Dashboard responding on :$DASH_PORT"

# ── 3. Kill any previous tunnel process ───────────────────────────────
if pgrep -f "cloudflared.*$DASH_PORT" >/dev/null; then
    echo "⚠️  Existing tunnel found, stopping it first..."
    pkill -f "cloudflared.*$DASH_PORT" || true
    sleep 1
fi

# ── 4. Start cloudflared and capture the URL ──────────────────────────
echo "🚀 Starting Cloudflare tunnel..."
"$CF_BIN" tunnel --url "http://localhost:$DASH_PORT" > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# Wait for the URL to appear in the log
URL=""
for _ in {1..30}; do
    sleep 1
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
    if [[ -n "$URL" ]]; then
        break
    fi
done

if [[ -z "$URL" ]]; then
    echo "❌ Could not find tunnel URL in $TUNNEL_LOG within 30s"
    echo "   Last log lines:"
    tail -20 "$TUNNEL_LOG"
    kill "$TUNNEL_PID" 2>/dev/null || true
    exit 2
fi

echo "$URL" > "$URL_FILE"

cat <<EOF

═══════════════════════════════════════════════════════════════
  🌐  Polaris is live at:

      $URL

  • Tunnel PID: $TUNNEL_PID
  • Logs:       $TUNNEL_LOG
  • URL file:   $URL_FILE
  • Stop:       ./scripts/stop_tunnel.sh

  When your Mac sleeps, cloudflared pauses and the page goes
  STALE (red banner). When you wake, the tunnel auto-reconnects
  and the dashboard resumes — the URL stays the same.
═══════════════════════════════════════════════════════════════

EOF

# Keep this script alive so Ctrl-C cleanly stops the tunnel
trap "echo; echo 'Stopping tunnel...'; kill $TUNNEL_PID 2>/dev/null; exit 0" INT TERM
wait $TUNNEL_PID
