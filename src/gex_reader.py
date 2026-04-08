"""
Thin read-only API for other tools (AlphaForge, analytics, scripts)
that want to consume the SQLite store populated by sqlite_writer.

Usage:
    from skylit_replica.gex_reader import get_latest_king_node
    print(get_latest_king_node("SPY"))
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from ..config import DB_PATH as _DEFAULT_DB
except (ImportError, ValueError):
    _DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "gex.db"


@dataclass
class KingNodeRow:
    ts: int
    ticker: str
    strike: float
    expiry: str
    gex_value: float


def get_latest_king_node(ticker: str, db_path: Path = _DEFAULT_DB) -> Optional[KingNodeRow]:
    if not Path(db_path).exists():
        return None
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT ts, ticker, strike, expiry, gex_value "
            "FROM king_nodes WHERE ticker = ? ORDER BY ts DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return KingNodeRow(*row)


def get_latest_grid(ticker: str, db_path: Path = _DEFAULT_DB) -> list[tuple]:
    """Returns list of (strike, expiry, gex_value, vex_value) for most recent ts."""
    if not Path(db_path).exists():
        return []
    with sqlite3.connect(db_path) as conn:
        ts_row = conn.execute(
            "SELECT MAX(ts) FROM gex_snapshots WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not ts_row or ts_row[0] is None:
            return []
        ts = ts_row[0]
        return conn.execute(
            "SELECT strike, expiry, gex_value, vex_value FROM gex_snapshots "
            "WHERE ticker = ? AND ts = ? ORDER BY strike, expiry",
            (ticker, ts),
        ).fetchall()
