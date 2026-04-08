"""Tests for memory cache, SQLite writer, and end-to-end pipeline."""
import sqlite3
import time
from pathlib import Path

from src.compute_loop import ComputeLoop
from src.data_feed import SyntheticOptionsFeed
from src.gex_engine import GEXCell, GEXGrid
from src.gex_reader import get_latest_king_node
from src.memory_cache import GEXCache
from src.node_classifier import NodeMap, classify_nodes
from src.sqlite_writer import flush_cache, init_db


def _mk_grid(ticker="SPY", spot=500.0) -> tuple[GEXGrid, NodeMap]:
    cells = [
        GEXCell(495.0, "2026-04-18", gex_value=-100.0, vex_value=-5),
        GEXCell(500.0, "2026-04-18", gex_value=-200.0, vex_value=-10),
        GEXCell(505.0, "2026-04-18", gex_value=50.0, vex_value=2),
    ]
    grid = GEXGrid(ticker=ticker, spot=spot, timestamp=int(time.time()), cells=cells)
    nodes = classify_nodes(grid)
    return grid, nodes


def test_cache_get_set():
    cache = GEXCache()
    grid, nodes = _mk_grid()
    cache.update("SPY", grid, nodes)
    assert cache.get_grid("SPY") is grid
    assert cache.get_nodes("SPY").king.strike == 500.0


def test_sqlite_writer_roundtrip(tmp_path):
    db = tmp_path / "test_gex.db"
    init_db(db)
    cache = GEXCache()
    grid, nodes = _mk_grid()
    cache.update("SPY", grid, nodes)

    rows = flush_cache(cache, db)
    assert rows == 3  # three cells

    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM gex_snapshots").fetchone()[0]
        assert count == 3
        king = conn.execute("SELECT ticker, strike FROM king_nodes").fetchone()
        assert king == ("SPY", 500.0)


def test_gex_reader_latest_king_node(tmp_path):
    db = tmp_path / "test_gex.db"
    init_db(db)
    cache = GEXCache()
    grid, nodes = _mk_grid()
    cache.update("SPY", grid, nodes)
    flush_cache(cache, db)

    row = get_latest_king_node("SPY", db)
    assert row is not None
    assert row.strike == 500.0
    assert row.ticker == "SPY"


def test_synthetic_feed_produces_chain():
    feed = SyntheticOptionsFeed()
    snap = feed.get_chain_snapshot("SPY")
    assert snap.ticker == "SPY"
    assert snap.spot > 0
    assert len(snap.contracts) > 100  # should be plenty of strikes × expiries


def test_compute_loop_populates_cache():
    feed = SyntheticOptionsFeed()
    cache = GEXCache()
    loop = ComputeLoop(feed, cache, tickers=["SPY", "QQQ"], interval=60)
    loop._tick()  # one synchronous tick
    grid = cache.get_grid("SPY")
    assert grid is not None
    assert len(grid.cells) > 0
    king = cache.get_nodes("SPY").king
    assert king is not None
