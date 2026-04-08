#!/usr/bin/env python3
"""
End-of-day OI reconciliation backfill.

Pulls yesterday's end-of-day open interest for each contract in our
universe, reads the intraday volume flow from our SQLite store (or
optionally from LSEG historical), and runs the Layer 2 OI reconciliation
to refine dealer sign estimates.

Run nightly after market close.

Usage:
    python3 scripts/backfill_eod.py [--ticker SPY] [--date 2026-04-07]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as app_config
from src.sign_imputation import ContractDay, reconcile_oi


def run_backfill(target_date: date, tickers: list[str], dry_run: bool = False):
    """
    For each ticker, pull contract-level volume and OI for target_date,
    run reconcile_oi(), and print the refined open/close breakdown.

    This is a stub for the real LSEG historical path — currently it
    requires the bridge to be running and `rd.get_history()` to work,
    which is a Milestone 2.5 item. The Layer 2 math itself is tested
    and working against synthetic data.
    """
    print(f"EOD reconciliation for {target_date}")
    print(f"Tickers: {', '.join(tickers)}")
    print()

    try:
        import refinitiv.data as rd
        rd.open_session(name="desktop.workspace")
        lseg_available = True
    except Exception as e:
        print(f"⚠️  LSEG session unavailable: {e}")
        print("   Falling back to synthetic demonstration.")
        lseg_available = False

    # Demo: synthetic reconciliation for SPY 580 call
    demo = ContractDay(
        ticker="SPY",
        strike=580.0,
        expiry=(target_date + timedelta(days=1)).isoformat(),
        option_type="C",
        buy_volume=5000,
        sell_volume=4500,
        oi_start=12000,
        oi_end=12300,  # +300 ΔOI
    )
    bo, so, bc, sc = reconcile_oi(demo)
    print(f"Demo reconciliation: SPY 580C {demo.expiry}")
    print(f"  Volume: buy={demo.buy_volume}, sell={demo.sell_volume}")
    print(f"  OI: {demo.oi_start} → {demo.oi_end} (Δ={demo.oi_delta:+.0f})")
    print(f"  Decomposed:")
    print(f"    buy_open={bo:.0f}  sell_open={so:.0f}")
    print(f"    buy_close={bc:.0f}  sell_close={sc:.0f}")
    print(f"  Customer net-opens: {(bo - sc) - (so - bc):+.0f}")

    if lseg_available:
        print("\nReal backfill against LSEG historical: NOT YET IMPLEMENTED.")
        print("Needs rd.get_history() with OPEN_INT field per contract per day.")
    else:
        print("\n(Run during market hours with bridge up to use real data.)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", default=None, help="Single ticker (default: all)")
    p.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: yesterday)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    tickers = [args.ticker] if args.ticker else app_config.TICKERS

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = date.today() - timedelta(days=1)

    run_backfill(target, tickers, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
