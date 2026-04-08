"""Smoke tests for the Skylit ground-truth fixtures."""
from tests.fixtures.skylit_ground_truth import (
    PERSISTENT_STRUCTURE,
    SPY_2026_04_07,
    all_snapshots,
    get_by_timestamp,
)


def test_13_snapshots_loaded():
    assert len(SPY_2026_04_07) == 13


def test_sirius_stable_at_658_until_close():
    # All snapshots except the last should have Sirius at 658 @ 0DTE
    pre_close = SPY_2026_04_07[:-1]
    # First one is at 657 (edge case when spot was below 657)
    assert pre_close[0].sirius_strike == 657.0
    for snap in pre_close[1:]:
        assert snap.sirius_strike == 658.0
        assert snap.sirius_expiry == "2026-04-07"


def test_reshuffle_at_final_snapshot():
    last = SPY_2026_04_07[-1]
    assert last.sirius_strike == 660.0
    assert last.sirius_expiry == "2026-04-08"
    assert "reshuffle" in last.notes.lower()


def test_all_sirius_values_positive():
    """Per the Skylit screenshots, the dominant cell is bright yellow (positive)."""
    for snap in SPY_2026_04_07:
        assert snap.sirius_value_k > 0, (
            f"{snap.timestamp}: Sirius value should be positive but is {snap.sirius_value_k}"
        )


def test_get_by_timestamp():
    snap = get_by_timestamp("15:53:19")
    assert snap is not None
    assert snap.spot == 658.38
    assert snap.sirius_value_k > 540_000  # was $548,749K


def test_persistent_structure_present():
    assert "SPY_2026_04_07" in PERSISTENT_STRUCTURE
    structure = PERSISTENT_STRUCTURE["SPY_2026_04_07"]
    assert structure["hedge_node"]["strike"] == 668
    assert structure["hedge_node"]["value_k"] < 0  # negative (Barney)
