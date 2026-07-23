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
from datetime import date
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
# Raw OI/volume only changes meaningfully on a ~15-min cadence (OI is fixed
# intraday; volume creeps up), so we don't log it every 60s flush.
OI_LOG_INTERVAL_SEC = int(os.environ.get("POLARIS_OI_LOG_INTERVAL", str(15 * 60)))
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

-- One row per (day, ticker, strike, expiry): raw call/put OI + volume.
-- Overwritten through the day (INSERT OR REPLACE), so by EOD each row holds
-- the day's final OI (constant intraday) and full-day volume. This is the
-- un-backfillable history a day-over-day ΔOI dealer-positioning model needs
-- later — start logging now, decide the model later. Tiny (~1 row/strike/day),
-- so it's exempt from retention pruning.
CREATE TABLE IF NOT EXISTS oi_daily (
    date        TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    strike      REAL NOT NULL,
    expiry      TEXT NOT NULL,
    call_oi     REAL NOT NULL,
    put_oi      REAL NOT NULL,
    call_volume REAL NOT NULL,
    put_volume  REAL NOT NULL,
    spot_price  REAL NOT NULL,
    ts          INTEGER NOT NULL,
    PRIMARY KEY (date, ticker, strike, expiry)
);

CREATE INDEX IF NOT EXISTS idx_oi_daily_ticker ON oi_daily(ticker, date);
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


def flush_oi_daily(cache: GEXCache, db_path: Path, day: Optional[str] = None) -> int:
    """Upsert today's raw call/put OI + volume per (strike, expiry).

    Keyed on (date, ticker, strike, expiry) with INSERT OR REPLACE, so calling
    it repeatedly through the day just refreshes today's row — by the close it
    holds the final OI and full-day volume. Returns rows written.
    """
    snap = cache.snapshot_all()
    if not snap:
        return 0
    if day is None:
        day = date.today().isoformat()
    rows = []
    for ticker, (grid, _nodes) in snap.items():
        for cell in grid.cells:
            # Skip cells with no OI and no volume — nothing worth logging.
            if cell.total_oi <= 0 and cell.total_volume <= 0:
                continue
            rows.append(
                (
                    day,
                    ticker,
                    cell.strike,
                    cell.expiry,
                    cell.call_oi,
                    cell.put_oi,
                    cell.call_volume,
                    cell.put_volume,
                    grid.spot,
                    grid.timestamp,
                )
            )
    if not rows:
        return 0
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.executemany(
            "INSERT OR REPLACE INTO oi_daily VALUES (?,?,?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


class SQLiteWriter(threading.Thread):
    """Background thread that periodically flushes the cache."""

    def __init__(
        self,
        cache: GEXCache,
        db_path: Path,
        interval: int = 60,
        retention_days: int = RETENTION_DAYS,
        prune_interval: int = PRUNE_INTERVAL_SEC,
        oi_interval: int = OI_LOG_INTERVAL_SEC,
    ):
        super().__init__(daemon=True)
        self.cache = cache
        self.db_path = db_path
        self.interval = interval
        self.retention_days = retention_days
        self.prune_interval = prune_interval
        self.oi_interval = oi_interval
        self._last_prune = 0.0   # 0 ⇒ prune on the first loop iteration
        self._last_oi = 0.0      # 0 ⇒ log OI on the first loop iteration
        self._stop = threading.Event()
        init_db(db_path)

    def run(self):
        while not self._stop.is_set():
            try:
                flush_cache(self.cache, self.db_path)
            except Exception as e:
                print(f"[sqlite_writer] flush failed: {e}")
            self._maybe_log_oi()
            self._maybe_prune()
            self._stop.wait(self.interval)

    def _maybe_log_oi(self):
        now = time.time()
        if now - self._last_oi < self.oi_interval:
            return
        try:
            n = flush_oi_daily(self.cache, self.db_path)
            # Only consume the interval window on a REAL write. An empty flush
            # (cache not primed yet on cold start, or feed down) must not push
            # the next attempt out a full oi_interval — retry next cycle instead.
            if n:
                self._last_oi = now
                print(f"[sqlite_writer] logged {n} oi_daily rows", flush=True)
        except Exception as e:
            print(f"[sqlite_writer] oi_daily log failed: {e}", flush=True)

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
