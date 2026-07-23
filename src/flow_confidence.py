"""
Sign-confidence layer — honest caveating of the near-spot node.

Polaris signs each strike with the textbook convention (calls +1, puts -1),
so a strike's GEX sign reduces to sign(call_OI - put_OI): pure resting-OI
composition. That convention is right AWAY from spot but is knowably
unreliable at the churned at-the-money strike, because 0DTE flow there is
dominated by same-day customer buying/selling that the resting-OI sign
cannot see. This is exactly where Polaris and flow-aware maps (Skylit)
disagree on the SIGN of the dominant node — the single cell a 0DTE trader
cares most about.

We do NOT have the trade-level data to compute the true flow sign on the
free CBOE feed (that needs the OPRA tape or OCC customer/firm OI). So the
honest move is not to fake a sign we can't compute — it's to FLAG the cells
where the textbook sign is least trustworthy, so the map stops asserting a
possibly-backwards sign with false confidence.

A near-spot, short-dated cell is flagged LOW confidence when either:
  1. it's heavily churned — today's volume >= churn_tau x resting OI, i.e.
     the strike is being driven by same-day flow, not resting positioning; or
  2. its call/put OI is near-balanced — sign(call_OI - put_OI) is a coin
     flip, so the sign has no real conviction behind it.

Everything else stays HIGH confidence (the textbook sign is fine there).

All thresholds are env-tunable and are meant to be calibrated forward against
Skylit captures via scripts/skylit_ab.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date

from .gex_engine import GEXCell, GEXGrid, _days_to_expiry


# ── Tunables (env-overridable; defaults seeded from the 2026-07-21 divergence)
# Band: how close to spot the caveat applies. ±1% covers the ATM churn zone
# where SPY 740/748/750 and QQQ 705 all lived on 07-21.
BAND_PCT = float(os.environ.get("POLARIS_CONF_BAND_PCT", "0.010"))
# Churn: volume / OI at/above this = same-day-flow-driven, sign unreliable.
CHURN_TAU = float(os.environ.get("POLARIS_CONF_CHURN_TAU", "0.80"))
# Balance: |call_OI - put_OI| / total_OI below this = coin-flip sign.
BALANCE_TOL = float(os.environ.get("POLARIS_CONF_BALANCE_TOL", "0.30"))
# Only caveat short-dated cells — the textbook sign fails hardest at 0DTE.
DTE_MAX = int(os.environ.get("POLARIS_CONF_DTE_MAX", "2"))


@dataclass
class CellConfidence:
    strike: float
    expiry: str
    confidence: str            # "high" | "low"
    churn: float               # volume / OI at this cell (0 if OI unknown)
    reasons: list[str] = field(default_factory=list)

    @property
    def is_low(self) -> bool:
        return self.confidence == "low"


@dataclass
class ConfidenceMap:
    """Per-cell sign confidence for one grid, keyed by (strike, expiry)."""
    cells: dict[tuple[float, str], CellConfidence] = field(default_factory=dict)

    def get(self, strike: float, expiry: str) -> CellConfidence | None:
        return self.cells.get((strike, expiry))

    def is_low(self, strike: float, expiry: str) -> bool:
        c = self.cells.get((strike, expiry))
        return bool(c and c.is_low)

    @property
    def low_keys(self) -> set[tuple[float, str]]:
        return {k for k, c in self.cells.items() if c.is_low}


def _assess_cell(
    cell: GEXCell,
    spot: float,
    today: date | None,
    band_pct: float,
    churn_tau: float,
    balance_tol: float,
    dte_max: int,
) -> CellConfidence:
    churn = cell.churn
    # Away from spot or longer-dated → textbook sign is fine, high confidence.
    near = spot > 0 and abs(cell.strike - spot) / spot <= band_pct
    dte = _days_to_expiry(cell.expiry, today)
    short = dte <= dte_max
    if not (near and short):
        return CellConfidence(cell.strike, cell.expiry, "high", churn)

    reasons: list[str] = []
    if churn >= churn_tau:
        reasons.append(f"heavy 0DTE churn (volume {churn:.1f}x open interest)")
    total_oi = cell.total_oi
    if total_oi > 0:
        imbalance = abs(cell.call_oi - cell.put_oi) / total_oi
        if imbalance < balance_tol:
            reasons.append("call/put OI near-balanced — the sign is a coin flip")

    confidence = "low" if reasons else "high"
    return CellConfidence(cell.strike, cell.expiry, confidence, churn, reasons)


def assess_confidence(
    grid: GEXGrid,
    today: date | None = None,
    band_pct: float = BAND_PCT,
    churn_tau: float = CHURN_TAU,
    balance_tol: float = BALANCE_TOL,
    dte_max: int = DTE_MAX,
) -> ConfidenceMap:
    """Assess per-cell sign confidence for a grid. Pure, no I/O."""
    cmap = ConfidenceMap()
    if grid is None or not grid.cells:
        return cmap
    for cell in grid.cells:
        cmap.cells[(cell.strike, cell.expiry)] = _assess_cell(
            cell, grid.spot, today, band_pct, churn_tau, balance_tol, dte_max
        )
    return cmap


def sirius_confidence(grid: GEXGrid, nodes, today: date | None = None) -> CellConfidence | None:
    """Confidence of the Sirius / Star Node cell specifically — the headline
    cell members read. Returns None if there's no Sirius."""
    if not (nodes and getattr(nodes, "sirius", None)):
        return None
    cmap = assess_confidence(grid, today=today)
    return cmap.get(nodes.sirius.strike, nodes.sirius.expiry)
