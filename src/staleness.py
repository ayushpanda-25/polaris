"""
Staleness detection for live dashboard data.

The dashboard's compute_loop pulls fresh chains every 15 seconds. If the
LSEG bridge dies, the Mac sleeps, or anything in the pipeline fails, the
in-memory cache will start serving stale data. This module decides what
"stale" means and how to display it to a viewer who shouldn't be misled.

States:
    LIVE      — data is < 30s old, fresh, trust it
    LAGGING   — data is 30s–2min old, suspect, show with warning
    STALE     — data is 2min–10min old, do not trust, banner the page
    OFFLINE   — data is > 10min old or missing, treat as no data at all

Viewers see:
    🟢 LIVE  ·  updated 3s ago
    🟡 LAGGING  ·  updated 47s ago
    🔴 STALE  ·  updated 4m ago — bridge may be down
    ⚫ OFFLINE  ·  no data — Mac asleep or bridge stopped
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FreshnessState(Enum):
    LIVE = "live"
    LAGGING = "lagging"
    STALE = "stale"
    OFFLINE = "offline"


# Thresholds in seconds. Tuned for 15s compute interval + some slack.
LIVE_THRESHOLD = 30        # Up to 30s = live (1 missed cycle is OK)
LAGGING_THRESHOLD = 120    # Up to 2min = lagging (warn but show)
STALE_THRESHOLD = 600      # Up to 10min = stale (red banner, don't trust)
                           # Beyond 10min = OFFLINE


@dataclass
class FreshnessStatus:
    state: FreshnessState
    age_seconds: Optional[float]    # None if no data at all
    last_update_ts: Optional[int]   # epoch seconds, None if no data
    message: str                    # human-readable for the banner

    @property
    def color(self) -> str:
        return {
            FreshnessState.LIVE: "#3edc81",      # green
            FreshnessState.LAGGING: "#f9d649",   # yellow
            FreshnessState.STALE: "#ff5959",     # red
            FreshnessState.OFFLINE: "#7a7a7a",   # grey
        }[self.state]

    @property
    def emoji(self) -> str:
        return {
            FreshnessState.LIVE: "🟢",
            FreshnessState.LAGGING: "🟡",
            FreshnessState.STALE: "🔴",
            FreshnessState.OFFLINE: "⚫",
        }[self.state]

    @property
    def label(self) -> str:
        return {
            FreshnessState.LIVE: "LIVE",
            FreshnessState.LAGGING: "LAGGING",
            FreshnessState.STALE: "STALE",
            FreshnessState.OFFLINE: "OFFLINE",
        }[self.state]


def _format_age(seconds: float) -> str:
    """Human relative time: '3s ago', '47s ago', '4m ago', '1h 12m ago'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        m = s // 60
        rem = s % 60
        return f"{m}m {rem}s ago" if rem and m < 5 else f"{m}m ago"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m ago"


def evaluate_freshness(
    last_update_ts: Optional[int],
    now: Optional[float] = None,
) -> FreshnessStatus:
    """
    Decide the freshness state given the most recent cache timestamp.

    Args:
        last_update_ts: epoch seconds of the last successful compute, or
            None if the cache has never been populated
        now: epoch seconds (current time) — defaults to time.time()
    """
    if now is None:
        now = time.time()

    if last_update_ts is None:
        return FreshnessStatus(
            state=FreshnessState.OFFLINE,
            age_seconds=None,
            last_update_ts=None,
            message="No data yet — bridge may not be running",
        )

    age = now - last_update_ts

    if age < 0:
        # Clock skew — treat as live
        age = 0

    if age <= LIVE_THRESHOLD:
        return FreshnessStatus(
            state=FreshnessState.LIVE,
            age_seconds=age,
            last_update_ts=last_update_ts,
            message=f"Live · updated {_format_age(age)}",
        )

    if age <= LAGGING_THRESHOLD:
        return FreshnessStatus(
            state=FreshnessState.LAGGING,
            age_seconds=age,
            last_update_ts=last_update_ts,
            message=f"Lagging · last update {_format_age(age)} — feed may be slow",
        )

    if age <= STALE_THRESHOLD:
        return FreshnessStatus(
            state=FreshnessState.STALE,
            age_seconds=age,
            last_update_ts=last_update_ts,
            message=(
                f"⚠️  STALE — last update {_format_age(age)}. "
                f"Bridge likely down or Mac asleep. Do not trust these values."
            ),
        )

    return FreshnessStatus(
        state=FreshnessState.OFFLINE,
        age_seconds=age,
        last_update_ts=last_update_ts,
        message=(
            f"⛔  OFFLINE — last update {_format_age(age)}. "
            f"Waiting for bridge to come back online…"
        ),
    )


def latest_cache_timestamp(cache) -> Optional[int]:
    """Return the most recent grid timestamp across all tickers in the cache."""
    snapshots = cache.snapshot_all()
    if not snapshots:
        return None
    return max(grid.timestamp for grid, _nodes in snapshots.values())
