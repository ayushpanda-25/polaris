"""Unit tests for GEX/VEX math."""
import math

import pytest

from src.gex_engine import (
    CONTRACT_SIZE,
    GEXGrid,
    OptionContract,
    compute_grid,
    grid_to_dataframe,
)


def test_single_atm_call_gex():
    """Hand-computed: SPY $500, gamma 0.02, OI 1000, dealer short call (+1).
    Formula: gamma * OI * 100 * spot * sign ($ per $1 move)."""
    c = OptionContract(
        strike=500.0,
        expiry="2026-04-18",
        option_type="C",
        gamma=0.02,
        vanna=0.0,
        open_interest=1000,
        dealer_sign=+1,
    )
    spot = 500.0
    # 0.02 * 1000 * 100 * 500 * +1 = +1,000,000
    expected = 1_000_000.0
    assert math.isclose(c.gex_dollars(spot), expected)


def test_single_atm_put_gex():
    """Puts have dealer_sign=-1 → negative GEX (destabilizing)."""
    c = OptionContract(
        strike=500.0,
        expiry="2026-04-18",
        option_type="P",
        gamma=0.02,
        vanna=0.0,
        open_interest=1000,
        dealer_sign=-1,
    )
    spot = 500.0
    # 0.02 * 1000 * 100 * 500 * -1 = -1,000,000
    expected = -1_000_000.0
    assert math.isclose(c.gex_dollars(spot), expected)


def test_compute_grid_aggregates_by_strike_and_expiry():
    contracts = [
        OptionContract(500.0, "2026-04-18", "C", 0.02, 0.0, 1000, +1),
        OptionContract(500.0, "2026-04-18", "P", 0.02, 0.0, 500, -1),
        OptionContract(505.0, "2026-04-18", "C", 0.015, 0.0, 200, +1),
    ]
    grid = compute_grid("SPY", 500.0, contracts, timestamp=1_700_000_000)

    assert grid.ticker == "SPY"
    assert grid.spot == 500.0
    assert len(grid.cells) == 2

    cell_500 = next(c for c in grid.cells if c.strike == 500.0)
    # call:  0.02 * 1000 * 100 * 500 * +1 = +1,000,000
    # put:   0.02 * 500  * 100 * 500 * -1 = -500,000
    # net = 500,000 / 1000 = 500 (in $K)
    assert math.isclose(cell_500.gex_value, 500.0)


def test_vex_formula():
    """VEX uses spot × sign."""
    c = OptionContract(
        strike=500.0,
        expiry="2026-04-18",
        option_type="C",
        gamma=0.0,
        vanna=0.01,
        open_interest=1000,
        dealer_sign=+1,
    )
    # 0.01 * 1000 * 100 * 500 * 1 = 500,000
    assert math.isclose(c.vex_dollars(500.0), 500_000.0)


def test_grid_as_matrix_shape():
    contracts = [
        OptionContract(500.0, "2026-04-18", "C", 0.02, 0.0, 100, +1),
        OptionContract(505.0, "2026-04-18", "C", 0.02, 0.0, 100, +1),
        OptionContract(500.0, "2026-04-25", "C", 0.02, 0.0, 100, +1),
        OptionContract(505.0, "2026-04-25", "C", 0.02, 0.0, 100, +1),
    ]
    grid = compute_grid("SPY", 500.0, contracts, timestamp=0)
    mat, strikes, expiries = grid.as_matrix("gex")
    assert mat.shape == (2, 2)
    assert strikes == [500.0, 505.0]
    assert expiries == ["2026-04-18", "2026-04-25"]


def test_empty_grid():
    grid = compute_grid("SPY", 500.0, [], timestamp=0)
    assert grid.cells == []
    assert grid.strikes == []
    assert grid.expiries == []


def test_grid_to_dataframe_roundtrip():
    contracts = [
        OptionContract(500.0, "2026-04-18", "C", 0.02, 0.0, 100, +1),
    ]
    grid = compute_grid("SPY", 500.0, contracts, timestamp=1)
    df = grid_to_dataframe(grid)
    assert len(df) == 1
    assert set(df.columns) == {
        "ts", "ticker", "strike", "expiry", "gex_value", "vex_value", "spot_price"
    }
    assert df.iloc[0]["ticker"] == "SPY"
