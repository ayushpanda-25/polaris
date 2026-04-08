#!/usr/bin/env python3
"""
Validation script — compare our GEX output to a Skylit screenshot.

Usage:
    python3 scripts/validate_vs_skylit.py SPY 588.0
    python3 scripts/validate_vs_skylit.py SPX 6941.0 --king-strike 6955

The script computes the current GEX grid for a ticker (using the synthetic
feed unless --lseg is passed), classifies nodes, and prints a side-by-side
comparison with the user-provided expected King Node.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path so `import src.*` works
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.compute_loop import ComputeLoop
from src.data_feed import make_feed
from src.memory_cache import GEXCache
from src.node_classifier import classify_nodes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticker")
    p.add_argument("expected_spot", type=float, help="Spot price visible in the Skylit screenshot")
    p.add_argument("--king-strike", type=float, default=None, help="Skylit's King Node strike")
    p.add_argument("--king-expiry", default=None, help="Skylit's King Node expiry (YYYY-MM-DD)")
    p.add_argument("--lseg", action="store_true")
    args = p.parse_args()

    mode = "lseg" if args.lseg else "synthetic"
    feed = make_feed(mode)
    cache = GEXCache()
    loop = ComputeLoop(feed, cache, [args.ticker], interval=60)
    loop._tick()

    grid = cache.get_grid(args.ticker)
    nodes = cache.get_nodes(args.ticker)

    print(f"=== {args.ticker} GEX Validation ({mode}) ===")
    print(f"Our spot:      ${grid.spot:.2f}")
    print(f"Skylit spot:   ${args.expected_spot:.2f}")
    print(f"Strikes:       {len(grid.strikes)}")
    print(f"Expiries:      {len(grid.expiries)}")
    print()

    if nodes.king:
        k = nodes.king
        print(f"Our King Node:    strike {k.strike}  expiry {k.expiry}  ${k.value:,.0f}k")
    else:
        print("No King Node found")

    if args.king_strike is not None:
        print(f"Skylit King:      strike {args.king_strike}  expiry {args.king_expiry or '?'}")
        if nodes.king:
            diff = abs(nodes.king.strike - args.king_strike)
            match = "✅" if diff <= 2 else "⚠️"
            print(f"King strike match: {match}  (Δ = {diff})")

    print()
    print("Top 5 nodes by |GEX|:")
    top5 = sorted(grid.cells, key=lambda c: abs(c.gex_value), reverse=True)[:5]
    for c in top5:
        print(f"  {c.strike:>8.2f}  {c.expiry}  ${c.gex_value:>+10,.0f}k")


if __name__ == "__main__":
    main()
