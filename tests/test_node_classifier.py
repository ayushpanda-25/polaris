"""Unit tests for node classification."""
from src.gex_engine import GEXCell, GEXGrid
from src.node_classifier import classify_nodes


def _grid_from_cells(cells: list[GEXCell], spot: float = 500.0) -> GEXGrid:
    return GEXGrid(ticker="SPY", spot=spot, timestamp=0, cells=cells)


def test_king_node_is_largest_absolute_value():
    cells = [
        GEXCell(495.0, "2026-04-18", gex_value=100.0, vex_value=0),
        GEXCell(500.0, "2026-04-18", gex_value=50.0, vex_value=0),
        GEXCell(505.0, "2026-04-18", gex_value=-200.0, vex_value=0),  # biggest |v|
        GEXCell(510.0, "2026-04-18", gex_value=150.0, vex_value=0),
    ]
    nm = classify_nodes(_grid_from_cells(cells))
    assert nm.king is not None
    assert nm.king.strike == 505.0
    assert nm.king.role == "king"
    assert nm.king.value == -200.0


def test_gatekeepers_sit_between_spot_and_king():
    spot = 500.0
    cells = [
        GEXCell(500.0, "2026-04-18", gex_value=-50.0, vex_value=0),
        GEXCell(503.0, "2026-04-18", gex_value=-80.0, vex_value=0),  # gatekeeper
        GEXCell(505.0, "2026-04-18", gex_value=-60.0, vex_value=0),  # gatekeeper
        GEXCell(510.0, "2026-04-18", gex_value=-200.0, vex_value=0),  # king
        GEXCell(490.0, "2026-04-18", gex_value=-40.0, vex_value=0),  # below spot
    ]
    nm = classify_nodes(_grid_from_cells(cells, spot=spot))
    assert nm.king.strike == 510.0
    gk_strikes = {g.strike for g in nm.gatekeepers}
    assert 503.0 in gk_strikes
    assert 505.0 in gk_strikes
    # Below-spot node should NOT be a gatekeeper when king is above spot
    assert 490.0 not in gk_strikes


def test_midpoint_detection():
    """Two opposite-sign nodes with similar magnitude create a midpoint trap."""
    cells = [
        GEXCell(498.0, "2026-04-18", gex_value=100.0, vex_value=0),
        GEXCell(502.0, "2026-04-18", gex_value=-95.0, vex_value=0),  # ~5% diff
    ]
    nm = classify_nodes(_grid_from_cells(cells))
    # Midpoint at (498+502)/2 = 500
    mids = [m.strike for m in nm.midpoints]
    assert 500.0 in mids


def test_empty_grid_returns_empty_map():
    nm = classify_nodes(_grid_from_cells([]))
    assert nm.king is None
    assert nm.gatekeepers == []
    assert nm.midpoints == []
