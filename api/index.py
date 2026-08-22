"""
Vercel serverless entry point for Polaris — the FULL terminal, CBOE-only, public.

This serves the real modern Polaris UI (palettes, ORION five-panel, VIX crash
walls, the /learn Academy) — the same `create_app` the Mac runs — but off the
Mac, always-up, on Vercel. Members open THIS from Meridian.

Two things make the threaded Mac app work as a stateless serverless function:

  1. Data: no background compute loop survives between lambda invocations, so
     `LazyCBOECache` computes a ticker's GEX grid on read (one CBOE CDN fetch +
     Black-Scholes), TTL-cached inside a warm lambda. It replaces the Mac's
     ComputeLoop + SQLiteWriter threads entirely.

  2. Feed: CBOE delayed quotes only — never LSEG. Polaris's single-user LSEG
     license must never serve anyone but Ayush, so the member-facing cloud
     terminal is CBOE (public, keyless, ~15-min delayed). `PrometheusBackupFeed`
     falls back to a vendored stdlib fetch here (no local prometheus repo on
     Vercel), so this needs nothing but the CBOE CDN.

Auth is skipped (`gate_auth=False`): there is nothing license-restricted to
gate. For the live LSEG terminal on the Mac, use `python3 -m src.dashboard`.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Make project modules importable (repo root is one level up from api/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_feed import PrometheusBackupFeed
from src.dashboard import create_app
from src.gex_engine import compute_grid
from src.memory_cache import GEXCache
from src.node_classifier import classify_nodes

import config as app_config


class LazyCBOECache(GEXCache):
    """Compute-on-read GEX cache for serverless — the drop-in replacement for
    the Mac's background compute loop.

    A ticker's grid is (re)computed the first time it's read and whenever the
    cached copy is older than `POLARIS_CLOUD_TTL` seconds; within that window a
    warm lambda's repeated polls are free. All reads flow through `get_grid` /
    `get_nodes`, which the dashboard callbacks already use, so nothing upstream
    changes. Fetch is CBOE-only (`PrometheusBackupFeed`)."""

    def __init__(self, ttl: int | None = None):
        super().__init__()
        self._feed = PrometheusBackupFeed()
        # Keep the served grid under the LIVE freshness threshold (30s) so a
        # healthy cloud terminal reads LIVE, not a false "Lagging". Recompute is
        # cheap; the CDN fetch under it is still capped by PrometheusBackupFeed's
        # own 60s TTL, so CBOE isn't hammered.
        self._ttl = ttl if ttl is not None else int(os.environ.get("POLARIS_CLOUD_TTL", "25"))
        self._computed_at: dict[str, float] = {}

    def _ensure(self, ticker: str) -> None:
        if not ticker:
            return
        now = time.time()
        fresh = (now - self._computed_at.get(ticker, 0.0)) < self._ttl
        if fresh and super().get_grid(ticker) is not None:
            return
        try:
            snap = self._feed.get_chain_snapshot(ticker)
            grid = compute_grid(
                ticker=snap.ticker,
                spot=snap.spot,
                contracts=snap.contracts,
                # Stamp SERVE time, not fetch time. Serverless has no background
                # loop, so the freshness badge means "when a viewer last got a
                # fresh recompute" — always recent. The inherent ~15-min CBOE
                # delay is disclosed by the "CBOE · 15m delayed" pill instead.
                timestamp=int(now),
            )
            nodes = classify_nodes(grid)
            self.update(ticker, grid, nodes, source=snap.source)
            self._computed_at[ticker] = now
        except Exception as e:  # keep serving stale-but-real over erroring out
            print(f"[polaris-cloud] {ticker} compute failed: {e}", file=sys.stderr)

    def get_grid(self, ticker: str):
        self._ensure(ticker)
        return super().get_grid(ticker)

    def get_nodes(self, ticker: str):
        self._ensure(ticker)
        return super().get_nodes(ticker)


# Module-level singletons (created once per lambda warm-start).
_cache = LazyCBOECache()

# Pre-warm only the default ticker so a cold lambda's first paint has data
# without paying for all 11 fetches up front. The rest lazy-load on demand.
try:
    _cache.get_grid("SPY")
except Exception as e:
    print(f"[polaris-cloud cold-start] SPY prime failed: {e}", file=sys.stderr)

# The real modern terminal — public (CBOE only, nothing to gate).
# Poll every 30s on the cloud (env POLARIS_CLOUD_POLL), not the Mac's 15s.
# CBOE regenerates its file ~every 60s and the quotes are 15-min delayed, so
# 30s is a responsiveness-feel choice (catches a new file within ~30s) — the
# real Active-CPU saver is the tab-visibility pause in create_app (a hidden
# tab stops polling entirely), which keeps this inside the free tier.
dash_app = create_app(
    _cache,
    app_config.TICKERS,
    gate_auth=False,
    poll_seconds=int(os.environ.get("POLARIS_CLOUD_POLL", "30")),
)

# Vercel's Python runtime binds to a module variable named `app` and requires
# it to be a WSGI/ASGI callable. A Dash object is NOT WSGI-callable — its Flask
# instance (`.server`) is, and Dash has already mounted every route + callback
# on it, so serving that Flask app serves the whole terminal. Keep `dash_app`
# referenced (module global) so its callbacks stay alive.
app = dash_app.server          # ← the WSGI callable Vercel binds to
server = app                   # legacy alias (older entry expected `server`)


if __name__ == "__main__":
    # Local smoke test: python3 api/index.py  → http://127.0.0.1:8050
    dash_app.run(debug=False, host="127.0.0.1", port=8050)
