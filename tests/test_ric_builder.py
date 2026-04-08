"""Tests for RIC builder / parser."""
from datetime import date

import pytest

from src.ric_builder import (
    CALL_MONTH_CODES,
    PUT_MONTH_CODES,
    build_option_ric,
    chain_ric,
    month_code,
    parse_ric,
)


def test_month_code_calls():
    assert month_code(1, "C") == "A"
    assert month_code(6, "C") == "F"
    assert month_code(12, "C") == "L"


def test_month_code_puts():
    assert month_code(1, "P") == "M"
    assert month_code(12, "P") == "X"


def test_invalid_month_raises():
    with pytest.raises(ValueError):
        month_code(13, "C")
    with pytest.raises(ValueError):
        month_code(0, "C")


def test_build_ric_spy_call():
    # SPY Apr 18 2026 $550 Call:
    #   strike < 1000 → uppercase D (April call), strike * 100
    ric = build_option_ric("SPY", date(2026, 4, 18), "C", 550)
    assert ric == "SPYD182655000.U"


def test_build_ric_spx_routes_to_spxw_and_lowercase():
    # SPX Feb 13 2026 $6900 Put:
    #   routes to SPXW, strike >= 1000 → lowercase n (Feb put), strike * 10
    ric = build_option_ric("SPX", date(2026, 2, 13), "P", 6900)
    assert ric == "SPXWn132669000.U"


def test_roundtrip_parse():
    ric = build_option_ric("AAPL", date(2026, 6, 20), "C", 200)
    parsed = parse_ric(ric)
    assert parsed is not None
    assert parsed.underlying == "AAPL"
    assert parsed.expiry == date(2026, 6, 20)
    assert parsed.option_type == "C"
    assert parsed.strike == 200.0


def test_parse_invalid_ric_returns_none():
    assert parse_ric("GARBAGE") is None
    assert parse_ric("SPY.U") is None  # too short


def test_chain_ric_format():
    assert chain_ric("SPY") == "0#SPY*.U"
    assert chain_ric("SPX") == "0#SPXW*.U"
    assert chain_ric("aapl") == "0#AAPL*.U"
