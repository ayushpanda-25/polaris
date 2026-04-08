"""Unit tests for sign imputation (all three layers)."""
from src.sign_imputation import (
    ContractDay,
    Quote,
    Side,
    Trade,
    blended_dealer_sign,
    classify_trade,
    customer_prior_sign,
    reconcile_oi,
)


# ---------------- Layer 1 ----------------

def test_trade_at_ask_is_buy():
    q = Quote(bid=1.00, ask=1.10, bid_size=10, ask_size=10)
    t = Trade(price=1.10, size=5)
    assert classify_trade(t, q) == Side.BUY


def test_trade_at_bid_is_sell():
    q = Quote(bid=1.00, ask=1.10)
    t = Trade(price=1.00, size=5)
    assert classify_trade(t, q) == Side.SELL


def test_inside_quote_uses_tick_rule_up():
    q = Quote(bid=1.00, ask=1.10)
    t = Trade(price=1.05, size=5, prev_different_price=1.03)
    assert classify_trade(t, q) == Side.BUY


def test_inside_quote_uses_tick_rule_down():
    q = Quote(bid=1.00, ask=1.10)
    t = Trade(price=1.05, size=5, prev_different_price=1.07)
    assert classify_trade(t, q) == Side.SELL


def test_outside_ask_large_size_still_buy():
    q = Quote(bid=1.00, ask=1.10, bid_size=10, ask_size=10)
    t = Trade(price=1.20, size=50)  # sweeping through the book
    assert classify_trade(t, q) == Side.BUY


# ---------------- Layer 2 ----------------

def test_oi_reconciliation_balances():
    day = ContractDay(
        ticker="SPY",
        strike=500.0,
        expiry="2026-04-18",
        option_type="C",
        buy_volume=1000,
        sell_volume=800,
        oi_start=5000,
        oi_end=5200,  # +200 ΔOI
    )
    bo, so, bc, sc = reconcile_oi(day)
    # All non-negative
    assert bo >= 0 and so >= 0 and bc >= 0 and sc >= 0
    # Totals preserved
    assert abs((bo + bc) - 1000) < 1e-6
    assert abs((so + sc) - 800) < 1e-6


def test_oi_reconciliation_zero_delta():
    """Zero OI change → should be pure close/open balance (no strong bias)."""
    day = ContractDay(
        ticker="SPY", strike=500, expiry="2026-04-18", option_type="C",
        buy_volume=500, sell_volume=500, oi_start=1000, oi_end=1000,
    )
    bo, so, bc, sc = reconcile_oi(day)
    assert abs((bo + bc) - 500) < 1e-6
    assert abs((so + sc) - 500) < 1e-6


# ---------------- Layer 3 ----------------

def test_default_prior_is_dealer_short():
    assert customer_prior_sign("SPY", 500.0, "C") == -1
    assert customer_prior_sign("SPY", 500.0, "P") == -1


def test_overwriting_whitelist_flips_calls():
    assert customer_prior_sign("AAPL", 200.0, "C") == +1
    # Puts unchanged
    assert customer_prior_sign("AAPL", 200.0, "P") == -1


def test_structured_product_band_flips_spx():
    # Inside the JPM collar band
    assert customer_prior_sign("SPX", 6000.0, "C") == +1
    assert customer_prior_sign("SPX", 6000.0, "P") == -1
    # Outside the band - default
    assert customer_prior_sign("SPX", 7000.0, "C") == -1


def test_blended_uses_oi_when_available():
    # Positive net opens → customer long → dealer short
    assert blended_dealer_sign("SPY", 500, "C", oi_reconciled_net=100) == -1
    # Negative net opens → customer short → dealer long
    assert blended_dealer_sign("SPY", 500, "C", oi_reconciled_net=-100) == +1
    # No OI → falls back to prior
    assert blended_dealer_sign("SPY", 500, "C", oi_reconciled_net=None) == -1
