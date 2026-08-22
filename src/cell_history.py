"""
Cell tape — the per-strike history behind the hover card.

Hovering a cell on the board should answer "is this node building or
bleeding?", not just "how big is it right now". Everything needed for that is
already on disk: `sqlite_writer` has been appending the full grid to
`gex_snapshots` every DB_FLUSH_INTERVAL (~69s in practice) since June, so a
cell's whole life is one indexed range-scan away.

This module turns that table into a compact per-poll payload:

    {"t":     [1787424475, ..., 1787426275],   # epoch seconds, ascending
     "cells": {"765|2026-08-26": {"s": [...],  # value at each t (null = gap)
                                  "e": [v1h, v4h, v1d]}},
     ...}

Timestamps are absolute epoch seconds, and the payload carries the server's
wall clock (`served`) so the browser can correct for its own clock skew before
merging these points with the ones it accumulated itself.

It ships PAST VALUES, never deltas. The card computes deltas in the browser
against the LIVE value from the cache, which is up to a flush-interval newer
than anything on disk — differencing on the server would quietly compare the
live number against a stale baseline of a different vintage.

Cost control: the whole visible grid (~270 cells) over 30 minutes is ~24
snapshots × 270 rows ≈ 6.5k rows, which comes back in ~15ms. We only ever ask
for the snapshot timestamps we actually plot (plus three anchors), so the query
never scans a day of rows to draw a half-hour line.

Serverless has no gex.db — `build_tape` returns an empty tape there and the
card falls back to the ring buffer the browser accumulates while you watch.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

# How far back the sparkline reaches, and how many points it's drawn from.
TAPE_WINDOW_SEC = int(os.environ.get("POLARIS_TAPE_WINDOW", str(30 * 60)))
TAPE_MAX_POINTS = int(os.environ.get("POLARIS_TAPE_POINTS", "40"))

# The second block on the card. (seconds, label) — the labels are the card's.
EXTENDED_LAGS: tuple[tuple[int, str], ...] = (
    (3600, "1 hour"),
    (4 * 3600, "4 hours"),
    (86400, "1 day"),
)

# How far an anchor may sit from its nominal lookback and still be used, as a
# fraction of that lookback. A "1 hour" row may be measured anywhere from 30
# minutes to 2 hours back; outside that it reads "—" rather than mislabelling
# a number from the wrong session. Overnight and weekend gaps are exactly why
# this exists: the app logs around the clock, but a restart or a dead feed
# leaves a hole, and a hole must not be papered over.
LAG_MIN_FRAC = float(os.environ.get("POLARIS_TAPE_MIN_FRAC", "0.5"))
LAG_MAX_FRAC = float(os.environ.get("POLARIS_TAPE_MAX_FRAC", "2.0"))

# Only two metrics are stored per cell. 'gex_norm' is derived from gex_value
# (it's GEX × √T), and 'color' (∂Γ/∂t) was never written to disk — that view
# gets a live-only tape.
_MODE_COLUMN = {"gex": "gex_value", "gex_norm": "gex_value", "vex": "vex_value"}

_EMPTY: dict = {"t": [], "cells": {}, "window": TAPE_WINDOW_SEC,
          "stored": False, "ts": None, "served": None,
          "lags": [lag for lag, _ in EXTENDED_LAGS],
          "labels": [label for _, label in EXTENDED_LAGS],
          "ext_ts": [None] * len(EXTENDED_LAGS)}


def quantize(v: float) -> float:
    """Trim a value to the precision the card can actually show.

    Values are thousands of dollars and the card renders at most two decimal
    places of $M — a tenth of a $K — so carrying more than this over the wire
    is pure payload. Small cells keep their decimals, because a $4.30K cell
    moving to $4.55K is a real move at that scale.
    """
    a = abs(v)
    if a >= 1000:
        return round(v)
    if a >= 10:
        return round(v, 1)
    return round(v, 2)


def cell_key(strike: float, expiry: str) -> str:
    """Stable identity for one heatmap cell, shared with the browser.

    `%g` matches the strike labels the board already renders (765, 765.5),
    so the card can look a cell up straight from Plotly's hover payload.
    """
    return f"{strike:g}|{expiry}"


def _connect(db_path: Path) -> Optional[sqlite3.Connection]:
    """Read-only connection, or None when there's no store (serverless)."""
    if not db_path or not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    # The writer flushes the whole grid every 60s; wait it out rather than
    # failing a hover payload with "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def latest_snapshot(db_path: Path, ticker: str) -> Optional[int]:
    """Newest snapshot timestamp logged for `ticker`, or None.

    One indexed MAX(ts) — cheap enough to run on every poll, which is the
    point: it tells a caller whether a cached tape is still current without
    rebuilding it.
    """
    conn = _connect(db_path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(ts) FROM gex_snapshots WHERE ticker = ?", (ticker,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def snapshot_times(conn: sqlite3.Connection, ticker: str, since: int,
                   until: Optional[int] = None) -> list[int]:
    """Ascending snapshot timestamps logged for `ticker` since `since`."""
    sql = "SELECT DISTINCT ts FROM gex_snapshots WHERE ticker = ? AND ts >= ?"
    params: list = [ticker, int(since)]
    if until is not None:
        sql += " AND ts <= ?"
        params.append(int(until))
    return [r[0] for r in conn.execute(sql + " ORDER BY ts", params)]


