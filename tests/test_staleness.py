"""Tests for staleness detection."""
import time

from src.staleness import (
    FreshnessState,
    evaluate_freshness,
    latest_cache_timestamp,
    _format_age,
)


def test_no_data_is_offline():
    s = evaluate_freshness(None)
    assert s.state == FreshnessState.OFFLINE
    assert s.age_seconds is None
    assert "No data" in s.message


def test_fresh_data_is_live():
    now = 1_000_000.0
    s = evaluate_freshness(int(now - 5), now=now)
    assert s.state == FreshnessState.LIVE
    assert s.age_seconds == 5
    assert "Live" in s.message


def test_30_to_120s_is_lagging():
    now = 1_000_000.0
    s = evaluate_freshness(int(now - 60), now=now)
    assert s.state == FreshnessState.LAGGING
    assert "Lagging" in s.message


def test_2min_to_10min_is_stale():
    now = 1_000_000.0
    s = evaluate_freshness(int(now - 200), now=now)
    assert s.state == FreshnessState.STALE
    assert "STALE" in s.message
    assert "Do not trust" in s.message


def test_over_10min_is_offline():
    now = 1_000_000.0
    s = evaluate_freshness(int(now - 1000), now=now)
    assert s.state == FreshnessState.OFFLINE
    assert "OFFLINE" in s.message


def test_clock_skew_treated_as_live():
    now = 1_000_000.0
    s = evaluate_freshness(int(now + 5), now=now)
    assert s.state == FreshnessState.LIVE


def test_color_codes():
    now = 1_000_000.0
    assert evaluate_freshness(int(now - 5), now=now).color == "#3edc81"  # green
    assert evaluate_freshness(int(now - 60), now=now).color == "#f9d649"  # yellow
    assert evaluate_freshness(int(now - 200), now=now).color == "#ff5959"  # red
    assert evaluate_freshness(None).color == "#7a7a7a"  # grey


def test_format_age_human_readable():
    assert _format_age(5) == "5s ago"
    assert _format_age(47) == "47s ago"
    assert _format_age(125) == "2m 5s ago"
    assert _format_age(600) == "10m ago"
    assert _format_age(4500) == "1h 15m ago"


def test_latest_cache_timestamp_with_data():
    from src.gex_engine import GEXCell, GEXGrid
    from src.memory_cache import GEXCache
    from src.node_classifier import classify_nodes

    cache = GEXCache()
    g1 = GEXGrid(ticker="SPY", spot=500, timestamp=1000, cells=[
        GEXCell(500, "2026-04-08", gex_value=10, vex_value=0)
    ])
    g2 = GEXGrid(ticker="QQQ", spot=400, timestamp=2000, cells=[
        GEXCell(400, "2026-04-08", gex_value=20, vex_value=0)
    ])
    cache.update("SPY", g1, classify_nodes(g1))
    cache.update("QQQ", g2, classify_nodes(g2))
    assert latest_cache_timestamp(cache) == 2000


def test_latest_cache_timestamp_empty():
    from src.memory_cache import GEXCache
    assert latest_cache_timestamp(GEXCache()) is None
