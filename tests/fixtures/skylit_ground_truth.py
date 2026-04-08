"""
Ground-truth data from Skylit HeatSeeker screenshots.

Encoded manually from 13 SPY screenshots taken on 2026-04-07
between 15:45 and 15:59 ET, covering the final 14 minutes of
regular session and the 0DTE reshuffle at market close.

Used by scripts/validate_vs_skylit.py and the regression tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkylitGroundTruth:
    timestamp: str         # HH:MM:SS ET
    ticker: str
    spot: float
    sirius_strike: float
    sirius_expiry: str       # YYYY-MM-DD
    sirius_value_k: float    # $ thousands, signed
    # Additional expected top-5 nodes: list of (strike, expiry, value_k)
    top_nodes: list[tuple[float, str, float]] = field(default_factory=list)
    # Persistent structural nodes seen across multiple screenshots
    notes: str = ""


SPY_2026_04_07 = [
    SkylitGroundTruth(
        timestamp="15:45:27",
        ticker="SPY",
        spot=656.72,
        sirius_strike=657.0,
        sirius_expiry="2026-04-07",  # 0DTE
        sirius_value_k=448_043.6,
        notes="Sirius shifted 657→658 when spot crossed 657",
    ),
    SkylitGroundTruth(
        timestamp="15:46:31",
        ticker="SPY",
        spot=658.14,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=437_595.2,
    ),
    SkylitGroundTruth(
        timestamp="15:47:23",
        ticker="SPY",
        spot=657.74,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=493_004.2,
    ),
    SkylitGroundTruth(
        timestamp="15:48:13",
        ticker="SPY",
        spot=658.10,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=521_532.9,
    ),
    SkylitGroundTruth(
        timestamp="15:49:51",
        ticker="SPY",
        spot=657.60,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=506_033.5,
    ),
    SkylitGroundTruth(
        timestamp="15:51:26",
        ticker="SPY",
        spot=658.38,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=539_357.2,
    ),
    SkylitGroundTruth(
        timestamp="15:52:14",
        ticker="SPY",
        spot=658.54,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=531_828.6,
    ),
    SkylitGroundTruth(
        timestamp="15:53:19",
        ticker="SPY",
        spot=658.38,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=548_749.0,
    ),
    SkylitGroundTruth(
        timestamp="15:54:54",
        ticker="SPY",
        spot=658.40,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=510_276.3,
    ),
    SkylitGroundTruth(
        timestamp="15:57:17",
        ticker="SPY",
        spot=658.61,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=487_813.1,
    ),
    SkylitGroundTruth(
        timestamp="15:59:08",
        ticker="SPY",
        spot=658.66,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=487_813.1,
    ),
    SkylitGroundTruth(
        timestamp="15:59:24",
        ticker="SPY",
        spot=658.76,
        sirius_strike=658.0,
        sirius_expiry="2026-04-07",
        sirius_value_k=476_061.7,
    ),
    SkylitGroundTruth(
        timestamp="15:59:44",
        ticker="SPY",
        spot=659.02,
        sirius_strike=660.0,
        sirius_expiry="2026-04-08",
        sirius_value_k=49_004.9,
        notes="0DTE reshuffle: Sirius jumped from 658@0DTE to 660@next-day",
    ),
]


# Persistent structural nodes seen across all snapshots
PERSISTENT_STRUCTURE = {
    "SPY_2026_04_07": {
        # Strong positive cluster near the close
        "positive_cluster": [658, 656, 657],
        # Consistent negative hedge node (Barney)
        "hedge_node": {"strike": 668, "expiry": "2026-04-10", "value_k": -44_000},
        # Stable downside magnet
        "floor_node": {"strike": 640, "expiry": "2026-04-10", "value_k": 30_000},
    }
}


def all_snapshots() -> list[SkylitGroundTruth]:
    return list(SPY_2026_04_07)


def get_by_timestamp(ts: str) -> SkylitGroundTruth | None:
    for snap in SPY_2026_04_07:
        if snap.timestamp == ts:
            return snap
    return None
