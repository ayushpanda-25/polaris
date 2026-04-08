"""Tests for Sirius significance flag and cache reshuffle tracking."""
import time

import pytest

from src.gex_engine import GEXCell, GEXGrid
from src.memory_cache import GEXCache
from src.node_classifier import classify_nodes


# ─────────────────────────────────────────────────────────────────────
# Significance flag
# ─────────────────────────────────────────────────────────────────────

def _grid(cells, ticker="SPY", spot=500.0, ts=1_000_000):
    return GEXGrid(ticker=ticker, spot=spot, timestamp=ts, cells=cells)


def test_clear_winner_is_significant():
    """Top cell is 5× the runners-up median → significant."""
    cells = [
        GEXCell(500, "2026-04-08", gex_value=-200, vex_value=0),  # winner
        GEXCell(495, "2026-04-08", gex_value=-30, vex_value=0),
        GEXCell(505, "2026-04-08", gex_value=-25, vex_value=0),
        GEXCell(498, "2026-04-08", gex_value=-20, vex_value=0),
        GEXCell(502, "2026-04-08", gex_value=-15, vex_value=0),
    ]
    nm = classify_nodes(_grid(cells))
    assert nm.sirius is not None
    assert nm.sirius.strike == 500
    assert nm.sirius.significant is True


def test_no_clear_leader_flagged():
    """Top cell is barely larger than runners → not significant."""
    cells = [
        GEXCell(500, "2026-04-08", gex_value=-110, vex_value=0),
        GEXCell(495, "2026-04-08", gex_value=-100, vex_value=0),
        GEXCell(505, "2026-04-08", gex_value=-95, vex_value=0),
        GEXCell(498, "2026-04-08", gex_value=-90, vex_value=0),
        GEXCell(502, "2026-04-08", gex_value=-85, vex_value=0),
    ]
    nm = classify_nodes(_grid(cells))
    assert nm.sirius is not None
    # Top is 110, runners median is ~95 → ratio 1.16, below 1.5 threshold
    assert nm.sirius.significant is False


def test_single_cell_is_trivially_significant():
    """One cell, nothing to compare → significant by default."""
    cells = [GEXCell(500, "2026-04-08", gex_value=-50, vex_value=0)]
    nm = classify_nodes(_grid(cells))
    assert nm.sirius.significant is True


def test_significance_uses_absolute_value():
    """Mixed signs: should use |GEX| not signed value."""
    cells = [
        GEXCell(500, "2026-04-08", gex_value=-300, vex_value=0),  # |300|
        GEXCell(505, "2026-04-08", gex_value=+50, vex_value=0),
        GEXCell(495, "2026-04-08", gex_value=-40, vex_value=0),
        GEXCell(510, "2026-04-08", gex_value=+30, vex_value=0),
        GEXCell(490, "2026-04-08", gex_value=-25, vex_value=0),
    ]
    nm = classify_nodes(_grid(cells))
    assert nm.sirius.strike == 500
    assert nm.sirius.significant is True  # 300 vs median(50,40,30,25)=35, ratio=8.6×


# ─────────────────────────────────────────────────────────────────────
# Reshuffle tracking
# ─────────────────────────────────────────────────────────────────────

def _populate(cache, ticker, sirius_strike, sirius_expiry="2026-04-08"):
    cells = [
        GEXCell(sirius_strike, sirius_expiry, gex_value=-500, vex_value=0),
        GEXCell(sirius_strike + 5, sirius_expiry, gex_value=-50, vex_value=0),
        GEXCell(sirius_strike - 5, sirius_expiry, gex_value=-40, vex_value=0),
    ]
    grid = _grid(cells, ticker=ticker, ts=int(time.time()))
    nodes = classify_nodes(grid)
    cache.update(ticker, grid, nodes)


def test_reshuffle_age_none_before_first_update():
    cache = GEXCache()
    assert cache.sirius_reshuffle_age("SPY") is None


def test_reshuffle_age_resets_on_first_update():
    cache = GEXCache()
    _populate(cache, "SPY", sirius_strike=580)
    age = cache.sirius_reshuffle_age("SPY")
    assert age is not None
    assert age < 1.0  # just happened


def test_reshuffle_age_grows_when_sirius_unchanged():
    cache = GEXCache()
    _populate(cache, "SPY", sirius_strike=580)
    time.sleep(0.05)
    _populate(cache, "SPY", sirius_strike=580)  # same Sirius
    age = cache.sirius_reshuffle_age("SPY")
    assert age >= 0.04  # didn't reset


def test_reshuffle_age_resets_when_sirius_changes():
    cache = GEXCache()
    _populate(cache, "SPY", sirius_strike=580)
    time.sleep(0.1)
    # Sirius strike now changes
    _populate(cache, "SPY", sirius_strike=585)
    age = cache.sirius_reshuffle_age("SPY")
    assert age is not None
    assert age < 0.05  # just reshuffled


def test_reshuffle_age_resets_on_expiry_change():
    cache = GEXCache()
    _populate(cache, "SPY", sirius_strike=580, sirius_expiry="2026-04-08")
    time.sleep(0.1)
    _populate(cache, "SPY", sirius_strike=580, sirius_expiry="2026-04-09")  # diff expiry
    age = cache.sirius_reshuffle_age("SPY")
    assert age < 0.05  # treated as reshuffle


def test_reshuffle_tracking_isolated_per_ticker():
    cache = GEXCache()
    _populate(cache, "SPY", sirius_strike=580)
    time.sleep(0.05)
    _populate(cache, "QQQ", sirius_strike=510)
    spy_age = cache.sirius_reshuffle_age("SPY")
    qqq_age = cache.sirius_reshuffle_age("QQQ")
    assert spy_age >= 0.04   # has aged
    assert qqq_age < 0.04    # just set
