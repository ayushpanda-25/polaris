"""
Node classification — Sirius, Gatekeeper, Midpoint.

The Sirius is the dominant gravitational center: the cell with the highest
absolute GEX value in the grid. Named after the brightest star in the night
sky — the standout, the one everything orbits around in the dealer
positioning map.

    Sirius:     The strike with the largest |GEX| — where dealers carry
                the largest hedging burden. Price gravitates here by EOD/EOW.
    Gatekeepers: Next-strongest nodes sitting between spot and Sirius,
                acting as deflection levels.
    Midpoint Zones: ~1:1 R/R trap regions between two comparable nodes.

(In Skylit's vocabulary the dominant cell is called the "King Node".
Polaris uses Sirius to keep its naming inside the celestial / Astraios
theme: Polaris guides you, Sirius is what you're aiming at.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .gex_engine import GEXCell, GEXGrid


@dataclass
class Node:
    strike: float
    expiry: str
    value: float         # signed GEX (in $k)
    role: str            # "sirius" | "gatekeeper" | "midpoint"
    significant: bool = True   # False if magnitude gap is too thin to trust


@dataclass
class NodeMap:
    sirius: Optional[Node]
    gatekeepers: list[Node]
    midpoints: list[Node]


# Significance threshold: Sirius |GEX| must be at least this multiple of
# the median |GEX| of the top-K cells to be considered a "clear leader".
# On quiet days the top cell is barely larger than its neighbors and the
# Sirius is meaningless noise.
SIGNIFICANCE_RATIO = 1.5
SIGNIFICANCE_TOP_K = 5


def _is_sirius_significant(grid: GEXGrid, sirius_cell: GEXCell) -> bool:
    """
    Returns True if the Sirius's |GEX| is meaningfully larger than the
    median of the top-K runners-up. Used to suppress "no clear leader" days.
    """
    top_k = sorted(grid.cells, key=lambda c: abs(c.gex_value), reverse=True)[:SIGNIFICANCE_TOP_K]
    if len(top_k) < 2:
        return True  # only one cell — trivially the Sirius
    runners_median = median(abs(c.gex_value) for c in top_k[1:])
    if runners_median == 0:
        return abs(sirius_cell.gex_value) > 0
    return abs(sirius_cell.gex_value) >= SIGNIFICANCE_RATIO * runners_median


def classify_nodes(
    grid: GEXGrid,
    max_gatekeepers: int = 3,
    midpoint_tol: float = 0.2,
) -> NodeMap:
    """Classify the most important nodes in a GEXGrid."""
    if not grid.cells:
        return NodeMap(sirius=None, gatekeepers=[], midpoints=[])

    # Sirius = cell with largest absolute GEX (the dominant magnet)
    sirius_cell = max(grid.cells, key=lambda c: abs(c.gex_value))
    sirius = Node(
        strike=sirius_cell.strike,
        expiry=sirius_cell.expiry,
        value=sirius_cell.gex_value,
        role="sirius",
        significant=_is_sirius_significant(grid, sirius_cell),
    )

    # Gatekeepers: next strongest cells between spot and Sirius
    spot = grid.spot
    lo, hi = sorted([spot, sirius.strike])
    candidates = [
        c for c in grid.cells
        if lo <= c.strike <= hi and not (c.strike == sirius.strike and c.expiry == sirius.expiry)
    ]
    candidates.sort(key=lambda c: abs(c.gex_value), reverse=True)
    gatekeepers = [
        Node(strike=c.strike, expiry=c.expiry, value=c.gex_value, role="gatekeeper")
        for c in candidates[:max_gatekeepers]
    ]

    # Midpoints: pairs of nearby-magnitude nodes with opposite sign,
    # where the mid between them forms a 1:1 R/R trap.
    midpoints = _find_midpoints(grid.cells, midpoint_tol)

    return NodeMap(sirius=sirius, gatekeepers=gatekeepers, midpoints=midpoints)


def _find_midpoints(cells: list[GEXCell], tol: float) -> list[Node]:
    """
    Find midpoint trap zones: the average strike between any two cells
    whose GEX magnitudes are within `tol` fractional distance of each other
    and whose signs differ (one support, one resistance).
    """
    midpoints: list[Node] = []
    seen: set[float] = set()

    # Work with top-N by magnitude to keep O(n^2) manageable
    top = sorted(cells, key=lambda c: abs(c.gex_value), reverse=True)[:15]

    for i, a in enumerate(top):
        for b in top[i + 1 :]:
            if a.gex_value * b.gex_value >= 0:
                continue  # same sign, not a trap
            mag_a, mag_b = abs(a.gex_value), abs(b.gex_value)
            if mag_a == 0 or mag_b == 0:
                continue
            ratio = abs(mag_a - mag_b) / max(mag_a, mag_b)
            if ratio > tol:
                continue
            mid_strike = round((a.strike + b.strike) / 2, 2)
            if mid_strike in seen:
                continue
            seen.add(mid_strike)
            midpoints.append(
                Node(
                    strike=mid_strike,
                    expiry=a.expiry,
                    value=(a.gex_value + b.gex_value) / 2,
                    role="midpoint",
                )
            )

    return midpoints
