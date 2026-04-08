#!/usr/bin/env bash
# Launch the HeatSeeker replica dashboard.
#
# Modes:
#   ./scripts/start_all.sh              → synthetic feed (safe off-hours)
#   ./scripts/start_all.sh --lseg       → live LSEG feed (requires bridge running)
set -euo pipefail

cd "$(dirname "$0")/.."

MODE_FLAG="--synthetic"
if [[ "${1:-}" == "--lseg" ]]; then
    MODE_FLAG="--lseg"
    echo "[start_all] LSEG mode — ensure Workspace + flow-terminal bridge are up"
fi

exec /usr/local/bin/python3 -m src.dashboard $MODE_FLAG
