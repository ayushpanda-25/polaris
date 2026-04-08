"""
Thread-safe in-memory snapshot store.

The dashboard reads from this cache at high frequency.
The sqlite_writer reads from this cache once every N seconds to persist.
The compute loop writes to this cache every 15s.
"""
from __future__ import annotations

import threading
from typing import Optional

from .gex_engine import GEXGrid
from .node_classifier import NodeMap


class GEXCache:
    """Dict-backed cache keyed by ticker. Simple lock (not async)."""

    def __init__(self):
        self._grids: dict[str, GEXGrid] = {}
        self._nodes: dict[str, NodeMap] = {}
        self._lock = threading.RLock()

    def update(self, ticker: str, grid: GEXGrid, nodes: NodeMap) -> None:
        with self._lock:
            self._grids[ticker] = grid
            self._nodes[ticker] = nodes

    def get_grid(self, ticker: str) -> Optional[GEXGrid]:
        with self._lock:
            return self._grids.get(ticker)

    def get_nodes(self, ticker: str) -> Optional[NodeMap]:
        with self._lock:
            return self._nodes.get(ticker)

    def snapshot_all(self) -> dict[str, tuple[GEXGrid, NodeMap]]:
        with self._lock:
            return {
                t: (self._grids[t], self._nodes[t])
                for t in self._grids.keys()
                if t in self._nodes
            }

    def tickers(self) -> list[str]:
        with self._lock:
            return list(self._grids.keys())


# Process-wide singleton
_cache: Optional[GEXCache] = None


def get_cache() -> GEXCache:
    global _cache
    if _cache is None:
        _cache = GEXCache()
    return _cache
