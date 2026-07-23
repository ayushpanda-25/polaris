#!/usr/bin/env python3
"""
Skylit A/B harness — measure Polaris against Skylit ground truth.

Two modes:

  summary   Quantify the divergence in a saved Skylit/Polaris pairs CSV
            (Vijay's dataset). Reproduces the headline findings as numbers so
            any formula/confidence change can be re-scored against them.

      python3 scripts/skylit_ab.py summary
      python3 scripts/skylit_ab.py summary --csv validation/gex_pairs_2026-07-21.csv

  live      Compare the CURRENT Polaris grid to a Skylit reading you type in.
            Reports sign match, magnitude ratio, and — crucially — whether
            Polaris now flags that node LOW confidence (the honest caveat).
            This is the go-forward daily check.

      python3 scripts/skylit_ab.py live SPY --strike 740 --sign + --value 12.5 --cboe
      python3 scripts/skylit_ab.py live QQQ --strike 705 --sign - --value 22 --lseg

The point isn't to make Polaris equal Skylit (it can't, without side-resolved
flow data — see FINDINGS). It's to (a) keep the divergence measured, and
(b) confirm the confidence layer lights up exactly where the two disagree.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_CSV = ROOT / "validation" / "gex_pairs_2026-07-21.csv"


# ────────────────────────────────────────────────────────────────────
# summary mode — score a saved pairs CSV
# ────────────────────────────────────────────────────────────────────
def _load_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def summary(csv_path: Path) -> None:
    rows = _load_rows(csv_path)
    if not rows:
        print(f"No rows in {csv_path}")
        return

    # Group by (ticker, flip_strike); collect signs per source.
    by_key: dict[tuple, dict[str, list]] = defaultdict(lambda: {"skylit": [], "polaris": []})
    for r in rows:
        ticker = r.get("ticker")
        strike = r.get("flip_strike")
        src = r.get("source")
        if not ticker or strike is None or src not in ("skylit", "polaris"):
            continue
        by_key[(ticker, strike)][src].append(r)

    def _dom_sign(group):
        """Majority sign across the group's rows, with the +/- counts."""
        signs = [g["flip_sign"] for g in group if g.get("flip_sign") in ("+", "-")]
        plus, minus = signs.count("+"), signs.count("-")
        return ("+" if plus >= minus else "-"), plus, minus

    def _mag_range(group):
        vals = []
        for g in group:
            try:
                vals.append(abs(float(g["flip_gex_musd"])))
            except (ValueError, KeyError, TypeError):
                pass
        return f"{min(vals):.0f}-{max(vals):.0f}M" if vals else "n/a"

    print(f"=== Skylit vs Polaris divergence — {csv_path.name} ===\n")
    print(f"{'ticker':<7}{'strike':>7}{'skylit':>16}{'polaris':>16}{'opposite?':>11}")
    print("-" * 57)
    n_pairs = n_opp = 0
    for (ticker, strike), srcs in sorted(by_key.items()):
        sky, pol = srcs["skylit"], srcs["polaris"]
        if not sky or not pol:
            continue
        s_dom, _, _ = _dom_sign(sky)
        p_dom, _, _ = _dom_sign(pol)
        n_pairs += 1
        opposite = s_dom != p_dom
        n_opp += int(opposite)
        sky_cell = f"{s_dom} ({_mag_range(sky)})"
        pol_cell = f"{p_dom} ({_mag_range(pol)})"
        print(f"{ticker:<7}{strike:>7}{sky_cell:>16}{pol_cell:>16}{'YES' if opposite else 'no':>11}")

    if n_pairs:
        print(
            f"\nKing-node dominant sign OPPOSITE on {n_opp}/{n_pairs} ticker-strikes "
            f"({100*n_opp/n_pairs:.0f}%). Away-from-node strikes agree "
            f"(see FINDINGS). This is the cell the confidence layer flags."
        )


# ────────────────────────────────────────────────────────────────────
# live mode — compare current Polaris to a typed Skylit reading
# ────────────────────────────────────────────────────────────────────
def live(args) -> None:
    from src.compute_loop import ComputeLoop
    from src.data_feed import make_feed
    from src.flow_confidence import assess_confidence
    from src.memory_cache import GEXCache
    from src.node_classifier import classify_nodes

    mode = "cboe" if args.cboe else ("lseg" if args.lseg else "synthetic")
    feed = make_feed(mode)
    cache = GEXCache()
    loop = ComputeLoop(feed, cache, [args.ticker], interval=60)
    loop._tick()
    grid = cache.get_grid(args.ticker)
    if grid is None or not grid.cells:
        print(f"No grid for {args.ticker} ({mode} feed)")
        return
    nodes = cache.get_nodes(args.ticker)
    cmap = assess_confidence(grid)

    # Polaris's own value at the Skylit strike: sum across expiries at that
    # strike (Skylit's king node is a strike-level read).
    near = min(grid.strikes, key=lambda s: abs(s - args.strike))
    strike_cells = [c for c in grid.cells if c.strike == near]
    pol_val_k = sum(c.gex_value for c in strike_cells)  # $ thousands, signed
    pol_val_m = pol_val_k / 1000.0
    pol_sign = "+" if pol_val_m >= 0 else "-"
    any_low = any(cmap.is_low(c.strike, c.expiry) for c in strike_cells)

    print(f"=== live A/B — {args.ticker} ({mode}) ===")
    print(f"Polaris spot:  ${grid.spot:.2f}")
    print(f"Strike:        {near:g}  (you gave {args.strike:g})")
    print(f"Skylit:        {args.sign} ${args.value:.1f}M")
    print(f"Polaris:       {pol_sign} ${pol_val_m:.1f}M")
    sign_match = "MATCH" if pol_sign == args.sign else "OPPOSITE"
    print(f"Sign:          {sign_match}")
    if args.value:
        ratio = abs(pol_val_m) / abs(args.value) if args.value else float("inf")
        print(f"Magnitude:     Polaris/Skylit = {ratio:.2f}x")
    print(
        f"Confidence:    {'⚠ LOW (flagged — caveat shown to members)' if any_low else 'high (no caveat)'}"
    )
    if pol_sign != args.sign and not any_low:
        print(
            "\nNOTE: signs disagree but Polaris did NOT flag it — consider "
            "tuning POLARIS_CONF_* (band/churn/balance) so this node is caveated."
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Skylit A/B harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("summary", help="score a saved pairs CSV")
    ps.add_argument("--csv", type=Path, default=DEFAULT_CSV)

    pl = sub.add_parser("live", help="compare current Polaris to a Skylit reading")
    pl.add_argument("ticker")
    pl.add_argument("--strike", type=float, required=True)
    pl.add_argument("--sign", choices=["+", "-"], required=True)
    pl.add_argument("--value", type=float, default=0.0, help="Skylit |value| in $M")
    pl.add_argument("--cboe", action="store_true")
    pl.add_argument("--lseg", action="store_true")

    args = p.parse_args()
    if args.cmd == "summary":
        summary(args.csv)
    elif args.cmd == "live":
        live(args)


if __name__ == "__main__":
    main()
