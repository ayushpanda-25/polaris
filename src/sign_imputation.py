"""
Dealer sign imputation — the moat.

Three-layer classifier per the research agent's recommendation:
    Layer 1: Grauer-Schuster-Uhrig-Homburg depth rule (per-trade)
    Layer 2: EOD open-interest reconciliation (per contract per day)
    Layer 3: Customer-side Bayesian prior with whitelist exceptions

The output is a dealer_sign ∈ {-1, +1} applied to OPEN INTEREST
(not volume) for the GEX/VEX surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

try:
    # Relative import when used as a package
    from ..config import OVERWRITING_WHITELIST, STRUCTURED_PRODUCT_STRIKES
except (ImportError, ValueError):
    # Fallback for direct imports / tests
    OVERWRITING_WHITELIST = {"AAPL", "NVDA", "MSFT", "TSLA"}
    STRUCTURED_PRODUCT_STRIKES = {"SPX": [(5800, 6200)]}


class Side(Enum):
    BUY = 1       # customer bought → dealer sold
    SELL = -1     # customer sold → dealer bought
    UNKNOWN = 0


@dataclass
class Trade:
    price: float
    size: float
    prev_different_price: Optional[float] = None


@dataclass
class Quote:
    bid: float
    ask: float
    bid_size: float = 0
    ask_size: float = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


# ----------------------------------------------------------------------
# Layer 1 — Depth-aware trade classification
# ----------------------------------------------------------------------

def classify_trade(trade: Trade, quote: Quote) -> Side:
    """
    Grauer-Schuster-Uhrig-Homburg depth rule.

    Returns the CUSTOMER side (dealer is the opposite).
    """
    # Rule 1: Quote rule
    if trade.price >= quote.ask:
        return Side.BUY
    if trade.price <= quote.bid:
        return Side.SELL

    # Rule 2: Reverse tick for inside-quote prints
    if trade.prev_different_price is not None:
        if trade.price > trade.prev_different_price:
            return Side.BUY
        if trade.price < trade.prev_different_price:
            return Side.SELL

    # Rule 3: Depth-aware fallback for outside-quote prints
    if trade.size > max(quote.ask_size, quote.bid_size):
        # Aggressive large trade — lean toward the mid side it hit
        if trade.price > quote.mid:
            return Side.BUY
        if trade.price < quote.mid:
            return Side.SELL

    # Rule 4: Mid-split fallback
    if trade.price > quote.mid:
        return Side.BUY
    if trade.price < quote.mid:
        return Side.SELL

    return Side.UNKNOWN


def classify_trades_batch(trades: list[Trade], quotes: list[Quote]) -> list[Side]:
    """Vectorizable form (currently just loops — fine at our scale)."""
    return [classify_trade(t, q) for t, q in zip(trades, quotes)]


# ----------------------------------------------------------------------
# Layer 2 — EOD Open-Interest reconciliation
# ----------------------------------------------------------------------

@dataclass
class ContractDay:
    """One contract, one day — for OI reconciliation."""
    ticker: str
    strike: float
    expiry: str
    option_type: str
    buy_volume: float      # from Layer 1
    sell_volume: float     # from Layer 1
    oi_start: float
    oi_end: float

    @property
    def oi_delta(self) -> float:
        return self.oi_end - self.oi_start


def reconcile_oi(day: ContractDay) -> tuple[float, float, float, float]:
    """
    Solve: (buy_open - sell_open) - (buy_close - sell_close) ≈ ΔOI
    subject to:
        buy_open + buy_close = buy_volume
        sell_open + sell_close = sell_volume
        all values >= 0

    Returns (buy_open, sell_open, buy_close, sell_close).
    Uses a closed-form heuristic because the system is underdetermined.
    """
    bv, sv = day.buy_volume, day.sell_volume
    delta = day.oi_delta

    # Prior: customers are net openers (~70% opens, 30% closes)
    # Adjust that ratio to match ΔOI.
    prior_open_ratio = 0.7
    buy_open = bv * prior_open_ratio
    buy_close = bv * (1 - prior_open_ratio)
    sell_open = sv * prior_open_ratio
    sell_close = sv * (1 - prior_open_ratio)

    # Adjust to satisfy ΔOI = (buy_open + sell_open) - (buy_close + sell_close)
    # Reference: positive OI delta means more opens than closes.
    computed_delta = (buy_open + sell_open) - (buy_close + sell_close)
    error = delta - computed_delta
    total_vol = bv + sv
    if total_vol > 0:
        shift = error / (2 * total_vol)
        shift = np.clip(shift, -0.3, 0.3)  # keep within reasonable bounds
        buy_open = np.clip(bv * (prior_open_ratio + shift), 0, bv)
        buy_close = bv - buy_open
        sell_open = np.clip(sv * (prior_open_ratio + shift), 0, sv)
        sell_close = sv - sell_open

    return buy_open, sell_open, buy_close, sell_close


# ----------------------------------------------------------------------
# Layer 3 — Customer-side Bayesian prior
# ----------------------------------------------------------------------

INDEX_TICKERS_FOR_0DTE_FLIP = {"SPY", "SPX", "QQQ"}


def customer_prior_sign(
    ticker: str,
    strike: float,
    option_type: str,
    days_to_expiry: Optional[int] = None,
    spot: Optional[float] = None,
) -> int:
    """
    Return the dealer's sign on OPEN INTEREST under the customer-side prior.

    Default: customers long calls AND long puts → dealers short both → -1

    Exceptions:
        1. Overwriting whitelist (AAPL/NVDA/MSFT/TSLA — heavy call-selling flow):
           customers short calls → dealers long calls → +1 for calls
        2. Structured-product bands (SPX JPM collar):
           flip put/call sign inside the band
        3. 0DTE indices (SPY/SPX/QQQ, days_to_expiry ≤ 1):
           Empirical finding from Skylit 2026-04-07 screenshots: King Node
           at 0DTE is POSITIVE (bright yellow) — dealers LONG gamma at pin.
           Consistent with retail/overwriting flow dominating 0DTE and
           dealers being the passive long-gamma counterparty.
           → Flip call-side dealer sign to +1 for 0DTE indices.
    """
    dealer_sign = -1  # baseline

    if option_type == "C" and ticker in OVERWRITING_WHITELIST:
        dealer_sign = +1

    # 0DTE index flip (derived from Skylit screenshots)
    if (
        ticker in INDEX_TICKERS_FOR_0DTE_FLIP
        and days_to_expiry is not None
        and days_to_expiry <= 1
        and option_type == "C"
    ):
        dealer_sign = +1

    bands = STRUCTURED_PRODUCT_STRIKES.get(ticker, [])
    for lo, hi in bands:
        if lo <= strike <= hi:
            if option_type == "C":
                dealer_sign = +1  # dealer long calls (collar upper leg)
            elif option_type == "P":
                dealer_sign = -1  # dealer short puts (collar lower leg)
            break

    return dealer_sign


def blended_dealer_sign(
    ticker: str,
    strike: float,
    option_type: str,
    oi_reconciled_net: Optional[float] = None,
    days_to_expiry: Optional[int] = None,
    spot: Optional[float] = None,
) -> int:
    """
    Combine the prior (Layer 3) with the OI reconciliation result (Layer 2).

    If we have Layer 2 output (net customer opening position for the day),
    use its sign. Otherwise fall back to the prior.
    """
    if oi_reconciled_net is not None and abs(oi_reconciled_net) > 0:
        return -1 if oi_reconciled_net > 0 else +1
    return customer_prior_sign(
        ticker, strike, option_type,
        days_to_expiry=days_to_expiry,
        spot=spot,
    )