def downsample(times: Sequence[int], max_points: int) -> list[int]:
    """Thin `times` to at most `max_points`, always keeping the newest.

    Evenly spaced by index rather than by clock, so a gap in logging shows up
    as a gap in the line instead of being silently interpolated away.
    """
    times = list(times)
    if max_points <= 0 or len(times) <= max_points:
        return times
    n = len(times)
    step = (n - 1) / (max_points - 1)
    idx = sorted({min(n - 1, round(i * step)) for i in range(max_points)})
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [times[i] for i in idx]


def pick_anchor(times: Sequence[int], now: int, lag: int,
                min_frac: float = LAG_MIN_FRAC,
                max_frac: float = LAG_MAX_FRAC) -> Optional[int]:
    """Snapshot nearest to `now - lag`, among those in the acceptable age band.

    NEAREST, not newest-at-or-before: at a 69-second logging cadence the last
    snapshot strictly older than a 60-second lookback can be over two minutes
    back, which would flag the 1-minute row as approximate on every board. The
    snapshot 55 seconds back is plainly the better answer, and the age band
    keeps "nearest" from drifting into something far too recent to be quoted
    under the row's label.

    Returns None when nothing falls in the band — a gap reads "—".
    """
    oldest, newest = now - lag * max_frac, now - lag * min_frac
    best = None
    for ts in times:
        if ts < oldest or ts > newest:
            continue
        if best is None or abs(ts - (now - lag)) < abs(best - (now - lag)):
            best = ts
    return best


def _norm_factor(expiry: str, ts: int) -> float:
    """√T at snapshot time — mirrors GEXCell.gex_normalized's flooring."""
    try:
        exp = date.fromisoformat(expiry)
    except ValueError:
        return 1.0
    dte = max((exp - datetime.fromtimestamp(ts).date()).days, 0)
    T = dte / 365.0
    if T <= 0:
        T = 0.5 / 365.0
    return math.sqrt(T)


