"""
Async SQLite writer — Option C cold path.

Runs as a background thread. Every DB_FLUSH_INTERVAL seconds, snapshots
the memory cache and persists all grids + Sirius nodes to SQLite.

The dashboard never touches this code path. Other tools / analytics
scripts read directly from gex.db.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .memory_cache import GEXCache


# Retention: the writer appends a full grid every DB_FLUSH_INTERVAL, so
# gex.db grows ~1.1M rows/day. Nothing in-app reads old rows (gex_reader only
# queries MAX(ts)), so pruning is safe when you want it.
#
# Default is 0 = KEEP EVERYTHING, per Ayush's "keep everything" call: the full
# history is preserved locally AND mirrored to iCloud (see
# scripts/icloud_archive.py). The prune mechanism stays wired up — set
# POLARIS_DB_RETENTION_DAYS=<n> if local disk ever gets tight (89 GB free as
# of 2026-07-09, so not a concern). NOTE: if you ever enable local pruning,
# the iCloud snapshots would only hold as much history as the local DB, so
# keep-everything and iCloud-full-mirror go together.
RETENTION_DAYS = int(os.environ.get("POLARIS_DB_RETENTION_DAYS", "0"))
# Pruning is a cheap DELETE but we don't need it every flush.
PRUNE_INTERVAL_SEC = int(os.environ.get("POLARIS_DB_PRUNE_INTERVAL", str(6 * 3600)))
# Give writes room to wait out a concurrent reader (e.g. the watchdog's
# freshness probe) instead of erroring with "database is locked".
_BUSY_TIMEOUT_MS = 10_000


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

CREATE TABLE IF NOT EXISTS sirius_nodes (
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
    # `with sqlite3.connect()` commits but does NOT close the fd — close
    # explicitly (same lesson as polaris_watchdog.data_age_seconds()).
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def prune_old(db_path: Path, retention_days: int = RETENTION_DAYS) -> int:
    """Delete snapshots older than retention_days from both tables.

    Returns the number of gex_snapshots rows deleted. This bounds the
    otherwise-unbounded growth of gex.db. Safe because no in-app read path
    touches old rows (gex_reader only ever queries MAX(ts)). A retention of
    0 disables pruning entirely.
    """
    if retention_days <= 0:
        return 0
    cutoff = int(time.time()) - retention_days * 86400
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        deleted = conn.execute(
            "DELETE FROM gex_snapshots WHERE ts < ?", (cutoff,)
        ).rowcount or 0
        conn.execute("DELETE FROM sirius_nodes WHERE ts < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()
    return deleted


def flush_cache(cache: GEXCache, db_path: Path) -> int:
    """Write everything in the cache to the DB. Returns row count written."""
    snap = cache.snapshot_all()
    if not snap:
        return 0
    rows_gex = []
    rows_sirius = []
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
        if nodes.sirius is not None:
            rows_sirius.append(
                (
                    grid.timestamp,
                    ticker,
                    nodes.sirius.strike,
                    nodes.sirius.expiry,
                    nodes.sirius.value,
                )
            )

    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.executemany(
            "INSERT OR REPLACE INTO gex_snapshots VALUES (?,?,?,?,?,?,?)",
            rows_gex,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO sirius_nodes VALUES (?,?,?,?,?)",
            rows_sirius,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows_gex)


class SQLiteWriter(threading.Thread):
    """Background thread that periodically flushes the cache."""

    def __init__(
        self,
        cache: GEXCache,
        db_path: Path,
        interval: int = 60,
        retention_days: int = RETENTION_DAYS,
        prune_interval: int = PRUNE_INTERVAL_SEC,
    ):
        super().__init__(daemon=True)
        self.cache = cache
        self.db_path = db_path
        self.interval = interval
        self.retention_days = retention_days
        self.prune_interval = prune_interval
        self._last_prune = 0.0   # 0 ⇒ prune on the first loop iteration
        self._stop = threading.Event()
        init_db(db_path)

    def run(self):
        while not self._stop.is_set():
            try:
                flush_cache(self.cache, self.db_path)
            except Exception as e:
                print(f"[sqlite_writer] flush failed: {e}")
            self._maybe_prune()
            self._stop.wait(self.interval)

    def _maybe_prune(self):
        now = time.time()
        if now - self._last_prune < self.prune_interval:
            return
        try:
            n = prune_old(self.db_path, self.retention_days)
            self._last_prune = now
            if n:
                print(
                    f"[sqlite_writer] pruned {n} rows older than "
                    f"{self.retention_days}d",
                    flush=True,
                )
        except Exception as e:
            print(f"[sqlite_writer] prune failed: {e}", flush=True)

    def stop(self):
        self._stop.set()
