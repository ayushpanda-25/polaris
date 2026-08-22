"""Tests for the strike tape's history layer (cell_history.py)."""
from __future__ import annotations

import sqlite3

import pytest

from src.cell_history import (
    EXTENDED_LAGS,
    LAG_MAX_FRAC,
    build_tape,
    cell_key,
    downsample,
    load_values,
    latest_snapshot,
    pick_anchor,
    quantize,
    snapshot_times,
)
from src.sqlite_writer import SCHEMA


NOW = 1_787_000_000
EXPIRIES = ["2026-08-26", "2026-08-27"]
STRIKES = [764.0, 765.0, 766.0]


@pytest.fixture
def db(tmp_path):
    """A store holding 2 days of SPY snapshots at the writer's real cadence."""
    path = tmp_path / "gex.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    rows = []
    for n in range(2500):                      # 2500 × 69s ≈ 2 days back
        ts = NOW - n * 69
        for i, strike in enumerate(STRIKES):
            for j, expiry in enumerate(EXPIRIES):
                # Deterministic and distinguishable per cell and per snapshot.
                gex = 1000.0 * (i + 1) * (j + 1) - n
                rows.append((ts, "SPY", strike, expiry, gex, -gex / 2, 765.5))
    conn.executemany("INSERT OR REPLACE INTO gex_snapshots VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


# ── pure helpers ─────────────────────────────────────────────────────

def test_downsample_keeps_endpoints_and_count():
    thinned = downsample(list(range(500)), 40)
    assert len(thinned) <= 41
    assert thinned[0] == 0 and thinned[-1] == 499


def test_downsample_leaves_short_series_alone():
    assert downsample([1, 2, 3], 40) == [1, 2, 3]


def test_quantize_keeps_what_the_card_can_show():
    # Two decimals of $M is a tenth of a $K, so big cells lose nothing real.
    assert quantize(-139281.71) == -139282
    assert quantize(47.36) == 47.4
    # Small cells keep their decimals — at that scale the cents are the move.
    assert quantize(4.317) == 4.32
    assert quantize(0.0) == 0.0


def test_pick_anchor_takes_the_nearest_snapshot_not_the_older_one():
    # Target is now-60 = 940. 945 is 5s off and 930 is 10s off; taking the
    # newest at-or-before would pick 930 and call a 70s-old number "1 min".
    assert pick_anchor([900, 930, 945, 1000], now=1000, lag=60) == 945


def test_pick_anchor_ignores_anything_too_recent_to_quote():
    # Everything inside half the lookback: a 5s-old value can't be labelled
    # "1 min", so the row goes unavailable instead.
    assert pick_anchor([980, 990, 1000], now=1000, lag=60) is None


def test_pick_anchor_ignores_anything_far_too_old():
    # Only candidate is 5 minutes back for a 1-minute row.
    assert pick_anchor([700], now=1000, lag=60) is None


def test_pick_anchor_spans_a_gap_up_to_the_max_band():
    # A restart left nothing between; 1100 is 100s back for a 60s row, which
    # is inside 2x and therefore usable — the card flags its real age.
    assert pick_anchor([500, 900], now=1000, lag=60) == 900
    assert LAG_MAX_FRAC == 2.0


# ── reads ────────────────────────────────────────────────────────────

def test_snapshot_times_are_ascending_and_bounded(db):
    conn = sqlite3.connect(db)
    times = snapshot_times(conn, "SPY", NOW - 600, NOW)
    conn.close()
    assert times == sorted(times)
    assert all(NOW - 600 <= t <= NOW for t in times)


def test_latest_snapshot_reports_the_newest_row(db):
    assert latest_snapshot(db, "SPY") == NOW


def test_latest_snapshot_is_none_without_a_store(tmp_path):
    # This is what the serverless terminal sees on every poll.
    assert latest_snapshot(tmp_path / "nope.db", "SPY") is None


def test_latest_snapshot_is_none_for_an_unlogged_ticker(db):
    assert latest_snapshot(db, "TSLA") is None


def test_load_values_filters_to_the_exact_visible_cells(db):
    conn = sqlite3.connect(db)
    values = load_values(conn, "SPY", [NOW], [765.0], EXPIRIES, "gex_value")
    conn.close()
    assert set(values) == {cell_key(765.0, e) for e in EXPIRIES}
    # 764 and 766 sit inside the queried strike RANGE but outside the
    # requested set — the Python-side filter has to drop them.
    assert cell_key(764.0, EXPIRIES[0]) not in values


def test_load_values_rejects_an_unknown_column(db):
    conn = sqlite3.connect(db)
    assert load_values(conn, "SPY", [NOW], STRIKES, EXPIRIES, "drop table") == {}
    conn.close()


# ── the payload ──────────────────────────────────────────────────────

def test_build_tape_covers_every_visible_cell(db):
    tape = build_tape(db, "SPY", "gex", STRIKES, EXPIRIES)
    assert tape["stored"] is True
    assert len(tape["cells"]) == len(STRIKES) * len(EXPIRIES)
    for cell in tape["cells"].values():
        assert len(cell["s"]) == len(tape["t"])


def test_build_tape_anchors_land_on_their_lookbacks(db):
    tape = build_tape(db, "SPY", "gex", STRIKES, EXPIRIES)
    for lag, anchor in zip(tape["lags"], tape["ext_ts"]):
        assert anchor is not None
        # Nearest snapshot to the target, so it sits within one cadence of it
        # on either side — never a whole extra window away.
        assert abs((tape["ts"] - lag) - anchor) < 69


def test_build_tape_timestamps_are_ascending_and_inside_the_window(db):
    tape = build_tape(db, "SPY", "gex", STRIKES, EXPIRIES, window=1800)
    assert tape["t"] == sorted(tape["t"])
    assert tape["ts"] - tape["t"][0] <= 1800


def test_build_tape_reports_values_matching_the_store(db):
    tape = build_tape(db, "SPY", "gex", STRIKES, EXPIRIES)
    conn = sqlite3.connect(db)
    latest = conn.execute(
        "SELECT gex_value FROM gex_snapshots WHERE ticker='SPY' AND ts=? "
        "AND strike=765.0 AND expiry=?", (tape["ts"], EXPIRIES[0]),
    ).fetchone()[0]
    conn.close()
    assert tape["cells"][cell_key(765.0, EXPIRIES[0])]["s"][-1] == pytest.approx(latest)


def test_vex_mode_reads_the_vex_column(db):
    gex = build_tape(db, "SPY", "gex", STRIKES, EXPIRIES)
    vex = build_tape(db, "SPY", "vex", STRIKES, EXPIRIES)
    key = cell_key(765.0, EXPIRIES[0])
    assert vex["cells"][key]["s"][-1] == pytest.approx(-gex["cells"][key]["s"][-1] / 2)


def test_gex_norm_scales_by_root_t(db):
    gex = build_tape(db, "SPY", "gex", STRIKES, EXPIRIES)
    norm = build_tape(db, "SPY", "gex_norm", STRIKES, EXPIRIES)
    key = cell_key(765.0, EXPIRIES[0])
    # √T is well under 1 for a near-dated expiry, so the normalized tape has
    # to come back smaller — and same-signed.
    assert abs(norm["cells"][key]["s"][-1]) < abs(gex["cells"][key]["s"][-1])
    assert norm["cells"][key]["s"][-1] * gex["cells"][key]["s"][-1] > 0


def test_color_mode_says_it_is_not_stored(db):
    tape = build_tape(db, "SPY", "color", STRIKES, EXPIRIES)
    assert tape["stored"] is False
    assert tape["reason"] == "not-stored"
    assert tape["cells"] == {}


def test_missing_store_degrades_instead_of_raising(tmp_path):
    tape = build_tape(tmp_path / "nope.db", "SPY", "gex", STRIKES, EXPIRIES)
    assert tape["stored"] is False
    assert tape["reason"] == "no-store"
    # The card still needs these keys to render its empty state.
    assert tape["cells"] == {} and tape["t"] == []
    assert len(tape["lags"]) == len(EXTENDED_LAGS)


def test_unknown_ticker_has_no_history(db):
    tape = build_tape(db, "TSLA", "gex", STRIKES, EXPIRIES)
    assert tape["stored"] is False and tape["reason"] == "no-history"


def test_a_gap_in_logging_drops_the_anchor_rather_than_misdating_it(tmp_path):
    """One recent snapshot and nothing else: every extended row must read
    unavailable instead of quoting a value from the wrong day."""
    path = tmp_path / "gex.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT OR REPLACE INTO gex_snapshots VALUES (?,?,?,?,?,?,?)",
        [(NOW, "SPY", 765.0, EXPIRIES[0], 1000.0, -500.0, 765.5)],
    )
    conn.commit()
    conn.close()
    tape = build_tape(path, "SPY", "gex", [765.0], [EXPIRIES[0]])
    assert tape["ext_ts"] == [None] * len(EXTENDED_LAGS)
    assert tape["cells"][cell_key(765.0, EXPIRIES[0])]["e"] == [None] * len(EXTENDED_LAGS)