def load_values(
    conn: sqlite3.Connection,
    ticker: str,
    times: Sequence[int],
    strikes: Sequence[float],
    expiries: Sequence[str],
    column: str,
) -> dict[str, dict[int, float]]:
    """{cell_key: {ts: value}} for the requested cells at the requested times.

    Bounded by strike RANGE (two params) rather than a strike IN-list, then
    filtered exactly in Python — the visible board can be 60+ strikes wide and
    there's no reason to spend a host parameter on each one.
    """
    if not times or not strikes or not expiries:
        return {}
    if column not in ("gex_value", "vex_value"):
        return {}
    want = {float(s) for s in strikes}
    t_ph = ",".join("?" * len(times))
    e_ph = ",".join("?" * len(expiries))
    sql = (
        f"SELECT ts, strike, expiry, {column} FROM gex_snapshots "
        f"WHERE ticker = ? AND ts IN ({t_ph}) AND expiry IN ({e_ph}) "
        f"AND strike BETWEEN ? AND ?"
    )
    params = [ticker, *[int(t) for t in times], *expiries,
              min(want), max(want)]
    out: dict[str, dict[int, float]] = {}
    for ts, strike, expiry, value in conn.execute(sql, params):
        if strike not in want:
            continue
        out.setdefault(cell_key(strike, expiry), {})[ts] = value
    return out


def build_tape(
    db_path: Path,
    ticker: str,
    mode: str,
    strikes: Sequence[float],
    expiries: Sequence[str],
    now: Optional[int] = None,
    window: int = TAPE_WINDOW_SEC,
    max_points: int = TAPE_MAX_POINTS,
) -> dict:
    """Build the hover-card history payload for one board.

    `strikes` / `expiries` are the VISIBLE grid (post-trim), so the payload
    covers exactly what a viewer can hover and nothing else.
    """
    empty = dict(_EMPTY, window=window)
    column = _MODE_COLUMN.get(mode)
    if column is None:
        # 'color' (∂Γ/∂t) has no stored column — say so instead of pretending.
        return dict(empty, reason="not-stored")

    conn = _connect(db_path)
    if conn is None:
        return dict(empty, reason="no-store")
    try:
        latest = conn.execute(
            "SELECT MAX(ts) FROM gex_snapshots WHERE ticker = ?", (ticker,)
        ).fetchone()
        latest = latest[0] if latest else None
        if latest is None:
            return dict(empty, reason="no-history")
        # Anchor the tape to the newest LOGGED snapshot, not the wall clock:
        # after a gap (restart, dead feed) that keeps the line drawn where the
        # data actually is instead of pushing it off the left edge.
        now = int(now if now is not None else latest)

        spark_times = downsample(
            snapshot_times(conn, ticker, now - window, now), max_points
        )
        oldest = int(now - EXTENDED_LAGS[-1][0] * LAG_MAX_FRAC)
        ext_times = snapshot_times(conn, ticker, oldest, now)
        anchors: list[Optional[int]] = [
            pick_anchor(ext_times, now, lag) for lag, _label in EXTENDED_LAGS
        ]

        wanted = sorted({*spark_times, *[a for a in anchors if a is not None]})
        values = load_values(conn, ticker, wanted, strikes, expiries, column)
    finally:
        conn.close()

    normalize = mode == "gex_norm"
    cells: dict[str, dict] = {}
    for key, by_ts in values.items():
        expiry = key.split("|", 1)[1]

        def _at(ts: Optional[int]) -> Optional[float]:
            if ts is None:
                return None
            v = by_ts.get(ts)
            if v is None:
                return None
            if normalize:
                v *= _norm_factor(expiry, ts)
            return quantize(v)

        spark = [_at(ts) for ts in spark_times]
        ext = [_at(ts) for ts in anchors]
        if not any(v is not None for v in spark) and not any(v is not None for v in ext):
            continue
        cells[key] = {"s": spark, "e": ext}

    return {
        "t": list(spark_times),
        "cells": cells,
        "window": window,
        "stored": True,
        # Newest snapshot on disk (what the tape is anchored to) vs the wall
        # clock at build time. The browser needs `served` to correct its own
        # clock against the server's before merging in its ring buffer.
        "ts": now,
        "served": int(time.time()),
        "lags": [lag for lag, _ in EXTENDED_LAGS],
        "labels": [label for _, label in EXTENDED_LAGS],
        # When each extended anchor actually landed, so the card can say "1
        # day" while telling the truth about what it measured.
        "ext_ts": anchors,
    }
