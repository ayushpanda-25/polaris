#!/usr/bin/env python3
"""
Pre-flight check for the LSEG feed.

Verifies:
  1. Can we import refinitiv-data?
  2. Can we open a desktop.workspace session?
  3. Can we fetch a SPY price snapshot? (should work within 10s)
  4. Can we fetch a sample option chain row?

Exit code 0 = all green. Non-zero = something's wrong and the message
tells you what to fix.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check(label: str, fn) -> bool:
    start = time.time()
    try:
        fn()
        dt = time.time() - start
        print(f"  ✅ {label}  ({dt:.1f}s)")
        return True
    except Exception as e:
        dt = time.time() - start
        print(f"  ❌ {label}  ({dt:.1f}s)")
        print(f"     → {type(e).__name__}: {e}")
        return False


def check_import():
    import refinitiv.data  # noqa


def check_session():
    import refinitiv.data as rd
    rd.open_session(name="desktop.workspace")


def check_spot():
    from src.data_feed import LSEGOptionsFeed
    feed = LSEGOptionsFeed(timeout_sec=10)
    feed._ensure_session()
    price = feed._fetch_spot("SPY")
    if not (price and price > 0):
        raise RuntimeError(f"Invalid SPY price: {price}")
    print(f"     SPY spot: ${price:.2f}")


def check_chain_sample():
    """Fetch a tiny chain sample (5 options) to verify options data flows."""
    import refinitiv.data as rd
    from datetime import date, timedelta
    from src.ric_builder import build_option_ric

    # Next business day expiry
    exp = date.today()
    while exp.weekday() >= 5:
        exp += timedelta(days=1)
    if exp == date.today():
        exp += timedelta(days=1)

    # 3 strikes around a rough SPY ATM
    sample_rics = [
        build_option_ric("SPY", exp, "C", 580),
        build_option_ric("SPY", exp, "C", 590),
        build_option_ric("SPY", exp, "P", 580),
    ]
    df = rd.get_data(sample_rics, ["OPEN_INT", "IMPL_VOL", "DELTA"])
    if df is None or df.empty:
        raise RuntimeError("Empty chain sample")
    print(f"     Sample chain rows: {len(df)}")
    # Count non-null OI
    import pandas as pd
    oi_col = df.get("OPEN_INT") if "OPEN_INT" in df.columns else None
    if oi_col is not None:
        non_null = sum(1 for v in oi_col if v is not None and not pd.isna(v))
        print(f"     Non-null OI rows: {non_null}/{len(df)}")


def main():
    print("Polaris bridge health check")
    print("=" * 40)

    ok = True
    ok &= check("import refinitiv.data", check_import)
    if not ok:
        print("\n  → pip3 install refinitiv-data")
        sys.exit(1)

    ok &= check("open desktop.workspace session", check_session)
    if not ok:
        print("\n  → Make sure LSEG Workspace is running and signed in")
        print("  → Check lseg-data.config.json in ~/flow-terminal/")
        sys.exit(2)

    ok &= check("fetch SPY spot price", check_spot)
    ok &= check("fetch sample option chain", check_chain_sample)

    print("=" * 40)
    if ok:
        print("All checks passed. Safe to run: python3 -m src.dashboard --lseg")
        sys.exit(0)
    else:
        print("Some checks failed. Fix the errors above before running live.")
        sys.exit(3)


if __name__ == "__main__":
    main()
