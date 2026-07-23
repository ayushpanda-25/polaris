"""Tests for the sign-confidence layer (flow_confidence.py)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.gex_engine import GEXCell, GEXGrid
from src.flow_confidence import assess_confidence, sirius_confidence


TODAY = date(2026, 7, 21)
DTE0 = TODAY.isoformat()                       # 0DTE
DTE1 = (TODAY + timedelta(days=1)).isoformat()  # 1DTE
DTE10 = (TODAY + timedelta(days=10)).isoformat()  # longer-dated


def _cell(strike, expiry, call_oi=0.0, put_oi=0.0, call_vol=0.0, put_vol=0.0):
    return GEXCell(
        strike=strike, expiry=expiry, gex_value=0.0, vex_value=0.0,
        call_oi=call_oi, put_oi=put_oi, call_volume=call_vol, put_volume=put_vol,
    )


def _grid(cells, spot=745.0):
    return GEXGrid(ticker="SPY", spot=spot, timestamp=0, cells=cells)


def test_away_from_spot_is_high_even_if_churned():
    # 720 is ~3.4% below a 745 spot — textbook sign is fine there.
    cell = _cell(720.0, DTE0, call_oi=1000, put_oi=9000, call_vol=5000, put_vol=5000)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    assert cmap.is_low(720.0, DTE0) is False


def test_near_spot_high_churn_is_low():
    # 745 == spot, 0DTE, volume 2x OI → churned → low confidence.
    cell = _cell(745.0, DTE0, call_oi=1000, put_oi=200, call_vol=1800, put_vol=600)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    c = cmap.get(745.0, DTE0)
    assert c.is_low
    assert any("churn" in r for r in c.reasons)


def test_near_spot_balanced_oi_is_low_coinflip():
    # Near spot, 0DTE, low churn, but call/put OI ~50/50 → sign is a coin flip.
    cell = _cell(744.0, DTE0, call_oi=1000, put_oi=1050, call_vol=50, put_vol=50)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    c = cmap.get(744.0, DTE0)
    assert c.is_low
    assert any("coin flip" in r for r in c.reasons)


def test_near_spot_low_churn_imbalanced_is_high():
    # Near spot, 0DTE, low churn, puts clearly dominate → sign has conviction.
    cell = _cell(743.0, DTE0, call_oi=200, put_oi=5000, call_vol=100, put_vol=200)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    assert cmap.is_low(743.0, DTE0) is False


def test_near_spot_but_longdated_is_high():
    # Same churn but 10DTE — the textbook sign is not the 0DTE failure mode.
    cell = _cell(745.0, DTE10, call_oi=1000, put_oi=200, call_vol=1800, put_vol=600)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    assert cmap.is_low(745.0, DTE10) is False


def test_unknown_oi_does_not_flag():
    # No OI data → churn 0, no balance check → don't invent a caveat.
    cell = _cell(745.0, DTE0, call_oi=0, put_oi=0, call_vol=0, put_vol=0)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    assert cmap.is_low(745.0, DTE0) is False


def test_1dte_still_covered():
    cell = _cell(745.0, DTE1, call_oi=1000, put_oi=200, call_vol=2000, put_vol=500)
    cmap = assess_confidence(_grid([cell]), today=TODAY)
    assert cmap.is_low(745.0, DTE1)


def test_sirius_confidence_helper():
    from src.node_classifier import classify_nodes
    # Make the churned ATM cell the dominant |GEX| node.
    king = _cell(745.0, DTE0, call_oi=1000, put_oi=200, call_vol=2000, put_vol=500)
    king.gex_value = 50_000.0
    quiet = _cell(720.0, DTE10, call_oi=500, put_oi=500)
    quiet.gex_value = 100.0
    grid = _grid([king, quiet])
    nodes = classify_nodes(grid)
    conf = sirius_confidence(grid, nodes, today=TODAY)
    assert conf is not None
    assert conf.is_low


def test_empty_grid_is_safe():
    cmap = assess_confidence(_grid([]), today=TODAY)
    assert cmap.low_keys == set()
