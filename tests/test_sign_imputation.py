"""Unit tests for dealer sign imputation."""
from src.sign_imputation import blended_dealer_sign


def test_calls_positive():
    """Standard: dealer short call → long gamma → positive GEX."""
    assert blended_dealer_sign("SPY", 500.0, "C") == +1
    assert blended_dealer_sign("SPX", 6800.0, "C") == +1
    assert blended_dealer_sign("AAPL", 200.0, "C") == +1
    assert blended_dealer_sign("QQQ", 600.0, "C") == +1


def test_puts_negative():
    """Standard: dealer short put → short gamma → negative GEX."""
    assert blended_dealer_sign("SPY", 500.0, "P") == -1
    assert blended_dealer_sign("SPX", 6800.0, "P") == -1
    assert blended_dealer_sign("AAPL", 200.0, "P") == -1
    assert blended_dealer_sign("QQQ", 600.0, "P") == -1


def test_case_insensitive():
    """Both 'C'/'c' and 'P'/'p' should work."""
    assert blended_dealer_sign("SPY", 500.0, "c") == +1
    assert blended_dealer_sign("SPY", 500.0, "p") == -1


def test_optional_params_ignored():
    """Extra params (oi_reconciled_net, days_to_expiry, spot) don't change the sign."""
    assert blended_dealer_sign("SPY", 500.0, "C", oi_reconciled_net=100) == +1
    assert blended_dealer_sign("SPY", 500.0, "P", days_to_expiry=0) == -1
    assert blended_dealer_sign("SPY", 500.0, "C", spot=500.0) == +1
