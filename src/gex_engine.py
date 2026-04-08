"""
GEX / VEX computation engine.

Formulas (industry-standard, SqueezeMetrics white paper):
    GEX_strike = Σ over contracts at that strike of:
        gamma * OI * contract_size * spot² * 0.01 * dealer_sign

    VEX_strike = Σ over contracts at that strike of:
        vanna * OI * contract_size * spot * 0.01 * dealer_sign

Where:
    dealer_sign = +1 if dealer is long the contract, -1 if short
                  (see sign_imputation.py)
    contract_size = 100 for standard US equity options
    0.01 = 1 percent move (units: $ per 1% move)

Returned values are in thousands of dollars to match Skylit's display units.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd


CONTRACT_SIZE = 100       # standard US equity option multiplier
PCT_MOVE = 0.01           # 1% move basis for GEX/VEX
THOUSANDS = 1_000         # display unit


def _days_to_expiry(expiry_str: str, today: date | None = None) -> int:
    """Days between today and expiry YYYY-MM-DD. Floors at 0."""
    if today is None:
        today = date.today()
    try:
        exp = date.fromisoformat(expiry_str)
    except ValueError:
        return 0
    return max((exp - today).days, 0)


@dataclass
class OptionContract:
    """A single option contract snapshot."""
    strike: float
    expiry: str           # YYYY-MM-DD
    option_type: str      # 'C' or 'P'
    gamma: float
    vanna: float
    open_interest: float
    dealer_sign: int      # +1 long, -1 short — from sign_imputation
    color: float = 0.0    # ∂Γ/∂t — optional, 0 if not populated

    def gex_dollars(self, spot: float) -> float:
        """GEX in raw dollars per 1% move."""
        return (
            self.gamma
            * self.open_interest
            * CONTRACT_SIZE
            * spot ** 2
            * PCT_MOVE
            * self.dealer_sign
        )

    def vex_dollars(self, spot: float) -> float:
        """VEX in raw dollars per 1% vol move."""
        return (
            self.vanna
            * self.open_interest
            * CONTRACT_SIZE
            * spot
            * PCT_MOVE
            * self.dealer_sign
        )

    def color_dollars(self, spot: float) -> float:
        """Color exposure — rate of gamma change per unit time, in dollars."""
        return (
            self.color
            * self.open_interest
            * CONTRACT_SIZE
            * spot ** 2
            * PCT_MOVE
            * self.dealer_sign
        )

    def gex_dollars_normalized(self, spot: float) -> float:
        """
        GEX * sqrt(T) — cancels the natural 1/sqrt(T) gamma scaling so long-
        dated expiries aren't visually dominated by 0DTE near the close.
        Useful for morning analysis or longer-horizon structure.
        """
        T = _days_to_expiry(self.expiry) / 365.0
        if T <= 0:
            T = 0.5 / 365.0
        return self.gex_dollars(spot) * math.sqrt(T)


@dataclass
class GEXCell:
    """One cell of the heatmap grid. All values in $ thousands."""
    strike: float
    expiry: str
    gex_value: float
    vex_value: float
    color_value: float = 0.0     # Color (∂Γ/∂t) exposure
    gex_normalized: float = 0.0  # GEX * sqrt(T) for cross-expiry comparison


@dataclass
class GEXGrid:
    """Full GEX/VEX grid for one ticker at one point in time."""
    ticker: str
    spot: float
    timestamp: int        # epoch seconds
    cells: list[GEXCell] = field(default_factory=list)

    @property
    def strikes(self) -> list[float]:
        return sorted({c.strike for c in self.cells})

    @property
    def expiries(self) -> list[str]:
        return sorted({c.expiry for c in self.cells})

    def as_matrix(self, value: str = "gex") -> tuple[np.ndarray, list[float], list[str]]:
        """
        Return (matrix, strikes, expiries) where matrix[i, j] = value at
        (strikes[i], expiries[j]). Missing cells are 0.

        Supported values:
            'gex'         — raw gamma exposure (default, matches Skylit)
            'gex_norm'    — GEX * sqrt(T), balanced across expiries
            'vex'         — vanna exposure
            'color'       — ∂Γ/∂t exposure
        """
        strikes = self.strikes
        expiries = self.expiries
        s_idx = {s: i for i, s in enumerate(strikes)}
        e_idx = {e: j for j, e in enumerate(expiries)}
        mat = np.zeros((len(strikes), len(expiries)))
        for c in self.cells:
            if value == "gex":
                v = c.gex_value
            elif value == "gex_norm":
                v = c.gex_normalized
            elif value == "vex":
                v = c.vex_value
            elif value == "color":
                v = c.color_value
            else:
                v = c.gex_value
            mat[s_idx[c.strike], e_idx[c.expiry]] = v
        return mat, strikes, expiries


def compute_grid(
    ticker: str,
    spot: float,
    contracts: Iterable[OptionContract],
    timestamp: int,
) -> GEXGrid:
    """Aggregate contracts into a (strike, expiry) grid with all view modes."""
    gex_buckets: dict[tuple[float, str], float] = {}
    vex_buckets: dict[tuple[float, str], float] = {}
    color_buckets: dict[tuple[float, str], float] = {}
    norm_buckets: dict[tuple[float, str], float] = {}

    for c in contracts:
        key = (c.strike, c.expiry)
        gex_buckets[key] = gex_buckets.get(key, 0) + c.gex_dollars(spot)
        vex_buckets[key] = vex_buckets.get(key, 0) + c.vex_dollars(spot)
        color_buckets[key] = color_buckets.get(key, 0) + c.color_dollars(spot)
        norm_buckets[key] = norm_buckets.get(key, 0) + c.gex_dollars_normalized(spot)

    cells = [
        GEXCell(
            strike=strike,
            expiry=expiry,
            gex_value=gex_buckets[(strike, expiry)] / THOUSANDS,
            vex_value=vex_buckets[(strike, expiry)] / THOUSANDS,
            color_value=color_buckets[(strike, expiry)] / THOUSANDS,
            gex_normalized=norm_buckets[(strike, expiry)] / THOUSANDS,
        )
        for (strike, expiry) in gex_buckets.keys()
    ]

    return GEXGrid(ticker=ticker, spot=spot, timestamp=timestamp, cells=cells)


def grid_to_dataframe(grid: GEXGrid) -> pd.DataFrame:
    """Flatten a grid into a DataFrame for display or DB insertion."""
    return pd.DataFrame(
        [
            {
                "ts": grid.timestamp,
                "ticker": grid.ticker,
                "strike": c.strike,
                "expiry": c.expiry,
                "gex_value": c.gex_value,
                "vex_value": c.vex_value,
                "spot_price": grid.spot,
            }
            for c in grid.cells
        ]
    )
