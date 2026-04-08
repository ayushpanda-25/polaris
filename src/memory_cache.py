"""
Thread-safe in-memory snapshot store.

The dashboard reads from this cache at high frequency.
The sqlite_writer reads from this cache once every N seconds to persist.
The compute loop writes to this cache every 15s.

Also tracks "Sirius reshuffle" timestamps — the moment a ticker's Sirius
strike+expiry last changed. The dashboard uses this to flag recently
reshuffled positioning so traders know not to trust the new Sirius yet.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .gex_engine import GEXGrid
from .node_classifier import NodeMap


class GEXCache:
    """Dict-backed cache keyed by ticker. Simple lock (not async)."""

    def __init__(self):
        self._grids: dict[str, GEXGrid] = {}
        self._nodes: dict[str, NodeMap] = {}
        # ticker → (last_sirius_strike, last_sirius_expiry, ts_of_last_change)
        self._sirius_history: dict[str, tuple[float, str, float]] = {}
        self._lock = threading.RLock()

    def update(self, ticker: str, grid: GEXGrid, nodes: NodeMap) -> None:
        with self._lock:
            # Reshuffle detection: compare new Sirius to last recorded Sirius
            new_sirius = nodes.sirius
            if new_sirius is not None:
                prev = self._sirius_history.get(ticker)
                changed = (
                    prev is None
                    or prev[0] != new_sirius.strike
                    or prev[1] != new_sirius.expiry
                )
                if changed:
                    self._sirius_history[ticker] = (
                        new_sirius.strike,
                        new_sirius.expiry,
                        time.time(),
                    )
            self._grids[ticker] = grid
            self._nodes[ticker] = nodes

    def get_grid(self, ticker: str) -> Optional[GEXGrid]:
        with self._lock:
            return self._grids.get(ticker)

    def get_nodes(self, ticker: str) -> Optional[NodeMap]:
        with self._lock:
            return self._nodes.get(ticker)

    def sirius_reshuffle_age(self, ticker: str) -> Optional[float]:
        """
        Seconds since this ticker's Sirius strike+expiry last changed.
        Returns None if there's no history yet.
        """
        with self._lock:
            entry = self._sirius_history.get(ticker)
            if entry is None:
                return None
            return time.time() - entry[2]

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
