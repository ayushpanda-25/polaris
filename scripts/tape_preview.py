"""
Isolated preview for the strike tape — real CBOE data, real snapshot store,
port 8051, nowhere near the live terminal.

Two things it deliberately avoids:

  • `src.dashboard --cboe` in its argv. The watchdog finds the live instance
    with `pgrep -f 'src\\.dashboard.*--cboe'`, so a second process wearing that
    shape gets phantom-matched as the real one and killed on the next sweep.
    Running the module by import keeps this process invisible to it.

  • The SQLite WRITER. It reads gex.db (that's the point — the tape needs the
    history the live instance has been logging) but never writes, so there's
    no second thread appending to the same store.

Compute-on-read, like the serverless entry: one CBOE fetch per ticker when a
grid goes stale, no background loop to leave running.

    python3 scripts/tape_preview.py        → http://127.0.0.1:8051
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dashboard import create_app
from src.data_feed import PrometheusBackupFeed
from src.gex_engine import compute_grid
from src.memory_cache import GEXCache
from src.node_classifier import classify_nodes

import config as app_config


class ReadThroughCache(GEXCache):
    """Fetch + compute a ticker's grid on read, cached for `ttl` seconds.

    Kept under the 30s LIVE threshold so the preview's freshness badge reads
    the way the real terminal's does — the Mac recomputes every 15s on a
    background loop, which no preview process should pretend to have."""

    def __init__(self, ttl: int = 20):
        super().__init__()
        self._feed = PrometheusBackupFeed()
        self._ttl = ttl
        self._at: dict[str, float] = {}

    def _ensure(self, ticker: str) -> None:
        if not ticker:
            return
        now = time.time()
        if (now - self._at.get(ticker, 0.0)) < self._ttl and super().get_grid(ticker):
            return
        try:
            snap = self._feed.get_chain_snapshot(ticker)
            grid = compute_grid(ticker=snap.ticker, spot=snap.spot,
                                contracts=snap.contracts, timestamp=int(now))
            self.update(ticker, grid, classify_nodes(grid), source=snap.source)
            self._at[ticker] = now
        except Exception as e:
            print(f"[tape-preview] {ticker} compute failed: {e}", file=sys.stderr)

    def get_grid(self, ticker: str):
        self._ensure(ticker)
        return super().get_grid(ticker)

    def get_nodes(self, ticker: str):
        self._ensure(ticker)
        return super().get_nodes(ticker)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8051"))
    cache = ReadThroughCache()
    cache.get_grid("SPY")                      # prime so the first paint has data
    app = create_app(cache, app_config.TICKERS, gate_auth=False,
                     db_path=app_config.DB_PATH)
    print(f"[tape-preview] http://127.0.0.1:{port}  (store: {app_config.DB_PATH})")
    app.run(debug=False, port=port, host="127.0.0.1")
