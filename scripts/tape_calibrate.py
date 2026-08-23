"""
Calibrate the strike tape's badges against real sessions.

The HOT / FADING / SIGN FLIP thresholds were picked by eye on a weekend board
where nothing moved, which is no way to set them. gex_snapshots has months of
real market hours in it, so replay those instead of waiting for an open and
guessing: for every cell of every board in the sample, compute the same 5-minute
change the card computes, and count how often each rule would have fired.

What you want out of this is a firing RATE, not a verdict. A badge that lights
two or three cells on a busy board is doing its job; one that lights forty is
wallpaper, and one that never lights is dead weight.

    python3 scripts/tape_calibrate.py --ticker SPY --days 5
    python3 scripts/tape_calibrate.py --ticker SPY --days 5 --sweep

The anchor selection is imported from cell_history, not reimplemented, so this
measures the rule that actually ships.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cell_history import pick_anchor
import config as app_config

# Mirrors of the constants in assets/tape.js. Keep them in step.
HOT_PCT = 35.0
HOT_FLOOR = 0.08
FIVE_MIN = 300

# US cash session in local (Central) time, which is what the timestamps are in.
RTH_OPEN, RTH_CLOSE = dtime(8, 30), dtime(15, 0)


def rth(ts: int) -> bool:
    d = datetime.fromtimestamp(ts)
    return d.weekday() < 5 and RTH_OPEN <= d.time() <= RTH_CLOSE


def load_session(conn, ticker: str, start: int, end: int):
    """{ts: {cell: value}} plus the sorted ts list, for one span of real trading."""
    rows = conn.execute(
        "SELECT ts, strike, expiry, gex_value, spot_price FROM gex_snapshots "
        "WHERE ticker = ? AND ts BETWEEN ? AND ?",
        (ticker, start, end),
    )
    boards: dict[int, dict] = defaultdict(dict)
    spots: dict[int, float] = {}
    for ts, strike, expiry, gex, spot in rows:
        if not rth(ts):
            continue
        boards[ts][(strike, expiry)] = gex
        spots[ts] = spot
    return boards, spots


def visible(board: dict, spot: float):
    """The ±3% / 6-expiry window the board actually draws."""
    lo, hi = spot * 0.97, spot * 1.03
    exps = sorted({e for _s, e in board})[:6]
    keep = set(exps)
    return {k: v for k, v in board.items() if lo <= k[0] <= hi and k[1] in keep}


def evaluate(boards, spots, hot_pct=HOT_PCT, hot_floor=HOT_FLOOR):
    """Replay every board and count what the badges would have done."""
    times = sorted(boards)
    stats = {"boards": 0, "cells": 0, "hot": 0, "fading": 0, "flip": 0,
             "no_anchor": 0, "pcts": [], "per_board_hot": []}
    for ts in times:
        board = visible(boards[ts], spots[ts])
        if not board:
            continue
        vmax = max((abs(v) for v in board.values()), default=0.0)
        if vmax <= 0:
            continue
        anchor = pick_anchor(times, ts, FIVE_MIN)
        if anchor is None:
            stats["no_anchor"] += 1
            continue
        past_board = boards[anchor]
        stats["boards"] += 1
        lit = 0
        for cell, cur in board.items():
            past = past_board.get(cell)
            if past is None or past == 0:
                continue
            stats["cells"] += 1
            pct = (cur - past) / abs(past) * 100.0
            stats["pcts"].append(pct)
            if (past < 0) != (cur < 0) and cur != 0:
                stats["flip"] += 1
                continue                       # SIGN FLIP wins the badge slot
            if abs(cur) < hot_floor * vmax:
                continue
            if pct >= hot_pct:
                stats["hot"] += 1
                lit += 1
            elif pct <= -hot_pct:
                stats["fading"] += 1
        stats["per_board_hot"].append(lit)
    return stats


def report(name, s):
    if not s["boards"]:
        print(f"  {name}: no usable boards")
        return
    pcts = sorted(abs(p) for p in s["pcts"])
    def pctile(q):
        return pcts[min(len(pcts) - 1, int(len(pcts) * q))] if pcts else 0.0
    print(f"  {name}")
    print(f"    boards replayed      {s['boards']:,}   cells evaluated {s['cells']:,}")
    print(f"    HOT                  {s['hot']:,}  ({s['hot']/s['cells']*100:.2f}% of cells,"
          f" {s['hot']/s['boards']:.2f} per board)")
    print(f"    FADING               {s['fading']:,}  ({s['fading']/s['cells']*100:.2f}%,"
          f" {s['fading']/s['boards']:.2f} per board)")
    print(f"    SIGN FLIP            {s['flip']:,}  ({s['flip']/s['cells']*100:.2f}%,"
          f" {s['flip']/s['boards']:.2f} per board)")
    print(f"    |5-min move| median {pctile(0.50):6.1f}%   p90 {pctile(0.90):6.1f}%"
          f"   p99 {pctile(0.99):6.1f}%")
    if s["per_board_hot"]:
        print(f"    busiest board        {max(s['per_board_hot'])} cells lit HOT")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument("--days", type=int, default=5, help="calendar days back to replay")
    ap.add_argument("--sweep", action="store_true",
                    help="try a range of thresholds instead of just the shipped one")
    ap.add_argument("--db", default=str(app_config.DB_PATH))
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=20)
    latest = conn.execute(
        "SELECT MAX(ts) FROM gex_snapshots WHERE ticker = ?", (args.ticker,)
    ).fetchone()[0]
    if latest is None:
        print(f"no history for {args.ticker}")
        return 1
    start = latest - args.days * 86400
    print(f"{args.ticker}: replaying market hours from "
          f"{datetime.fromtimestamp(start):%Y-%m-%d} to "
          f"{datetime.fromtimestamp(latest):%Y-%m-%d}\n")
    boards, spots = load_session(conn, args.ticker, start, latest)
    conn.close()
    if not boards:
        print("no RTH snapshots in that span (weekend or feed gap)")
        return 1

    if not args.sweep:
        report(f"shipped thresholds (HOT ≥ +{HOT_PCT:.0f}%, floor {HOT_FLOOR:.0%} of board max)",
               evaluate(boards, spots))
        return 0

    print("  threshold sweep — HOT firings per board\n")
    print(f"    {'pct':>6} " + "".join(f"{f'floor {f:.0%}':>12}" for f in (0.04, 0.08, 0.15)))
    for pct in (15, 25, 35, 50, 75, 100):
        cells = []
        for floor in (0.04, 0.08, 0.15):
            s = evaluate(boards, spots, hot_pct=pct, hot_floor=floor)
            cells.append(s["hot"] / s["boards"] if s["boards"] else 0.0)
        print(f"    {pct:>5}% " + "".join(f"{c:>12.2f}" for c in cells))
    print("\n  Aim for roughly 1-3 HOT cells on a board: enough to point at,")
    print("  few enough that pointing means something.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
