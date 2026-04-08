"""
Async SQLite writer — Option C cold path.

Runs as a background thread. Every DB_FLUSH_INTERVAL seconds, snapshots
the memory cache and persists all grids + king nodes to SQLite.

The dashboard never touches this code path. Other tools (AlphaForge,
analytics scripts) read directly from gex.db.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .memory_cache import GEXCache


SCHEMA = """
CREATE TABLE IF NOT EXISTS gex_snapshots (
    ts           INTEGER NOT NULL,
    ticker       TEXT NOT NULL,
    strike       REAL NOT NULL,
    expiry       TEXT NOT NULL,
    gex_value    REAL NOT NULL,
    vex_value    REAL NOT NULL,
    spot_price   REAL NOT NULL,
    PRIMARY KEY (ts, ticker, strike, expiry)
);

CREATE INDEX IF NOT EXISTS idx_ticker_ts ON gex_snapshots(ticker, ts);

CREATE TABLE IF NOT EXISTS king_nodes (
    ts         INTEGER NOT NULL,
    ticker     TEXT NOT NULL,
    strike     REAL NOT NULL,
    expiry     TEXT NOT NULL,
    gex_value  REAL NOT NULL,
    PRIMARY KEY (ts, ticker)
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def flush_cache(cache: GEXCache, db_path: Path) -> int:
    """Write everything in the cache to the DB. Returns row count written."""
    snap = cache.snapshot_all()
    if not snap:
        return 0
    rows_gex = []
    rows_king = []
    for ticker, (grid, nodes) in snap.items():
        for cell in grid.cells:
            rows_gex.append(
                (
                    grid.timestamp,
                    ticker,
                    cell.strike,
                    cell.expiry,
                    cell.gex_value,
                    cell.vex_value,
                    grid.spot,
                )
            )
        if nodes.king is not None:
            rows_king.append(
                (
                    grid.timestamp,
                    ticker,
                    nodes.king.strike,
                    nodes.king.expiry,
                    nodes.king.value,
                )
            )

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO gex_snapshots VALUES (?,?,?,?,?,?,?)",
            rows_gex,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO king_nodes VALUES (?,?,?,?,?)",
            rows_king,
        )
        conn.commit()
    return len(rows_gex)


class SQLiteWriter(threading.Thread):
    """Background thread that periodically flushes the cache."""

    def __init__(self, cache: GEXCache, db_path: Path, interval: int = 60):
        super().__init__(daemon=True)
        self.cache = cache
        self.db_path = db_path
        self.interval = interval
        self._stop = threading.Event()
        init_db(db_path)

    def run(self):
        while not self._stop.is_set():
            try:
                flush_cache(self.cache, self.db_path)
            except Exception as e:
                print(f"[sqlite_writer] flush failed: {e}")
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
