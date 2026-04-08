"""
Option RIC (Reuters Instrument Code) builder for LSEG Workspace.

LSEG option RIC format (desktop feed):
    Standard equity options:
        {UNDERLYING}{MonthCode}{DD}{YY}{Strike}.U
        Example: AAPL June 20 2026 $200 Call → "AAPLF2026200.U"

    Index options (SPX):
        {UNDERLYING}{MonthCode}{DD}{YY}{Strike}.U
        SPX Weekly (SPXW) uses the same pattern with SPXW root.

Month codes (calls):  A=Jan  B=Feb  C=Mar  D=Apr  E=May  F=Jun
                      G=Jul  H=Aug  I=Sep  J=Oct  K=Nov  L=Dec
Month codes (puts):   M=Jan  N=Feb  O=Mar  P=Apr  Q=May  R=Jun
                      S=Jul  T=Aug  U=Sep  V=Oct  W=Nov  X=Dec

Strike convention: multiply by 1000, pad with zeros so "6900" = $690.00.
Different feeds use slightly different conventions — the canonical format
varies. This module centralizes that logic so the rest of the code can
call a single function.

We also expose the inverse: `parse_ric()` for decoding chain results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


CALL_MONTH_CODES = "ABCDEFGHIJKL"  # Jan..Dec (strikes < 1000)
PUT_MONTH_CODES = "MNOPQRSTUVWX"
CALL_MONTH_CODES_LOW = "abcdefghijkl"  # Jan..Dec (strikes >= 1000, e.g. SPX)
PUT_MONTH_CODES_LOW = "mnopqrstuvwx"


@dataclass
class ParsedRIC:
    underlying: str
    expiry: date
    option_type: str  # "C" or "P"
    strike: float
    raw: str


def month_code(month: int, option_type: str) -> str:
    """Return the LSEG month code letter for a given month (1-12) and C/P."""
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid month: {month}")
    codes = CALL_MONTH_CODES if option_type.upper() == "C" else PUT_MONTH_CODES
    return codes[month - 1]


def build_option_ric(
    underlying: str,
    expiry: date,
    option_type: str,
    strike: float,
) -> str:
    """
    Build an LSEG option RIC string.

    Examples:
        SPY, date(2026, 4, 18), "C", 550.0
            → "SPYD2026550.U"  (D = April call, 26 = year, 550 = strike)

        SPX, date(2026, 2, 13), "P", 6900.0  (SPXW weekly)
            → "SPXWN26136900.U"

        AAPL, date(2026, 6, 20), "C", 200.0
            → "AAPLF2026200.U"

    Note: the exact pattern that works depends on LSEG feed tier and the
    instrument's root symbol. Real chains should be discovered via
    refinitiv-data's chain resolver, not constructed blindly. This helper
    is primarily for display / fallback / testing.
    """
    if option_type.upper() not in ("C", "P"):
        raise ValueError(f"option_type must be C or P, got {option_type}")

    # Route SPX → SPXW for weekly chains (which is what Skylit shows)
    root = "SPXW" if underlying.upper() == "SPX" else underlying.upper()

    # LSEG convention (from flow-terminal/local_bridge.py):
    # - Strike < 1000: uppercase month code, strike * 100 (integer)
    # - Strike >= 1000: lowercase month code, strike * 10 (integer)
    mi = expiry.month - 1
    opt = option_type.upper()
    if strike >= 1000:
        mc = (CALL_MONTH_CODES_LOW if opt == "C" else PUT_MONTH_CODES_LOW)[mi]
        sn = int(round(strike * 10))
    else:
        mc = (CALL_MONTH_CODES if opt == "C" else PUT_MONTH_CODES)[mi]
        sn = int(round(strike * 100))

    dd = f"{expiry.day:02d}"
    yy = f"{expiry.year % 100:02d}"

    return f"{root}{mc}{dd}{yy}{sn}.U"


def parse_ric(ric: str) -> Optional[ParsedRIC]:
    """
    Parse an LSEG option RIC back into components. Returns None on failure.

    Format: {ROOT}{MonthCode}{DD}{YY}{StrikeNum}.U
    Month code is a single letter. DD is 2 digits, YY is 2 digits.
    Strike is decoded per the 1000-threshold rule.
    """
    if not ric.endswith(".U"):
        return None
    body = ric[:-2]

    # Walk backwards past the digit tail (DD + YY + strike all run together).
    # Stop when we hit a non-digit — that's the month code letter.
    i = len(body) - 1
    while i >= 0 and body[i].isdigit():
        i -= 1
    if i < 0 or i >= len(body) - 5:
        return None  # need at least MC + DD + YY + 1 digit strike
    mc_char = body[i]
    tail = body[i + 1 :]
    dd = tail[:2]
    yy = tail[2:4]
    strike_str = tail[4:]
    if not (dd.isdigit() and yy.isdigit() and strike_str.isdigit()):
        return None
    strike_num = int(strike_str)

    if mc_char in CALL_MONTH_CODES:
        option_type, month = "C", CALL_MONTH_CODES.index(mc_char) + 1
        strike_val = strike_num / 100
    elif mc_char in PUT_MONTH_CODES:
        option_type, month = "P", PUT_MONTH_CODES.index(mc_char) + 1
        strike_val = strike_num / 100
    elif mc_char in CALL_MONTH_CODES_LOW:
        option_type, month = "C", CALL_MONTH_CODES_LOW.index(mc_char) + 1
        strike_val = strike_num / 10
    elif mc_char in PUT_MONTH_CODES_LOW:
        option_type, month = "P", PUT_MONTH_CODES_LOW.index(mc_char) + 1
        strike_val = strike_num / 10
    else:
        return None

    underlying = body[:i]
    if not underlying:
        return None

    try:
        expiry = date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None

    return ParsedRIC(
        underlying=underlying,
        expiry=expiry,
        option_type=option_type,
        strike=strike_val,
        raw=ric,
    )


# Chain RIC patterns — LSEG uses "0#{root}*.U" to enumerate all options
def chain_ric(underlying: str) -> str:
    """Return the RIC that expands to the full option chain for an underlying."""
    root = "SPXW" if underlying.upper() == "SPX" else underlying.upper()
    return f"0#{root}*.U"
