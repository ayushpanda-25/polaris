"""
Background compute loop — pulls from the feed, computes GEX grid
and node classification, writes into the memory cache.

Runs as a thread started alongside the dashboard.
"""
from __future__ import annotations

import threading
import time
from typing import Iterable

from .data_feed import OptionsFeed
from .gex_engine import compute_grid
from .memory_cache import GEXCache
from .node_classifier import classify_nodes


class ComputeLoop(threading.Thread):
    def __init__(
        self,
        feed: OptionsFeed,
        cache: GEXCache,
        tickers: Iterable[str],
        interval: int = 15,
    ):
        super().__init__(daemon=True)
        self.feed = feed
        self.cache = cache
        self.tickers = list(tickers)
        self.interval = interval
        self._stop = threading.Event()

    def run(self):
        # Prime the cache immediately so the dashboard has data on first paint
        self._tick()
        while not self._stop.wait(self.interval):
            self._tick()

    def _tick(self):
        for ticker in self.tickers:
            try:
                snap = self.feed.get_chain_snapshot(ticker)
                grid = compute_grid(
                    ticker=snap.ticker,
                    spot=snap.spot,
                    contracts=snap.contracts,
                    timestamp=snap.timestamp,
                )
                nodes = classify_nodes(grid)
                self.cache.update(ticker, grid, nodes)
            except Exception as e:
                print(f"[compute_loop] {ticker} failed: {e}")

    def stop(self):
        self._stop.set()
